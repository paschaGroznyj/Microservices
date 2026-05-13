import asyncio
import logging
import time
from enum import Enum
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from langchain.schema import SystemMessage

from .core.agent_provider import get_model
from .core.botocore_client import OBSWorker
from .core.config import (
    ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH,
    METADATA_FILE, PORT_GATEWAY_PORT
)
from .digest_email_handler import DigestEmailHandler
from .monitor import StorageMonitor
from .summary import SummaryGenerator
from .summary_period import agent_composite_digest_tool
from .update_metadata import MetadataUpdater
from .vector_db import VectorDBManager
from .download_pdf_and_make import download_and_parse_pdf, generate_review
from .favorites_digest import build_user_favorites_review_digest_from_query
from pydantic import BaseModel, Field

GATEWAY_URL = f"http://api_gateway:{PORT_GATEWAY_PORT}"


# ================================================================
# ASYNC TOOLS — каждый инструмент агента как отдельная функция
# ================================================================

async def tool_delete_file(
    vector_db: VectorDBManager,
    files: List[str],
) -> Dict[str, Any]:
    """Удаление файлов из векторной БД."""
    if not files:
        return {"status": "error", "message": "Нет файлов для удаления"}

    await asyncio.to_thread(vector_db.delete_documents, files)
    return {"status": "success", "message": f"Я удалил {len(files)} файлов"}


async def tool_analyze_folder(
    monitor: StorageMonitor,
    articles_path: Path,
) -> Dict[str, Any]:
    """Анализ директории со статьями."""
    changes = await asyncio.to_thread(monitor.get_changes)
    files_list = list(articles_path.glob("*.md"))
    return {
        "status": "success",
        "new_files": len(changes["new"]),
        "modified_files": len(changes["modified"]),
        "total_files": len(files_list),
        "files": [f.name for f in files_list[:5]],
        "message": (
            f"📂 **Анализ директории завершен**\n\n"
            f"📊 **Статистика изменений:**\n"
            f"• 🆕 Новых файлов: `{len(changes['new'])}`\n"
            f"• ✏️ Изменено файлов: `{len(changes['modified'])}`\n"
            f"• 📁 Всего файлов: `{len(files_list)}`"
        ),
    }


async def tool_search_db(
    vector_db: VectorDBManager,
    query: str,
    clean_markdown_fn,
) -> Dict[str, Any]:
    """Семантический поиск в векторной БД."""
    results = await asyncio.to_thread(vector_db.similarity_search, query, 5)

    if not results:
        return {
            "status": "warning",
            "count": 0,
            "preview": [],
            "message": (
                f"🔍 **По запросу не найдено совпадений**\n\n"
                f"Запрос: `\"{query}\"`\n\n"
                f"⚠️ **Вероятная причина:** Векторная база знаний пуста или еще не создана.\n"
                f"Без векторного индекса семантический поиск работать не может.\n\n"
                f"💡 **Рекомендация:**\n"
                f"Сначала выполните команду для построения индекса:\n"
                f"`Построй векторную базу по всем статьям`"
            ),
        }

    formatted_results = []
    for i, r in enumerate(results, 1):
        clean_content = clean_markdown_fn(r["content"].strip())
        snippet = clean_content[:512].rsplit(" ", 1)[0] + "..." if len(clean_content) > 512 else clean_content
        formatted_results.append(
            f"**Результат {i}** (релевантность: {r['score']:.3f})\n\n{snippet}"
        )

    return {
        "status": "success",
        "count": len(results),
        "preview": formatted_results,
        "message": f"Найдено {len(results)} релевантных фрагментов по запросу:\n\n"
                   + "\n\n---\n\n".join(formatted_results),
    }


async def tool_rebuild_categories(
    obs_loader: OBSWorker,
    name_meta_dir: Path,
) -> Dict[str, Any]:
    """Переопределение категорий в метаданных."""
    await obs_loader.download_metadata()

    json_file = name_meta_dir / "metadata.json"
    updater = MetadataUpdater(metadata_file=json_file, max_retries=1, retry_delay=30)
    message = await asyncio.to_thread(updater.run)
    await obs_loader.put_json()

    return {"status": "success", "message": message}


async def tool_general_question(agent_answer: str) -> Dict[str, Any]:
    """Форматирование ответа на общий вопрос."""
    instruction_link = (
        "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/instructions/agent_commands.html"
    )
    text = re.sub(r"\s*\[.*?\]\s*", "", agent_answer)
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        text = text[: match.start()] + text[match.end():]
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"\n\s*\n", "\n", text)
    text = re.sub(r"general_question", "", text)
    final_answer = text.strip() or "Чистили-чистили, но не дочистили. Это был точно общий вопрос"
    return {
        "status": "success",
        "message": f"{final_answer}\n\n📚 [Прикладываю ссылку на инструкцию]({instruction_link})",
    }


