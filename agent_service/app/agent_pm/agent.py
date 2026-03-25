import logging
import json
import re
from typing import List, Dict, Any, Optional
from enum import Enum
from pydantic import BaseModel, Field
from pathlib import Path
import time
import asyncio
from .core.agent_provider import get_model
from langchain.schema import SystemMessage
from .core.config import *
from .vector_db import VectorDBManager
from .monitor import StorageMonitor
from .core.config import ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH, METADATA_FILE
from .summary import SummaryGenerator
from .core.botocore_client import OBSWorker
import os
from .summary_period import agent_composite_digest_tool
from .update_metadata import MetadataUpdater
from .digest_email_handler import DigestEmailHandler


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
                    'email': 'send_digest_email'
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
            'send_digest_email': ['отправь на почту', 'отправь дайджест', 'send email', 'отправь на email']
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
  "action": "search_db|summarize_file|summarize_all|summarize_new|analyze_folder|delete_file|summarize_filt|rebuild_categories|general_question|send_digest_email",
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
                        content_=content,
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


class KnowledgeAgent:
    def __init__(self, skip_startup_autoprocess: bool = False):
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.articles_path = base_dir / Path(STORAGE_PATH) / ARTICLES_FOLDER
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)
        self.digest_path = base_dir / "digests" # Хардкод, занести в конфиг
        # print("ПУТЬ ДО БАЗЫ ===========", self.articles_path)
        self.monitor = StorageMonitor(STORAGE_PATH, METADATA_FILE)
        self.vector_db = VectorDBManager(str(self.digest_path), VECTOR_DB_PATH)
        self.summarizer = SummaryGenerator()
        self.auto_processor = AutoProcessor(
            self.articles_path, self.monitor, self.vector_db,
            self.summarizer)
        self.gigachat = get_model() # Паттерн Singleton для получения экзепляра Гигачат

        # PydanticOutputParser
        self.parser = PydanticOutputParser(pydantic_object=AgentAction)
        self.obs_loader = OBSWorker()

        # Email (вынесено в отдельный модуль digest_email_handler)
        self.email_handler = DigestEmailHandler()
        # asyncio.run(self.obs_loader.run_download())
        # asyncio.run(self.obs_loader.download_reviews())
        # asyncio.run(self.obs_loader.download_vectorstore())
        asyncio.run(self.make_reviews())
        # Флаг для автозапуска
        self.skip_startup_autoprocess = skip_startup_autoprocess


    def get_state(self) -> str:
        """Текущее состояние системы с проверкой существования БД"""
        changes = self.monitor.get_changes()
        stats = self.vector_db.get_stats()

        # # Проверяем наличие векторной базы
        # db_exists = stats['total_vectors'] > 0
        # db_status = "СУЩЕСТВУЕТ" if db_exists else "ОТСУТСТВУЕТ - НУЖНО ПОСТРОИТЬ"

        return f"""Статистика:
    • Новых файлов: {len(changes['new'])}
    • Изменённых: {len(changes['modified'])}
    • Удалённых: {len(changes['deleted'])}"""

    def _analyze(self, user_input: str) -> Dict[str, Any]:
        """АНАЛИЗ с авто-рекомендацией построения БД"""
        state = self.get_state()
        changes = self.monitor.get_changes()

        # УМНЫЙ промпт с авто-постройкой БД
        prompt = f"""Ты ИИ-агент по управлению базой знаний.

    ДОСТУПНЫЕ ИНСТРУМЕНТЫ:
    1. [search_db] - найти документы по запросу  
    2. [summarize_file] - суммаризация/ревью файла
    3. [summarize_new] - суммаризация/ревью новых статей
    4. [summarize_all] - ревью ВСЕХ файлов
    5. [analyze_folder] - анализ папки
    6. [delete_file] - удалить файл из БД
    7. [summarize_filt] - суммаризация/дайджест с условием по дате или теме
    8. [rebuild_categories] - переопредели категории в метаданыных
    9. [general_question] - общие вопросы пользователя, которые не касаются директорий, БД, дайджестов и ревью
    10. [send_digest_email] - сформировать дайджест и отправить на email пользователя

    ОСОБЫЕ ПРАВИЛА:
    - "ревью по статье", "суммаризуй", "кратко" → [summarize_file]
    - "ревью по всем", "суммаризуй все" → [summarize_all]
    - Если запрос на поиск → [search_db]
    - "проверь папку", "анализ" → [analyze_folder]
    - "удалить файл" → [delete_file]
    - "дайджест по категории", "дайджест за период" → [summarize_filt]
    - Любые общие вопросы, которые не относятся к примерам выше, отвечай как знаешь, но называй свою роль → [general_question]
    - "отправь дайджест на почту", "отправь на email" → [send_digest_email]

    Состояние: {state}
    Изменения: Новых={len(changes['new'])}, Изменённых={len(changes['modified'])}

    Запрос: {user_input}

    {self.parser.format_instructions}

    ШАГ 1: Проверь наличие БД и изменения
    ШАГ 2: Проанализируй запрос  
    ШАГ 3: Выбери ТОЧНЫЙ инструмент"""

        try:
            messages = [SystemMessage(content=prompt)]
            response = self.gigachat.invoke(messages)

            # Парсим план агента
            for attempt in range(3):
                try:
                    action = self.parser.parse(response.content)

                    print("ПАРСИНГ УСПЕШЕН:", action)
                    break
                except Exception as parse_error:
                    print(f"Попытка {attempt + 1}/3: {parse_error}")
                    if attempt == 2:
                        raise parse_error

            return {
                "user_input": user_input,
                "state": state,
                "reasoning": action.reasoning,
                "action": action.action,
                "agent_answer": response.content,
                "files": action.files or [],
                "query": action.query or "",
                "status": "analyzed",
                "db_exists": self.vector_db.get_stats()['total_vectors'] > 0
            }

        except Exception as e:
            print(f"Ошибка анализа: {e}")
            return self._fallback_user_action(user_input)

    def _fallback_user_action(self, user_input: str) -> Dict[str, Any]:
        user_lower = user_input.lower()
        if any(word in user_lower for word in ['поиск', 'найди', 'search']):
            return {"action": "search_db", "query": user_input, "files": [], "reasoning": "Поиск"}
        elif any(word in user_lower for word in ['дайджест', 'суммаризуй', 'кратко']):
            return {"action": "summarize_file", "files": [], "query": "", "reasoning": "Дайджест"}
        elif 'удали' in user_lower or 'delete' in user_lower:
            return {"action": "delete_file", "files": [], "query": "", "reasoning": "Удаление"}
        # elif any(word in user_lower for word in ['обнови', 'построй', 'перестрой']):
        #     return {"action": "rebuild_all", "files": [], "query": "", "reasoning": "Перестройка"}
        elif any(word in user_lower for word in ['обнови', 'категории', 'метаданные']):
            return {"action": "rebuild_categories", "files": [], "query": "", "reasoning": "Обновление"}
        elif any(phrase in user_lower for phrase in ['отправь на почту', 'отправь на email', 'отправь дайджест']):
            return {"action": "send_digest_email", "files": [], "query": user_input, "reasoning": "Отправка email"}
        return {"action": "analyze_folder", "files": [], "query": "", "reasoning": "Анализ"}

    def pluralize_vectors(self, n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return "вектор"
        elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
            return "вектора"
        else:
            return "векторов"

    def _execute(self, action: AgentAction, user_input='') -> Dict[str, Any]:
        """ШАГ 2: ВЫПОЛНЕНИЕ ИНСТРУМЕНТА"""
        # print(f"ВЫПОЛНЯЕМ: {action['action']}")

        try:
            # if action['action'] == "rebuild_all":
            #     changes = self.monitor.get_changes()
            #     deleted = changes.get('deleted', [])
            #     if deleted:
            #         self.vector_db.delete_documents(deleted)
            #     count = self.vector_db.rebuild_index()
            #     word = self.pluralize_vectors(count)
            #     if count == 0:
            #         return {
            #             "status": "warning",
            #             "message": (
            #                 "⚠️ **Не удалось создать векторный индекс**\n\n"
            #                 "💡 **Что делать:**\n"
            #                 "Выполните команду:\n"
            #                 "`Сформируй ревью по всем статьям`"
            #             )
            #         }
            #     else:
            #         return {
            #             "status": "success",
            #             "message": (
            #                 f"✅ **Векторный индекс успешно построен!**\n\n"
            #                 f"Обработано и добавлено в базу: **{count} {word}**.\n"
            #                 f"Теперь семантический поиск готов к работе. 🚀"
            #             )
            #         }

            if action['action'] == "delete_file":
                files = action.get('files', [])
                if files:
                    self.vector_db.delete_documents(files)
                    return {"status": "success", "message": f"Я удалил {len(files)} файлов"}
                return {"status": "error", "message": "Нет файлов для удаления"}

            elif action['action'] == "analyze_folder":
                changes = self.monitor.get_changes()
                files_list = list(self.articles_path.glob("*.md"))
                return {
                    "status": "success",
                    "new_files": len(changes['new']),
                    "modified_files": len(changes['modified']),
                    "total_files": len(files_list),
                    "files": [f.name for f in files_list[:5]],
                    "message":  f"📂 **Анализ директории завершен**\n\n"
                                f"📊 **Статистика изменений:**\n"
                                f"• 🆕 Новых файлов: `{len(changes['new'])}`\n"
                                f"• ✏️ Изменено файлов: `{len(changes['modified'])}`\n"
                                f"• 📁 Всего файлов: `{len(files_list)}`"


                }

            elif action['action'] == "search_db":
                # asyncio.run(self.obs_loader.download_vectorstore()) # OBS ломает файлы, хотя вроде сохраняем как бинарник
                results = self.vector_db.similarity_search(action['query'] or "", k=5)
                if not results:
                    return {
                        "status": "warning",  # Меняем на warning, так как это не успех, а отсутствие данных
                        "count": 0,
                        "preview": [],
                        "message": (
                            f"🔍 **По запросу не найдено совпадений**\n\n"
                            f"Запрос: `\"{action['query']}\"`\n\n"
                            f"⚠️ **Вероятная причина:** Векторная база знаний пуста или еще не создана.\n"
                            f"Без векторного индекса семантический поиск работать не может.\n\n"
                            f"💡 **Рекомендация:**\n"
                            f"Сначала выполните команду для построения индекса:\n"
                            f"`Построй векторную базу по всем статьям`"
                        )
                    }

                formatted_results = []
                for i, r in enumerate(results, 1):
                    raw_content = r['content'].strip()
                    clean_content = self.clean_markdown(raw_content)
                    content_snippet = clean_content
                    if len(content_snippet) > 512:
                        content_snippet = content_snippet[:512].rsplit(' ', 1)[0] + '...'
                    score = r['score']
                    block = f"""**Результат {i}** (релевантность: {score:.3f})

{content_snippet}"""
                    formatted_results.append(block)

                full_preview = "\n\n---\n\n".join(formatted_results)
                        
                message = f"""Найдено {len(results)} релевантных фрагментов по запросу:

{full_preview}"""

                return {
                    "status": "success",
                    "count": len(results),
                    "preview": formatted_results,
                    "message": message
                }

            elif action['action'] == "rebuild_categories":
                message = asyncio.run(self.auto_categories())
                return {
                    "status": "success",
                    "message": message
                }

            elif action['action'] == "general_question":
                raw_text = action["agent_answer"]
                text_no_tag = re.sub(r'\s*\[.*?\]\s*', '', raw_text)

                clean_text = text_no_tag

                match = re.search(r'\{[^{}]*\}', clean_text, re.DOTALL)
                clean_text = clean_text[:match.start()] + clean_text[match.end():]

                clean_text = re.sub(r'\*\*', '', clean_text)  # Убираем жирность
                clean_text = re.sub(r'\n\s*\n', '\n', clean_text)  # Убираем двойные пустые строки
                clean_text = re.sub(r'general_question', '', clean_text)

                final_answer = clean_text.strip()
                instruction_link = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/instructions/agent_commands.html"
                if not final_answer:
                    final_answer = "Чистили-чистили, но не дочистили. Это был точно общий вопрос"

                final_answer = f"{final_answer}\n\n📚 [Прикладываю ссылку на инструкцию]({instruction_link})"

                return {"status": "success", "message": final_answer}

            elif action['action'] == "send_digest_email":
                return self.email_handler.execute(
                    user_input=user_input,
                    gigachat=self.gigachat,
                    obs_loader=self.obs_loader,
                    name_meta_dir=self.name_meta_dir,
                    digest_path=self.digest_path,
                )

            elif action['action'] in ["summarize_file", "summarize_all", "summarize_new", "summarize_filt"]:
                if action['action'] == "summarize_all":
                    asyncio.run(self.obs_loader.run_download())
                    asyncio.run(self.obs_loader.download_reviews())
                    # Дайджест ВСЕХ статей
                    reviews_root = self.digest_path
                    # ДУБЛИРОВАНИЕ КОДА
                    json_file = self.name_meta_dir / "metadata.json"
                    with open(json_file, 'r',
                              encoding='utf-8') as f:  # Считаем метаданные один раз, чтобы связать с множеством ревью
                        articles_data = json.load(f)

                    articles_map: Dict[str, Dict[str, str]] = {
                        article['file_article']: {
                            "theme": article.get('category', 'категория не найдена'),
                            "pdf_url": article.get('pdf_url', 'Не найден')
                        }
                        for article in articles_data if not article.get('category', 'Не релевантна PM') == 'Не релевантна PM'
                    }


                    # Дайджест ВСЕХ статей
                    if not reviews_root.exists():
                        os.makedirs(reviews_root, exist_ok=True)
                    md_articles = [name.name for name in self.articles_path.glob("*.md") if name.name in articles_map.keys()] # Тексты оригинальных статей
                    md_reviews = [name.name for name in reviews_root.rglob("*.md")]  # Тексты ревью

                    need_reviews = list(set(md_articles) - set(md_reviews))
                    print("Тексты статей для которых нужно сделать ревью", need_reviews)
                    # print("Спискок статей", len(md_files))
                    for filename in need_reviews:
                        article_path = self.articles_path / filename
                        meta = articles_map.get(filename, {})
                        theme = meta.get("theme", "категория не найдена")
                        pdf_url = meta.get("pdf_url", "Не найден")

                        digest_result = self.summarizer.generate_summary(
                            content_=article_path.read_text(encoding='utf-8'),
                            filename=filename,
                            theme=theme,
                            pdf_url=pdf_url
                        )
                        # ДУБЛИРОВАНИЕ КОДА
                        results.append({
                            "file": filename,
                            "status": digest_result["status"],
                            "digest_file": digest_result.get('digest_file')
                        })

                    asyncio.run(self.obs_loader.upload_digests())
                    articles_count = len(md_articles)
                    reviews_count = len(md_reviews)
                    diff = articles_count - reviews_count

                    obs_path = "obs://cds-iad/reviews/"
                    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/reviews/"

                    if diff == 0:
                        # 🎉 Сценарий: Полная синхронизация
                        message = (
                            f"🎉 **Полная синхронизация!**\n\n"
                            f"🗄 **Статей:** `{articles_count}`\n"
                            f"✅ **Ревью:** `{reviews_count}`\n\n"
                            f"✨ Все актуальные статьи уже имеют ревью.\n"
                            f"Новых задач для генерации нет.\n"
                            f"📂 Хранилище актуально: `{obs_path}`"
                        )
                        status = "synced"

                    else:
                        excess_count = abs(diff) 
                        
                        message = (
                            f"⚠️ **Обнаружено несоответствие!**\n\n"
                            f"🗄 **Статей в хранилище:** `{articles_count}`\n"
                            f"✅ **Найдено ревью:** `{reviews_count}`\n\n"
                            f"🧮 **Разница:** `{excess_count}` лишних ревью.\n\n"
                            f"🤔 **Возможные причины:**\n"
                            f"• Статьи были удалены из базы, но ревью остались.\n"
                            f"• Ревью были созданы вручную.\n"
                            f"• Изменилась логика именования файлов.\n\n"
                            f"💡 **Рекомендация:** Проверьте папку `{obs_path}` на наличие orphaned (осиротевших) файлов."
                        )
                        status = "warning"

                    # Формируем итоговый ответ
                    return {
                        "status": status,
                        "message": message,
                        "stats": {
                            "articles": articles_count,
                            "reviews": reviews_count,
                            "difference": diff
                        }
                    }
                elif action['action'] == "summarize_new":
                    asyncio.run(self.obs_loader.run_download())
                    results = self.auto_processor.process_new_articles()
                    asyncio.run(self.obs_loader.upload_digests())
                    asyncio.run(self.obs_loader.upload_vectorstore())
                    return {
                        "status": "success",
                        "message": f"Провел саммари новых статей",
                        "results": results
                    }
                elif action['action'] == "summarize_filt":
                    # asyncio.run(self.obs_loader.run_download())
                    asyncio.run(self.obs_loader.download_reviews())
                    json_file = self.name_meta_dir / "metadata.json"
                    results = agent_composite_digest_tool(llm_client=self.gigachat, query=user_input, json_path=json_file, md_folder=self.digest_path)
                    filename_fin = results['digest_file']
                    # print(results['digest_file'])
                    # print(results['filename'])
                    if results['found_articles'] == 0:
                        message = (
                            "❌ **Нет данных для формирования дайджеста**\n\n"
                            "По вашему запросу (период или категория) материалов не обнаружено.\n"
                            "Пожалуйста, выберите другие параметры."
                        )
                    else:
                        count = results['found_articles']
                        filename = results['filename']
                        base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
                        full_link = f"{base_url}{filename}"
                        
                        message = (
                            f"🎉 **Готово! Дайджест сформирован.**\n\n"
                            f"• Имя файла: `{filename}`\n\n"
                            f"🔗 **Ссылка на скачивание:** [Скачать]({full_link})"
                        )
                    # print(message)
                    asyncio.run(self.obs_loader.upload_single_digest_cond(filename_fin, results['filename']))
                    return {
                        "status": "success",
                        "message": message,
                        "results": results
                    }
                else:  # summarize_file
                    if action.get('files') and action['files'][0]:
                        filename = action.get('files', [None])[0]

                        asyncio.run(self.obs_loader.ensure_article_downloaded(filename))
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
                                content_=content,
                                filename=filename,
                                theme=theme,
                                pdf_url=pdf_url
                            )

                            base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/reviews"
                            obs_path = "obs://cds-iad/PM/reviews/"

                            if digest_result["status"] == "created":
                                filename_fin = digest_result['digest_file']
                                # Загрузка файла
                                asyncio.run(self.obs_loader.upload_single_digest(filename_fin, filename))
                                
                                full_link = f"{base_url}/{filename}"
                                
                                message = (
                                    f"✨ **Одиночное ревью успешно сформировано!**\n\n"
                                    f"📄 **Файл:** `{filename}`\n"
                                    f"📥 **Скачать:** [Нажмите здесь]({full_link})\n"
                                    f"_Или скопируйте ссылку:_ `{full_link}`"
                                )
                                
                                return {
                                    "status": "success",
                                    "message": message
                                }

                            elif digest_result["status"] == "exists":
                                filename_fin = digest_result['digest_file']
                                asyncio.run(self.obs_loader.upload_single_digest(filename_fin, filename))
                                
                                existing_link = f"{base_url}/{filename}"
                                
                                message = (
                                    f"ℹ️ **Ревью уже существует**\n\n"
                                    f"Файл `{filename}` был создан ранее.\n"
                                    f"📂 **Путь в хранилище:** `{obs_path}`\n"
                                    f"🔗 **Ссылка на скачивание:** `{existing_link}`"
                                )
                                
                                return {
                                    "status": "exists",
                                    "message": message
                                }

                    return {"status": "error", "message": "❌ Не получилось найти файл"}

        except Exception as e:
            return {"status": "error", "message": f"Ошибка: {str(e)}"}

    # def run_autonomous_mode(self) -> Dict[str, Any]:
    #     """АВТОНОМНЫЙ РЕЖИМ: только автозапуск + 30мин ожидания пользователя"""
    #
    #     start_time = time.time()
    #
    #     try:
    #         # ШАГ 1: Автоматическая обработка новых статей
    #         result = self.auto_processor.process_new_articles()
    #
    #         # ШАГ 2: Финальное состояние
    #         final_stats = self.get_state()
    #
    #
    #         return {
    #             "mode": "autonomous",
    #             "status": "completed",
    #             "autoprocess": result,
    #             "duration": time.time() - start_time,
    #             "final_stats": final_stats
    #         }
    #
    #     except Exception as e:
    #         return {"mode": "autonomous", "status": "error", "error": str(e)}

    def clean_markdown(self, text):
        if not text:
            return ""
    
        # 1. Убираем явные заголовки (# Заголовок)
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        
        # 2. Убираем жирный/курсив
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\*(.*?)\*', r'\1', text)
        
        # 3. Убираем маркеры списков
        text = re.sub(r'^(\s*[-*+]|\s*\d+\.)\s+', '', text, flags=re.MULTILINE)
        
        # 4. Защита от ложных заголовков по двоеточию
        text = re.sub(r'^:\s*', '', text, flags=re.MULTILINE)
        
        # 5. Убираем ссылки и код
        text = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', text)
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`(.*?)`', r'\1', text)
        
        # 6. Убираем горизонтальные линии внутри сниппета
        text = re.sub(r'^\s*---+\s*$', '', text, flags=re.MULTILINE)
        
        # 7. "Сплющиваем" опасные переносы строк
        text = re.sub(r'([А-ЯA-Z][^:.!?]*):\n\s*([А-ЯA-Z])', r'\1: \2', text)
        
        # Чистим лишние пустые строки
        text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)
        
        return text.strip()


    async def auto_categories(self):
        await self.obs_loader.download_metadata()  # Подкачаем метаданные для определения категорий
        base_dir = Path(__file__).resolve().parent
        json_file = base_dir / "knowledge_base" / "metadata.json"

        updater = MetadataUpdater(
            metadata_file=json_file,
            max_retries=1,
            retry_delay=30
        )

        message = updater.run()
        await self.obs_loader.put_json()
        return message

    async def make_reviews(self):
        await self.auto_categories() # Заранее определим категории для статей внутри метаданных
        await self.obs_loader.download_all_md() # Подгрузим оригиналы статей
        await self.obs_loader.download_reviews() # Подгрузим уже готовые ревью
        reviews_root = self.digest_path  # Путь до ревьюшек/summary статей по шаблону
        json_file = self.name_meta_dir / "metadata.json"  # Файл до метаданных

        with open(json_file, 'r', encoding='utf-8') as f: # Считаем метаданные один раз, чтобы связать с множеством ревью
            articles_data = json.load(f)

        articles_map: Dict[str, Dict[str, str]] = {
            article['file_article']: {
                "theme": article.get('category', 'категория не найдена'),
                "pdf_url": article.get('pdf_url', 'Не найден')
            }
            for article in articles_data if not article.get('category', 'Не релевантна PM') == 'Не релевантна PM'
        }


        # Дайджест ВСЕХ статей
        if not reviews_root.exists():
            os.makedirs(reviews_root, exist_ok=True)
        md_articles = [name.name for name in self.articles_path.glob("*.md") if name.name in articles_map.keys()] # Тексты оригинальных статей
        md_reviews = [name.name for name in reviews_root.rglob("*.md")]  # Тексты ревью

        need_reviews = list(set(md_articles) - set(md_reviews))
        print("Тексты статей для которых нужно сделать ревью", need_reviews)
        results = []
        for filename in need_reviews:
            article_path = self.articles_path / filename
            meta = articles_map.get(filename, {})
            theme = meta.get("theme", "категория не найдена")
            pdf_url = meta.get("pdf_url", "Не найден")
            content = article_path.read_text(encoding='utf-8')
            print(f"ℹ️ Контент размер = {len(content)}, для файла {filename}")
            if len(content) == 0:
                continue
            digest_result = self.summarizer.generate_summary(
                    content=content,
                    filename=filename,
                    theme=theme,
                    pdf_url=pdf_url
                )
            results.append({
                    "file": filename,
                    "status": digest_result["status"],
                    "digest_file": digest_result.get('digest_file')
                })

        await self.obs_loader.upload_digests()
        success_count = sum(1 for r in results if r["status"] == "created")
        print(success_count)

        # Автоотправка новых дайджестов на почту
        new_digest_files = [
            r["digest_file"] for r in results
            if r["status"] == "created" and r.get("digest_file")
        ]
        if new_digest_files:
            print(f"Автоотправка {len(new_digest_files)} дайджестов на почту...")
            email_result = self.email_handler.send_auto_digest(new_digest_files)
            print(f"Результат автоотправки: {email_result}")

        changes = self.monitor.get_changes()
        deleted = changes.get('deleted', [])
        if deleted:
            await asyncio.to_thread(self.vector_db.delete_documents, deleted)
        count = await asyncio.to_thread(self.vector_db.rebuild_index)
        word = self.pluralize_vectors(count)
        if count == 0:
            print("⚠️ **Не удалось создать векторный индекс**\n\n 💡 **Что делать:**\n Выполните команду:\n `Сформируй ревью по всем статьям`"
                )
        else:
            print(f"✅ **Векторный индекс успешно построен!**\n\n"
                    f"Обработано и добавлено в базу: **{count} {word}**.\n"
                    f"Теперь семантический поиск готов к работе. 🚀"
                )

    def process_query(self, user_input: str) -> Dict[str, Any]:
        try:
            action = self._analyze(user_input)
            result = self._execute(action, user_input)
            final_state = self.get_state()

            user_message = result.get("message", "Операция завершена.")
            return {
                "input": user_input,
                "action": action["action"],
                "message": user_message,
                "reasoning": action["reasoning"],
                "result": result,
                "final_state": final_state,
                "success": True
            }
        except Exception as e:
            error_msg = f"Я упал, но скоро встану. Ошибка: {str(e[:100])}"
            return {
                "input": user_input,
                "error": str(e),
                "success": False,
                "message": error_msg
            }