async def tool_send_digest_email(
    email_handler: DigestEmailHandler,
    user_input: str,
    gigachat,
    obs_loader: OBSWorker,
    name_meta_dir: Path,
    digest_path: Path,
) -> Dict[str, Any]:
    """Формирование и отправка дайджеста на email."""
    return await asyncio.to_thread(
        email_handler.execute,
        user_input=user_input,
        gigachat=gigachat,
        obs_loader=obs_loader,
        name_meta_dir=name_meta_dir,
        digest_path=digest_path,
    )


async def tool_summarize_all(
    articles_path: Path,
    name_meta_dir: Path,
    digest_path: Path,
    summarizer: SummaryGenerator,
    obs_loader: OBSWorker,
) -> Dict[str, Any]:
    """Генерация ревью для всех статей без существующего ревью."""
    reviews_root = digest_path
    json_file = name_meta_dir / "metadata.json"

    # Читаем json в потоке, чтобы не блокировать event loop
    def _load_metadata():
        with open(json_file, "r", encoding="utf-8") as f:
            return json.load(f)

    articles_data = await asyncio.to_thread(_load_metadata)

    articles_map: Dict[str, Dict[str, str]] = {
        a["file_article"]: {
            "theme": a.get("category", "категория не найдена"),
            "pdf_url": a.get("pdf_url", "Не найден"),
        }
        for a in articles_data
        if a.get("category", "Не релевантна PM") != "Не релевантна PM"
    }

    if not reviews_root.exists():
        os.makedirs(reviews_root, exist_ok=True)

    md_articles = [n.name for n in articles_path.glob("*.md") if n.name in articles_map]
    md_reviews = [n.name for n in reviews_root.rglob("*.md")]
    need_reviews = list(set(md_articles) - set(md_reviews))

    for filename in need_reviews:
        article_path = articles_path / filename
        meta = articles_map.get(filename, {})
        content = await asyncio.to_thread(article_path.read_text, encoding="utf-8")
        await asyncio.to_thread(
            summarizer.generate_summary,
            content=content,
            filename=filename,
            theme=meta.get("theme", "категория не найдена"),
            pdf_url=meta.get("pdf_url", "Не найден"),
        )

    await obs_loader.upload_digests()

    articles_count = len(md_articles)
    reviews_count = len(md_reviews)
    diff = articles_count - reviews_count
    obs_path = "obs://cds-iad/reviews/"

    if diff == 0:
        message = (
            f"🎉 **Полная синхронизация!**\n\n"
            f"🗄 **Статей:** `{articles_count}`\n"
            f"✅ **Ревью:** `{reviews_count}`\n\n"
            f"✨ Все актуальные статьи уже имеют ревью.\n"
            f"📂 Хранилище актуально: `{obs_path}`"
        )
        status = "synced"
    else:
        message = (
            f"⚠️ **Обнаружено несоответствие!**\n\n"
            f"🗄 **Статей в хранилище:** `{articles_count}`\n"
            f"✅ **Найдено ревью:** `{reviews_count}`\n\n"
            f"🧮 **Разница:** `{abs(diff)}` лишних ревью.\n\n"
            f"💡 **Рекомендация:** Проверьте папку `{obs_path}` на наличие orphaned файлов."
        )
        status = "warning"

    return {
        "status": status,
        "message": message,
        "stats": {"articles": articles_count, "reviews": reviews_count, "difference": diff},
    }


async def tool_summarize_new(
    auto_processor,
    obs_loader: OBSWorker,
) -> Dict[str, Any]:
    """Обработка только новых статей."""
    results = await asyncio.to_thread(auto_processor.process_new_articles)
    await obs_loader.upload_digests()
    await obs_loader.upload_vectorstore()
    return {
        "status": "success",
        "message": "Провел саммари новых статей",
        "results": results,
    }


async def tool_summarize_filt(
    gigachat,
    user_input: str,
    name_meta_dir: Path,
    digest_path: Path,
    obs_loader: OBSWorker,
) -> Dict[str, Any]:
    """Дайджест с фильтрацией по дате или категории."""
    json_file = name_meta_dir / "metadata.json"
    results = await asyncio.to_thread(
        agent_composite_digest_tool,
        llm_client=gigachat,
        query=user_input,
        json_path=json_file,
        md_folder=digest_path,
    )

    if results["found_articles"] == 0:
        message = (
            "❌ **Нет данных для формирования дайджеста**\n\n"
            "По вашему запросу (период или категория) материалов не обнаружено.\n"
            "Пожалуйста, выберите другие параметры."
        )
    else:
        base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
        full_link = f"{base_url}{results['filename']}"
        message = (
            f"🎉 **Готово! Дайджест сформирован.**\n\n"
            f"• Имя файла: `{results['filename']}`\n\n"
            f"🔗 **Ссылка на скачивание:** [Скачать]({full_link})"
        )

    await obs_loader.upload_single_digest_cond(results["digest_file"], results["filename"])
    return {"status": "success", "message": message, "results": results}


async def tool_summarize_file(
    filename: str,
    articles_path: Path,
    name_meta_dir: Path,
    summarizer: SummaryGenerator,
    obs_loader: OBSWorker,
) -> Dict[str, Any]:
    """Генерация ревью для одного файла."""
    await obs_loader.ensure_article_downloaded(filename)
    filepath = articles_path / filename

    def _load():
        with open(name_meta_dir / "metadata.json", "r", encoding="utf-8") as f:
            return json.load(f)

    articles_data = await asyncio.to_thread(_load)
    articles_map = {
        a["file_article"]: {
            "theme": a.get("category", "категория не найдена"),
            "pdf_url": a.get("pdf_url", "Не найден"),
        }
        for a in articles_data
    }
    meta = articles_map.get(filename, {})
    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/reviews"
    obs_path = "obs://cds-iad/PM/reviews/"

    if not filepath.exists():
        return {"status": "error", "message": "❌ Не получилось найти файл"}

    content = await asyncio.to_thread(filepath.read_text, encoding="utf-8")
    digest_result = await asyncio.to_thread(
        summarizer.generate_summary,
        content=content,
        filename=filename,
        theme=meta.get("theme", "категория не найдена"),
        pdf_url=meta.get("pdf_url", "Не найден"),
    )

    if digest_result["status"] in ("created", "exists"):
        await obs_loader.upload_single_digest(digest_result["digest_file"], filename)
        full_link = f"{base_url}/{filename}"

        if digest_result["status"] == "created":
            message = (
                f"✨ **Одиночное ревью успешно сформировано!**\n\n"
                f"📄 **Файл:** `{filename}`\n"
                f"📥 **Скачать:** [Нажмите здесь]({full_link})\n"
                f"_Или скопируйте ссылку:_ `{full_link}`"
            )
        else:
            message = (
                f"ℹ️ **Ревью уже существует**\n\n"
                f"Файл `{filename}` был создан ранее.\n"
                f"📂 **Путь в хранилище:** `{obs_path}`\n"
                f"🔗 **Ссылка на скачивание:** `{full_link}`"
            )
        return {"status": digest_result["status"], "message": message}

    return {"status": "error", "message": "❌ Не получилось найти файл"}


async def tool_make_reviews_basket(db, user_id, llm_client):
    """
    Основная функция-инструмент.
    1. Находит избранные статьи без ревью.
    2. Параллельно скачивает PDF, генерирует ревью и сохраняет в БД.
    """
    rows = await db.get_reviews(user_id)
    articles_data = [dict(row) for row in rows]

    articles_to_process = [art for art in articles_data if not art.get('review_text')]
    if len(articles_data) == 0:
        message = (
            "📂 **Ваша корзина пока пуста**\n\n"
            "Чтобы я мог сделать ревью, нужно сначала добавить статьи в избранное.\n"
            "Загляните в раздел *Поиск статей*, выберите что-нибудь интересное и нажмите на звездочку ⭐️😉"
        )
        return {"status": "success", "message": message}

    if not articles_to_process:
        message = (
            f"✨ **Отличные новости! Всё уже готово**\n\n"
            f"Вы уже сформировали ревью для всех {len(articles_data)} статей в вашей корзине.\n\n"
            f"Можем перейти к следующему шагу? Например, собрать по ним общий дайджест 🤠"
        )
        return {"status": "success", "message": message}

    print(f"Начинаем обработку {len(articles_to_process)} новых статей...")

    tasks = []
    for article in articles_to_process:
        task = process_single_article_safe(
            article=article,
            db=db,
            user_id=user_id,
            llm=llm_client
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results if r is True)
    total_count = len(articles_to_process)

    results_data = {
        "processed": success_count,
        "total": total_count
    }

    # Формируем сообщение для пользователя
    if success_count == 0:
        message = (
            "ℹ️ **Нечего анализировать**\n\n"
            "В вашей корзине избранного нет новых статей без ревью, "
            "либо возникли ошибки при обработке PDF.\n"
            "Попробуйте добавить новые статьи в избранное."
        )
    else:
        message = (
            f"🎉 **Готово! Ревью сформированы.**\n\n"
            f"• Обработано статей: `{success_count}` из `{total_count}`\n\n"
            f"Теперь вы можете сформировать дайджест по своей корзине с избранным\n"
        )

    return {
        "status": "success",
        "message": message,
        "results": results_data
    }

async def tool_digest_basket(db,
    user_id: str,
    user_query: str
) -> Dict[str, Any]:
    """Генерация дайджеста."""
    
    rows = await db.get_reviews(user_id)
    articles_data = [dict(row) for row in rows]

    res = await build_user_favorites_review_digest_from_query(
        username=user_id,
        favorites_with_reviews=articles_data,
        user_query=user_query,
        save_to_file=False,
    )
    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
    full_link = f"{base_url}{res['file_name']}"
    if res['digest_markdown']:
        message = (
            f"🎉 **Готово! Дайджест сформирован.**\n\n"
            f"📖 **Посмотреть:** [Нажмите здесь]({full_link})\n"
        )
        return {"status": 'created', "message": message}

    return {"status": "error", "message": "❌ Не получилось сделать дайджест"}

async def process_single_article_safe(article: dict, db, user_id: int, llm):
    """
    Безопасная обертка для обработки одной статьи.
    """
    article_id = article['article_id']
    pdf_url = article['pdf_url']
    title = article['title']

    try:
        text_content = await download_and_parse_pdf(pdf_url)

        if not text_content or len(text_content) < 50:
            raise ValueError("Недостаточно текста для анализа")

        review_text = await generate_review(llm, text_content, title, pdf_url)
        logging.info(f"ТЕКСТ РЕВЬЮ {review_text[:100]}")
        await db.save_review(user_id, article_id, review_text)

        return True

    except Exception as e:
        print(f"❌ Failed to process article {article_id} ({title}): {e}")
        return False


# Pydantic модели для structured output
class ActionType(str, Enum):
    SEARCH_DB = "search_db"
    SUMMARIZE_FILE = "summarize_file"
    SUMMARIZE_ALL = "summarize_all"
    SUMMARIZE_NEW = "summarize_new"
    # REBUILD_ALL = "rebuild_all"
    ANALYZE_FOLDER = "analyze_folder"
    DELETE_FILE = "delete_file"
    SUMMARIZE_FILTER = 'summarize_filt'
    REBUILD_CATEGORIES = 'rebuild_categories'
    REBUILD_QUESTION = 'general_question'
    SEND_DIGEST_EMAIL = 'send_digest_email'
    SUMMARIZE_BASKET = 'summarize_basket'
    DIGEST_BASKET = 'digest_basket'


class AgentAction(BaseModel):
    action: ActionType = Field(..., description="Действие агента")
    files: Optional[List[str]] = Field(default=None, description="Файлы")
    query: Optional[str] = Field(default=None, description="Запрос поиска")
    reasoning: str = Field(..., description="Обоснование действия", alias='reasoning')


class PydanticOutputParser:
    def __init__(self, pydantic_object):
        self._pydantic_object = pydantic_object

    def parse(self, text: str) -> AgentAction:
        """НАЙДЕТ JSON в любом месте текста (даже после схемы + объяснений)"""
        try:
            # 1. Убираем ** markdown
            text = re.sub(r'\*\*(.*)\*\*', r'\1', text)
            text = re.sub(r'``````', '', text)
            text = re.sub(r'``````', '', text, flags=re.DOTALL)

            # 2. Ищем ВСЕ JSON блоки
            json_blocks = re.findall(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)

            if not json_blocks:
                raise ValueError("JSON блоки не найдены")

            # 3. Берем ПОСЛЕДНИЙ JSON (тот, что с реальным ответом)
            last_json = json_blocks[-1]

            # print(f"Найден JSON: {last_json[:100]}...")

            # 4. Парсим
            data = json.loads(last_json)
            if 'action' in data:
                # Приводим к правильным значениям enum
                action_map = {
                    'search_db': 'search_db',
                    'summarize_file': 'summarize_file',
                    'summarize_all': 'summarize_all',
                    'summarize_new': 'summarize_new',
                    # 'rebuild_all': 'rebuild_all',
                    'analyze_folder': 'analyze_folder',
                    'delete_file': 'delete_file',
                    'summarize_filt': 'summarize_filt',
                    # Fallback для опечаток
                    'search': 'search_db',
                    'summary': 'summarize_file',
                    'summarize': 'summarize_file',
                    # 'rebuild': 'rebuild_all',
                    # 'build': 'rebuild_all',
                    'analyze': 'analyze_folder',
                    'summarize_filter': 'summarize_filt',
                    'rebuild_categories': 'rebuild_categories',
                    'general_question': 'general_question',
                    'send_digest_email': 'send_digest_email',
                    # Fallback aliases
                    'send_email': 'send_digest_email',
                    'email': 'send_digest_email',
                    'summarize_basket': 'summarize_basket',
                    'digest_basket': 'digest_basket'
                }
                data['action'] = action_map.get(data['action'].lower().strip(), data['action'])
            return self._pydantic_object.model_validate(data)

        except json.JSONDecodeError as e:
            raise Exception(f"JSON ошибка: {e}\nТекст: {text[:200]}...")
        except Exception as e:
            raise self._fallback_parse(text)

    def _fallback_parse(self, text: str) -> AgentAction:
        """РЕЗЕРВНЫЙ ПАРСЕР - если Pydantic упал"""
        print("Используем FALLBACK парсер")

        text_lower = text.lower()
        action_map = {
            'search_db': ['search', 'поиск', 'найди', 'найти'],
            'summarize_file': ['summarize_file', 'summarize', 'суммаризуй', 'дайджест', 'кратко'],
            'summarize_all': ['summarize_all', 'всех', 'все статьи', 'дайджест всех'],
            'summarize_new': ['summarize_new', 'новые', 'все новые'],
            # 'rebuild_all': ['rebuild_all', 'построй', 'перестрой', 'обнови', 'создай базу'],
            'analyze_folder': ['analyze_folder', 'проверь папку', 'анализ', 'анализируй'],
            'delete_file': ['delete_file', 'удали', 'все новые'],
            'summarize_filt': ['по категории', 'за период'],
            'rebuild_categories': ['переопредели', 'категории', 'метаданные'],
            'general_question': ['упай', 'вставай'],
            'send_digest_email': ['отправь на почту', 'отправь дайджест', 'send email', 'отправь на email', 'email', 'почта', 'mail'],
            'summarize_basket': ['корзнин', 'избран'],
            'digest_basket': ['корзина', 'избран', 'дайджест']
        }

        # Определяем action по ключевым словам
        for action, keywords in action_map.items():
            if any(keyword in text_lower for keyword in keywords):
                return AgentAction(
                    action=action,
                    files=[],
                    query="",
                    reasoning=f"Fallback: найдены ключевые слова для {action}"
                )

        # Последний резерв - поиск по запросу
        return AgentAction(
            action="analyze_folder",
            files=[],
            query="",
            reasoning="Fallback: анализ по умолчанию"
        )

    @property
    def format_instructions(self) -> str:
        """ПРОСТАЯ инструкция БЕЗ Pydantic схемы"""
        return """В КОНЦЕ ОТВЕТА добавь ТОЧНО такой JSON:

**{
  "action": "search_db|summarize_file|summarize_all|summarize_new|analyze_folder|delete_file|summarize_filt|rebuild_categories|general_question|send_digest_email|summarize_basket|digest_basket",
  "query": "поисковый запрос", 
  "files": ["file1.md"],
  "reasoning": "почему выбрал этот инструмент"
}**

НЕ добавляй схемы или примеры JSON перед этим!"""

# ========== АВТОМАТИЧЕСКИЙ ОБРАБОТЧИК НОВЫХ СТАТЕЙ ==========
class AutoProcessor:
    """Автоматическая обработка новых статей после парсера"""

    def __init__(self, articles_path: Path, monitor: StorageMonitor,
                 vector_db: VectorDBManager, summarizer: SummaryGenerator,
                 dedup_manager=None):
        self.articles_path = articles_path
        self.monitor = monitor
        self.vector_db = vector_db
        self.summarizer = summarizer
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)

    def process_new_articles(self) -> Dict[str, Any]:
        """ГЛАВНАЯ ФУНКЦИЯ: Авто-обработка новых статей"""

        # ШАГ 1: Находим новые статьи
        changes = self.monitor.get_changes()
        new_articles = changes.get('new', [])
        deleted_files = changes.get('deleted', [])

        print("Удаленные файлы=============", deleted_files)

        results = {"new": [], "deleted": []}

        # НОВЫЕ ФАЙЛЫ → Дайджесты
        if new_articles:
            print(f"Новых статей: {len(new_articles)}")
            for filename in new_articles:
                filepath = self.articles_path / filename
                # ДУБЛИРОВАНИЕ КОДА
                json_file = self.name_meta_dir / "metadata.json"  # Файл до метаданных

                with open(json_file, 'r',
                          encoding='utf-8') as f:  # Считаем метаданные один раз, чтобы связать с множеством ревью
                    articles_data = json.load(f)

                articles_map: Dict[str, Dict[str, str]] = {
                    article['file_article']: {
                        "theme": article.get('category', 'категория не найдена'),
                        "pdf_url": article.get('pdf_url', 'Не найден')
                    }
                    for article in articles_data
                }

                meta = articles_map.get(filename, {})
                theme = meta.get("theme", "категория не найдена")
                pdf_url = meta.get("pdf_url", "Не найден")

                if filepath.exists():
                    content = filepath.read_text(encoding='utf-8')
                    digest_result = self.summarizer.generate_summary(
                        content=content,
                        filename=filename,
                        theme=theme,
                        pdf_url=pdf_url
                    )
                    # ДУБЛИРОВАНИЕ КОДА
                    results["new"].append({
                        "file": filename,
                        "status": digest_result["status"],
                        "digest_file": digest_result.get('digest_file', '')
                    })

        # УДАЛЕННЫЕ ФАЙЛЫ → Удаление из БД
        if deleted_files:
            print(f"Удаленных: {len(deleted_files)}")
            self.vector_db.delete_documents(deleted_files)
            results["deleted"] = deleted_files

        # ПЕРЕСТРОЙКА БД (если были изменения)
        if new_articles or deleted_files:
            print("Перестраиваем БД...")
            vector_count = self.vector_db.rebuild_index()
            results["vectors_built"] = vector_count

        return results

# ================================================================
# KnowledgeAgent
# ================================================================

class KnowledgeAgent:
    def __init__(self, database, skip_startup_autoprocess: bool = False):
        base_dir = Path(__file__).resolve().parent
        self.db = database
        self.articles_path = base_dir / Path(STORAGE_PATH) / ARTICLES_FOLDER
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)
        self.digest_path = base_dir / "digests"
        self.db = None

        self.monitor = StorageMonitor(STORAGE_PATH, METADATA_FILE)
        self.vector_db = VectorDBManager(str(self.digest_path), VECTOR_DB_PATH)
        self.summarizer = SummaryGenerator()
        self.auto_processor = AutoProcessor(
            self.articles_path, self.monitor, self.vector_db, self.summarizer
        )
        self.gigachat = get_model()
        self.parser = PydanticOutputParser(pydantic_object=AgentAction)
        self.obs_loader = OBSWorker()
        self.email_handler = DigestEmailHandler()
        self.skip_startup_autoprocess = skip_startup_autoprocess
        # Переиспользуемый httpx клиент
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def close(self):
        """Закрыть ресурсы при завершении."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    async def _send_status(self, status: str, request_id: str) -> None:
        if not request_id:
            return
        try:
            client = await self._get_http_client()
            await client.post(
                f"{GATEWAY_URL}/internal/bot_status",
                json={"request_id": request_id, "status": status},
                timeout=2,
            )
        except Exception as e:
            print(f"Не удалось отправить статус: {e}")

    async def get_state(self) -> str:
        changes = await asyncio.to_thread(self.monitor.get_changes)
        return (
            f"Статистика:\n"
            f"  • Новых файлов: {len(changes['new'])}\n"
            f"  • Изменённых: {len(changes['modified'])}\n"
            f"  • Удалённых: {len(changes['deleted'])}"
        )

    # ------------------------------------------------------------------
    # Анализ запроса
    # ------------------------------------------------------------------

    async def _analyze(self, user_input: str) -> Dict[str, Any]:
        state = await self.get_state()
        changes = await asyncio.to_thread(self.monitor.get_changes)

        prompt = f"""Ты ИИ-агент по управлению базой знаний.

ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
1. [search_db] - найти документы по запросу  
2. [summarize_file] - суммаризация/ревью файла
3. [summarize_new] - суммаризация/ревью новых статей
4. [summarize_all] - ревью ВСЕХ файлов
5. [analyze_folder] - анализ папки
6. [delete_file] - удалить файл из БД
7. [summarize_filt] - суммаризация/дайджест с условием по дате или теме, есть встронная отправка на почту
8. [rebuild_categories] - переопредели категории в метаданных
9. [general_question] - общие вопросы пользователя
10. [send_digest_email] - сформировать дайджест по всем файлам и отправить на email
11. [summarize_basket] - создать ревью/саммари статей по корзине с избранным
12. [digest_basket] - сформировать дайджест/сводку по корзине с избранным

ОСОБЫЕ ПРАВИЛА:
- "ревью по статье", "суммаризуй", "кратко" → [summarize_file]
- "ревью по всем", "суммаризуй все" → [summarize_all]
- Если запрос на поиск → [search_db]
- "проверь папку", "анализ" → [analyze_folder]
- "удалить файл" → [delete_file]
- "дайджест по категории", "дайджест за период" → [summarize_filt]
- Любые общие вопросы → [general_question]
- "отправь дайджест на почту" → [send_digest_email]

ПРИМЕЧАНИЯ:
 - не пугайся, если в [summarize_filt] пользователь укажет почту, тут отправяется дайджест за определенный период,
 а для [send_digest_email] за весь период

Состояние: {state}
Изменения: Новых={len(changes['new'])}, Изменённых={len(changes['modified'])}

Запрос: {user_input}

{self.parser.format_instructions}

ШАГ 1: Проверь наличие БД и изменения
ШАГ 2: Проанализируй запрос  
ШАГ 3: Выбери ТОЧНЫЙ инструмент"""

        try:
            messages = [SystemMessage(content=prompt)]
            response = await asyncio.to_thread(self.gigachat.invoke, messages)

            action = None
            for attempt in range(3):
                try:
                    action = self.parser.parse(response.content)
                    print("ПАРСИНГ УСПЕШЕН:", action)
                    break
                except Exception as parse_error:
                    print(f"Попытка {attempt + 1}/3: {parse_error}")
                    if attempt == 2:
                        raise parse_error

            stats = await asyncio.to_thread(self.vector_db.get_stats)
            return {
                "user_input": user_input,
                "state": state,
                "reasoning": action.reasoning,
                "action": action.action,
                "agent_answer": response.content,
                "files": action.files or [],
                "query": action.query or "",
                "status": "analyzed",
                "db_exists": stats["total_vectors"] > 0,
            }

        except Exception as e:
            print(f"Ошибка анализа: {e}")
            return self._fallback_user_action(user_input)

    def _fallback_user_action(self, user_input: str) -> Dict[str, Any]:
        user_lower = user_input.lower()
        if any(w in user_lower for w in ["поиск", "найди", "search"]):
            return {"action": "search_db", "query": user_input, "files": [], "reasoning": "Поиск"}
        elif any(w in user_lower for w in ["дайджест", "суммаризуй", "кратко"]):
            return {"action": "summarize_file", "files": [], "query": "", "reasoning": "Дайджест"}
        elif "удали" in user_lower or "delete" in user_lower:
            return {"action": "delete_file", "files": [], "query": "", "reasoning": "Удаление"}
        elif any(w in user_lower for w in ["обнови", "категории", "метаданные"]):
            return {"action": "rebuild_categories", "files": [], "query": "", "reasoning": "Обновление"}
        elif any(p in user_lower for p in ["почта", "email", "mail", "yandex"]):
            return {"action": "send_digest_email", "files": [], "query": user_input, "reasoning": "Email"}
        return {"action": "analyze_folder", "files": [], "query": "", "reasoning": "Анализ"}

    # ------------------------------------------------------------------
    # Диспетчер выполнения
    # ------------------------------------------------------------------

    async def _execute(self, action: Dict[str, Any], user_input: str = "", request_id: str = "", user_id: int = None) -> Dict[str, Any]:
        try:
            match action["action"]:

                case "delete_file":
                    await self._send_status(f"🗑️ Удаляю {len(action.get('files', []))} файлов из базы...", request_id)
                    return await tool_delete_file(self.vector_db, action.get("files", []))

                case "analyze_folder":
                    await self._send_status("📂 Анализирую директорию...", request_id)
                    return await tool_analyze_folder(self.monitor, self.articles_path)

                case "search_db":
                    await self._send_status("🔍 Ищу в базе знаний...", request_id)
                    return await tool_search_db(self.vector_db, action.get("query", ""), self.clean_markdown)

                case "rebuild_categories":
                    await self._send_status("🏗️ Перестраиваю категории...", request_id)
                    return await tool_rebuild_categories(self.obs_loader, self.name_meta_dir)

                case "general_question":
                    await self._send_status("💬 Формирую ответ на вопрос...", request_id)
                    return await tool_general_question(action.get("agent_answer", ""))

                case "send_digest_email":
                    await self._send_status("📧 Подготавливаю и отправляю дайджест на почту...", request_id)
                    return await tool_send_digest_email(
                        self.email_handler, user_input, self.gigachat,
                        self.obs_loader, self.name_meta_dir, self.digest_path,
                    )

                case "summarize_all":
                    await self._send_status("📥 Загружаю метаданные статей...", request_id)
                    return await tool_summarize_all(
                        self.articles_path, self.name_meta_dir,
                        self.digest_path, self.summarizer, self.obs_loader,
                    )

                case "summarize_new":
                    await self._send_status("🆕 Ищу новые статьи для обработки...", request_id)
                    return await tool_summarize_new(self.auto_processor, self.obs_loader)

                case "summarize_filt":
                    await self._send_status("🔎 Фильтрую статьи по запросу...", request_id)
                    return await tool_summarize_filt(
                        self.gigachat, user_input,
                        self.name_meta_dir, self.digest_path, self.obs_loader,
                    )

                case "summarize_file":
                    filename = (action.get("files") or [None])[0]
                    if not filename:
                        return {"status": "error", "message": "❌ Не получилось найти файл"}
                    await self._send_status(f"📥 Скачиваю статью: {filename}", request_id)
                    return await tool_summarize_file(
                        filename, self.articles_path, self.name_meta_dir,
                        self.summarizer, self.obs_loader,
                    )

                case "summarize_basket":
                    await self._send_status(f"🧺 Формирую ревью по корзине с избранным", request_id)
                    return await tool_make_reviews_basket(self.db, user_id, llm_client=self.gigachat)
                    time.sleep(5)
                case "digest_basket":
                    await self._send_status(f"⭐ Формирую единый дайджест по корзине с избранным", request_id)
                    return await tool_digest_basket(db=self.db, user_id=user_id, user_query=user_input)
                    time.sleep(5)

                case _:
                    return {"status": "error", "message": f"Неизвестный action: {action['action']}"}

        except Exception as e:
            return {"status": "error", "message": f"Ошибка: {str(e)}"}

    # ------------------------------------------------------------------
    # Вспомогательные методы класса
    # ------------------------------------------------------------------

    def pluralize_vectors(self, n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return "вектор"
        elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
            return "вектора"
        return "векторов"

    def clean_markdown(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"^(\s*[-*+]|\s*\d+\.)\s+", "", text, flags=re.MULTILINE)
        text = re.sub(r"^:\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", text)
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"`(.*?)`", r"\1", text)
        text = re.sub(r"^\s*---+\s*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"([А-ЯA-Z][^:.!?]*):\n\s*([А-ЯA-Z])", r"\1: \2", text)
        text = re.sub(r"\n\s*\n\s*\n", "\n\n", text)
        return text.strip()

    async def auto_categories(self) -> str:
        await self.obs_loader.download_metadata()
        base_dir = Path(__file__).resolve().parent
        json_file = base_dir / "knowledge_base" / "metadata.json"
        updater = MetadataUpdater(metadata_file=json_file, articles_path=self.articles_path, max_retries=1, retry_delay=30)
        message = await asyncio.to_thread(updater.run)

        with open(json_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        # 1. Удаляем нерелевантные локально
        self._delete_irrelevant_md_local(metadata)

        # 2. Удаляем нерелевантные из OBS
        await self.obs_loader.delete_irrelevant_md(metadata)
        await self.obs_loader.put_json()
        return message

    def _delete_irrelevant_md_local(self, metadata: list):
        """Удаляет локальные .md файлы нерелевантных статей."""
        deleted = 0
        for item in metadata:
            if item.get('is_relevant') or item.get('category') != 'Не релевантна PM':
                continue

            file_article = item.get('file_article', '')
            if not file_article or not file_article.endswith('.md'):
                continue

            local_path = self.articles_path / file_article
            if local_path.exists():
                local_path.unlink()
                deleted += 1
                print(f"  🗑️ Удалён локально: {file_article}")

        print(f"Локально удалено {deleted} нерелевантных .md файлов.")

    async def make_reviews(self) -> None:
        # await self.obs_loader.download_all_md()
        # await self.obs_loader.download_reviews()
        await self.auto_categories()

        def _load():
            with open(self.name_meta_dir / "metadata.json", "r", encoding="utf-8") as f:
                return json.load(f)

        articles_data = await asyncio.to_thread(_load)
        articles_map = {
            a["file_article"]: {
                "theme": a.get("category", "категория не найдена"),
                "pdf_url": a.get("pdf_url", "Не найден"),
            }
            for a in articles_data
            if a.get("category", "Не релевантна PM") != "Не релевантна PM"
        }

        if not self.digest_path.exists():
            os.makedirs(self.digest_path, exist_ok=True)

        md_articles = [n.name for n in self.articles_path.glob("*.md") if n.name in articles_map]
        md_reviews = [n.name for n in self.digest_path.rglob("*.md")]
        need_reviews = list(set(md_articles) - set(md_reviews))

        results = []
        for filename in need_reviews:
            article_path = self.articles_path / filename
            meta = articles_map.get(filename, {})
            content = await asyncio.to_thread(article_path.read_text, encoding="utf-8")
            if not content:
                continue
            digest_result = await asyncio.to_thread(
                self.summarizer.generate_summary,
                content=content,
                filename=filename,
                theme=meta.get("theme", "категория не найдена"),
                pdf_url=meta.get("pdf_url", "Не найден"),
            )
            results.append({
                "file": filename,
                "status": digest_result["status"],
                "digest_file": digest_result.get("digest_file"),
            })

        await self.obs_loader.upload_digests()

        new_digest_files = [r["digest_file"] for r in results if r["status"] == "created" and r.get("digest_file")]
        if new_digest_files:
            email_result = await asyncio.to_thread(self.email_handler.send_auto_digest, new_digest_files, self.obs_loader)
            print(f"Результат автоотправки: {email_result}")

        changes = await asyncio.to_thread(self.monitor.get_changes)
        deleted = changes.get("deleted", [])
        if deleted:
            await asyncio.to_thread(self.vector_db.delete_documents, deleted)

        count = await asyncio.to_thread(self.vector_db.rebuild_index)
        word = self.pluralize_vectors(count)
        if count == 0:
            print("⚠️ Не удалось создать векторный индекс")
        else:
            print(f"✅ Векторный индекс построен! Добавлено: {count} {word}.")

    # ------------------------------------------------------------------
    # Точка входа
    # ------------------------------------------------------------------

    async def process_query(self, user_input: str, request_id: str, user_id: int, database) -> Dict[str, Any]:
        if not self.db:
            self.db = database

        try:
            await self._send_status("🌞 Определяю инструмент ...", request_id)
            action = await self._analyze(user_input)
            result = await self._execute(action, user_input, request_id, user_id)
            final_state = await self.get_state()

            return {
                "input": user_input,
                "action": action["action"],
                "message": result.get("message", "Операция завершена."),
                "reasoning": action.get("reasoning", ""),
                "result": result,
                "final_state": final_state,
                "success": True,
            }
        except Exception as e:
            return {
                "input": user_input,
                "error": str(e),
                "success": False,
                "message": f"Я упал, но скоро встану. Ошибка: {e}",
            }