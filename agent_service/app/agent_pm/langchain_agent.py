import asyncio
import logging
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain.tools import tool
from langchain_core.messages import BaseMessage
from langchain.memory import ConversationBufferWindowMemory

from .core.agent_provider import get_model, get_async_gemini
from .core.botocore_client import OBSWorker
from .core.config import (
    ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH,
    METADATA_FILE, PORT_GATEWAY_PORT
)
from datetime import datetime
from .digest_email_handler import DigestEmailHandler
from .monitor import StorageMonitor
from .summary import SummaryGenerator
from .summary_period import agent_composite_digest_tool
from .update_metadata import MetadataUpdater
from .vector_db import VectorDBManager
from .download_pdf_and_make import download_and_parse_pdf, generate_review
from .favorites_digest import build_user_favorites_review_digest_from_query

GATEWAY_URL = f"http://api_gateway:{PORT_GATEWAY_PORT}"

logger = logging.getLogger(__name__)


# ================================================================
# ASYNC TOOL FUNCTIONS
# ================================================================

async def tool_delete_file(vector_db: VectorDBManager, files: List[str]) -> Dict[str, Any]:
    if not files:
        return {"status": "error", "message": "Нет файлов для удаления"}
    await asyncio.to_thread(vector_db.delete_documents, files)
    return {"status": "success", "message": f"Удалено {len(files)} файлов из базы знаний."}


async def tool_analyze_folder(monitor: StorageMonitor, articles_path: Path) -> Dict[str, Any]:
    changes = await asyncio.to_thread(monitor.get_changes)
    files_list = list(articles_path.glob("*.md"))
    return {
        "status": "success",
        "new_files": len(changes["new"]),
        "modified_files": len(changes["modified"]),
        "total_files": len(files_list),
        "files": [f.name for f in files_list[:5]],
        "changes": changes,
    }


async def tool_search_db(vector_db: VectorDBManager, query: str, clean_markdown_fn) -> Dict[str, Any]:
    results = None
    try:
        results = await asyncio.to_thread(vector_db.similarity_search, query, 5)
    except:
        logger.warning("Модель векторизации отвалилась")
        return {
            "status": "critical",
            "count": 0,
            "preview": [],
            "raw_results": [],
        }
    if not results:
        return {
            "status": "warning",
            "count": 0,
            "preview": [],
            "raw_results": [],
        }
    formatted = []
    for i, r in enumerate(results, 1):
        clean = clean_markdown_fn(r["content"].strip())
        snippet = clean[:512].rsplit(" ", 1)[0] + "..." if len(clean) > 512 else clean
        formatted.append({"rank": i, "score": r["score"], "snippet": snippet})
    return {"status": "success", "count": len(results), "preview": formatted}


async def tool_rebuild_categories(
    obs_loader: OBSWorker, name_meta_dir: Path, article_path: Path,
    rebuild_errors: bool = True
) -> Dict[str, Any]:
    await obs_loader.download_metadata()
    json_file = name_meta_dir / "metadata.json"
    updater = MetadataUpdater(
        metadata_file=json_file, articles_path=article_path, max_retries=1, retry_delay=30
    )
    message = await updater.run(rebuild_errors)
    await obs_loader.put_json()
    return {"status": "success", "message": message}


async def tool_send_digest_email(
    email_handler: DigestEmailHandler,
    user_input: str,
    gigachat,
    obs_loader: OBSWorker,
    name_meta_dir: Path,
    digest_path: Path,
) -> Dict[str, Any]:
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
    json_file = name_meta_dir / "metadata.json"

    def _load():
        with open(json_file, "r", encoding="utf-8") as f:
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

    os.makedirs(digest_path, exist_ok=True)
    md_articles = [n.name for n in articles_path.glob("*.md") if n.name in articles_map]
    md_reviews = [n.name for n in digest_path.rglob("*.md")]
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

    return {
        "status": "success",
        "articles_count": len(md_articles),
        "reviews_count": len(md_reviews),
        "new_reviews": len(need_reviews),
        "obs_path": "obs://cds-iad/reviews/",
    }


async def tool_summarize_new(auto_processor, obs_loader: OBSWorker) -> Dict[str, Any]:
    results = await asyncio.to_thread(auto_processor.process_new_articles)
    await obs_loader.upload_digests()
    await obs_loader.upload_vectorstore()
    return {"status": "success", "results": results}


async def tool_summarize_filt(
    gigachat, user_input: str, name_meta_dir: Path, digest_path: Path, obs_loader: OBSWorker
) -> Dict[str, Any]:
    json_file = name_meta_dir / "metadata.json"
    results = await asyncio.to_thread(
        agent_composite_digest_tool,
        llm_client=gigachat,
        query=user_input,
        json_path=json_file,
        md_folder=digest_path,
    )
    await obs_loader.upload_single_digest_cond(results["digest_file"], results["filename"])
    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
    return {
        "status": "success",
        "found_articles": results["found_articles"],
        "filename": results["filename"],
        "download_url": f"{base_url}{results['filename']}",
        "results": results,
    }


async def tool_summarize_file(
    filename: str,
    articles_path: Path,
    name_meta_dir: Path,
    summarizer: SummaryGenerator,
    obs_loader: OBSWorker,
) -> Dict[str, Any]:
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

    if not filepath.exists():
        return {"status": "error", "filename": filename}

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
        base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/reviews"
        return {
            "status": digest_result["status"],
            "filename": filename,
            "download_url": f"{base_url}/{filename}",
            "obs_path": "obs://cds-iad/PM/reviews/",
        }

    return {"status": "error", "filename": filename}


async def process_single_article_safe(article: dict, db, user_id: int, llm) -> bool:
    article_id = article["article_id"]
    pdf_url = article["pdf_url"]
    title = article["title"]
    try:
        text_content = await download_and_parse_pdf(pdf_url)
        if not text_content or len(text_content) < 50:
            raise ValueError("Недостаточно текста для анализа")
        review_text = await generate_review(llm, text_content, title, pdf_url)
        await db.save_review(user_id, article_id, review_text)
        return True
    except Exception as e:
        logger.error(f"Failed to process article {article_id} ({title}): {e}")
        return False


async def tool_make_reviews_basket(db, user_id, llm_client) -> Dict[str, Any]:
    rows = await db.get_reviews(user_id)
    articles_data = [dict(row) for row in rows]
    articles_to_process = [a for a in articles_data if not a.get("review_text")]

    if not articles_data:
        return {"status": "success", "basket_empty": True, "total": 0}
    if not articles_to_process:
        return {"status": "success", "basket_empty": False, "all_done": True, "total": len(articles_data)}

    tasks = [
        process_single_article_safe(article=a, db=db, user_id=user_id, llm=llm_client)
        for a in articles_to_process
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    success_count = sum(1 for r in results if r is True)

    return {
        "status": "success",
        "processed": success_count,
        "total": len(articles_to_process),
    }


async def tool_digest_basket(db, user_id: str, user_query: str) -> Dict[str, Any]:
    rows = await db.get_reviews(user_id)
    articles_data = [dict(row) for row in rows]
    res = await build_user_favorites_review_digest_from_query(
        username=user_id,
        favorites_with_reviews=articles_data,
        user_query=user_query,
        save_to_file=False,
    )
    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
    if res["digest_markdown"]:
        return {
            "status": "created",
            "file_name": res["file_name"],
            "download_url": f"{base_url}{res['file_name']}",
        }
    return {"status": "error"}


# ================================================================
# AutoProcessor
# ================================================================

class AutoProcessor:
    def __init__(
        self,
        articles_path: Path,
        monitor: StorageMonitor,
        vector_db: VectorDBManager,
        summarizer: SummaryGenerator,
    ):
        self.articles_path = articles_path
        self.monitor = monitor
        self.vector_db = vector_db
        self.summarizer = summarizer
        base_dir = Path(__file__).resolve().parent
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)

    def process_new_articles(self) -> Dict[str, Any]:
        changes = self.monitor.get_changes()
        new_articles = changes.get("new", [])
        deleted_files = changes.get("deleted", [])
        results = {"new": [], "deleted": []}

        if new_articles:
            json_file = self.name_meta_dir / "metadata.json"
            with open(json_file, "r", encoding="utf-8") as f:
                articles_data = json.load(f)
            articles_map = {
                a["file_article"]: {
                    "theme": a.get("category", "категория не найдена"),
                    "pdf_url": a.get("pdf_url", "Не найден"),
                }
                for a in articles_data
            }
            for filename in new_articles:
                filepath = self.articles_path / filename
                meta = articles_map.get(filename, {})
                if filepath.exists():
                    content = filepath.read_text(encoding="utf-8")
                    digest_result = self.summarizer.generate_summary(
                        content=content,
                        filename=filename,
                        theme=meta.get("theme", "категория не найдена"),
                        pdf_url=meta.get("pdf_url", "Не найден"),
                    )
                    results["new"].append({
                        "file": filename,
                        "status": digest_result["status"],
                        "digest_file": digest_result.get("digest_file", ""),
                    })

        if deleted_files:
            self.vector_db.delete_documents(deleted_files)
            results["deleted"] = deleted_files

        if new_articles or deleted_files:
            vector_count = self.vector_db.rebuild_index()
            results["vectors_built"] = vector_count

        return results


# ================================================================
# KnowledgeAgent — LangChain Tools + Window Memory
# ================================================================

SYSTEM_PROMPT = """Ты ИИ-агент по управлению базой знаний статей по Product Management.
Ты помогаешь пользователям искать статьи, создавать ревью и дайджесты, управлять базой знаний.
Отвечай на русском языке, дружелюбно и по делу. Используй доступные инструменты для выполнения задач.
При необходимости задавай уточняющие вопросы. Не выдумывай данные — всегда используй инструменты.
Текущая дата и время: {datetime}
Состояние базы: {state}"""


class KnowledgeAgent:
    def __init__(self, database=None, skip_startup_autoprocess: bool = False):
        base_dir = Path(__file__).resolve().parent
        self.db = database
        self.articles_path = base_dir / Path(STORAGE_PATH) / ARTICLES_FOLDER
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)
        self.digest_path = base_dir / "digests"

        self.monitor = StorageMonitor(STORAGE_PATH, METADATA_FILE)
        self.vector_db = VectorDBManager(str(self.digest_path), VECTOR_DB_PATH)
        self.summarizer = SummaryGenerator()
        self.auto_processor = AutoProcessor(
            self.articles_path, self.monitor, self.vector_db, self.summarizer
        )
        self.gigachat = get_async_gemini()
        self.obs_loader = OBSWorker()
        self.email_handler = DigestEmailHandler()
        self.skip_startup_autoprocess = skip_startup_autoprocess

        # Память: последние 10 сообщений (5 пар human/ai)
        self._memories: Dict[str, ConversationBufferWindowMemory] = {}

        self._http_client: Optional[httpx.AsyncClient] = None


    # ------------------------------------------------------------------
    # Память per-user
    # ------------------------------------------------------------------

    def _get_memory(self, user_id) -> ConversationBufferWindowMemory:
        key = str(user_id)
        if key not in self._memories:
            self._memories[key] = ConversationBufferWindowMemory(
                k=10,
                return_messages=True,
                memory_key="history",
            )
        return self._memories[key]

    def _build_history(self, user_id) -> List[BaseMessage]:
        mem = self._get_memory(user_id)
        return mem.chat_memory.messages

    def _save_turn(self, user_id, human_text: str, ai_text: str) -> None:
        mem = self._get_memory(user_id)
        mem.chat_memory.add_user_message(human_text)
        mem.chat_memory.add_ai_message(ai_text)

    # ------------------------------------------------------------------
    # LangChain tools — замыкания, захватывающие self
    # ------------------------------------------------------------------

    def _build_tools(self, user_id, user_input: str):
        """Возвращает список LangChain @tool-функций, привязанных к контексту агента."""

        agent = self  # захват в замыкание

        @tool
        async def search_db(query: str) -> str:
            """Семантический поиск статей в базе знаний по смыслу запроса."""
            result = await tool_search_db(agent.vector_db, query, agent.clean_markdown)
            if result["status"] == "warning" or result["count"] == 0:
                return "По запросу ничего не найдено. Возможно, векторная база ещё не построена."
            if result["status"] == "critical":
                return "Модель векторизации недоступна. Проблема со стороны cloud.ru"
            lines = [f"Найдено {result['count']} результатов:\n"]
            for item in result["preview"]:
                lines.append(f"[{item['rank']}] (score={item['score']:.3f})\n{item['snippet']}\n")
            return "\n".join(lines)

        @tool
        async def analyze_folder() -> str:
            """Анализ директории: сколько статей, какие изменения (новые, изменённые, удалённые)."""
            result = await tool_analyze_folder(agent.monitor, agent.articles_path)
            changes = result["changes"]
            return (
                f"Всего файлов: {result['total_files']}\n"
                f"Новых: {result['new_files']}\n"
                f"Изменённых: {result['modified_files']}\n"
                f"Удалённых: {len(changes.get('deleted', []))}\n"
                f"Примеры файлов: {', '.join(result['files']) or '—'}"
            )

        @tool
        async def delete_files(files: List[str]) -> str:
            """Удалить указанные файлы из векторной базы знаний."""
            result = await tool_delete_file(agent.vector_db, files)
            return result.get("message", "Готово.")

        @tool
        async def summarize_file(filename: str) -> str:
            """Создать ревью (summary) для одного конкретного файла по его имени."""
            result = await tool_summarize_file(
                filename, agent.articles_path, agent.name_meta_dir,
                agent.summarizer, agent.obs_loader,
            )
            if result["status"] == "error":
                return f"Не удалось найти или обработать файл: {filename}"
            status_label = "создано" if result["status"] == "created" else "уже существовало"
            return (
                f"Ревью {status_label}.\n"
                f"Файл: {result['filename']}\n"
                f"Ссылка: {result['download_url']}"
            )

        @tool
        async def summarize_all_articles() -> str:
            """Создать ревью для ВСЕХ статей, у которых его ещё нет."""
            result = await tool_summarize_all(
                agent.articles_path, agent.name_meta_dir,
                agent.digest_path, agent.summarizer, agent.obs_loader,
            )
            diff = result["articles_count"] - result["reviews_count"]
            if diff == 0:
                return (
                    f"Всё синхронизировано. Статей: {result['articles_count']}, "
                    f"ревью: {result['reviews_count']}."
                )
            return (
                f"Статей: {result['articles_count']}, ревью: {result['reviews_count']}, "
                f"новых обработано: {result['new_reviews']}. "
                f"Разница: {abs(diff)} — проверьте {result['obs_path']}."
            )

        @tool
        async def summarize_new_articles() -> str:
            """Обработать только новые статьи (появившиеся после последней синхронизации)."""
            result = await tool_summarize_new(agent.auto_processor, agent.obs_loader)
            new = result["results"].get("new", [])
            deleted = result["results"].get("deleted", [])
            vectors = result["results"].get("vectors_built", 0)
            return (
                f"Обработано новых: {len(new)}, удалено: {len(deleted)}, "
                f"векторов построено: {vectors}."
            )

        @tool
        async def summarize_by_filter(filter_query: str) -> str:
            """Сформировать дайджест с фильтрацией по дате или категории/теме. 
            filter_query — пользовательский запрос с условием, например 'за июнь 2025' или 'по теме аналитика'."""
            logging.info(f"Пользовательский запрос {filter_query}")
            result = await tool_summarize_filt(
                agent.gigachat, filter_query,
                agent.name_meta_dir, agent.digest_path, agent.obs_loader,
            )
            if result.get("found_articles", 0) == 0:
                return "По заданным параметрам статей не найдено. Попробуйте другой период или категорию."
            return (
                f"Дайджест готов.\n"
                f"Файл: {result['filename']}\n"
                f"Скачать: {result['download_url']}"
            )

        @tool
        async def rebuild_categories() -> str:
            """Переопределить категории статей в метаданных (перезапустить классификацию)."""
            result = await tool_rebuild_categories(
                agent.obs_loader, agent.name_meta_dir, agent.articles_path
            )
            return result.get("message", "Категории обновлены.")

        @tool
        async def send_digest_email(email_query: str) -> str:
            """Сформировать дайджест по всем статьям и отправить на email. 
            email_query — запрос пользователя с указанием адреса или пожеланий."""
            result = await tool_send_digest_email(
                agent.email_handler, email_query, agent.gigachat,
                agent.obs_loader, agent.name_meta_dir, agent.digest_path,
            )
            return result.get("message", "Дайджест отправлен.")

        @tool
        async def make_basket_reviews() -> str:
            """Создать ревью для статей из корзины избранного пользователя."""
            result = await tool_make_reviews_basket(agent.db, user_id, agent.gigachat)
            if result.get("basket_empty"):
                return "Корзина пуста. Добавьте статьи в избранное, чтобы получить ревью."
            if result.get("all_done"):
                return f"Ревью уже готовы для всех {result['total']} статей в корзине."
            processed = result.get("processed", 0)
            total = result.get("total", 0)
            return (
                f"Готово! Обработано {processed} из {total} статей. "
                f"Теперь можно сформировать дайджест по корзине."
            )

        @tool
        async def digest_basket(digest_query: str) -> str:
            """Сформировать единый дайджест по корзине избранных статей пользователя.
            digest_query — пользовательский запрос с пожеланиями к дайджесту."""
            result = await tool_digest_basket(db=agent.db, user_id=user_id, user_query=digest_query)
            if result["status"] == "error":
                return "Не удалось сформировать дайджест. Убедитесь, что у статей есть ревью."
            return (
                f"Дайджест готов!\n"
                f"Файл: {result['file_name']}\n"
                f"Посмотреть: {result['download_url']}"
            )

        return [
            search_db,
            analyze_folder,
            delete_files,
            summarize_file,
            summarize_all_articles,
            summarize_new_articles,
            summarize_by_filter,
            rebuild_categories,
            send_digest_email,
            make_basket_reviews,
            digest_basket,
        ]

    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

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
            logger.debug(f"Не удалось отправить статус: {e}")

    async def get_state(self) -> str:
        changes = await asyncio.to_thread(self.monitor.get_changes)
        return (
            f"новых={len(changes['new'])}, "
            f"изменённых={len(changes['modified'])}, "
            f"удалённых={len(changes['deleted'])}"
        )

    # ------------------------------------------------------------------
    # Основной цикл: ReAct-style через bind_tools + рекурсивный вызов
    # ------------------------------------------------------------------

    async def process_query(
        self,
        user_input: str,
        request_id: str,
        user_id: int,
        database,
    ) -> Dict[str, Any]:
        if not self.db:
            self.db = database

        await self._send_status("🌞 Думаю...", request_id)

        tools = self._build_tools(user_id, user_input)
        tools_by_name = {t.name: t for t in tools}

        # Привязываем инструменты к модели
        llm_with_tools = self.gigachat.bind_tools(tools)

        state = await self.get_state()
        current_datetime = datetime.now().strftime("%d.%m.%Y %H:%M, %A")
        system_content = SYSTEM_PROMPT.format(datetime=current_datetime, state=state)

        # Собираем историю из памяти
        history = self._build_history(user_id)
        messages: List[BaseMessage] = [
            SystemMessage(content=system_content),
            *history,
            HumanMessage(content=user_input),
        ]

        final_response = ""
        used_tools = []
        max_iterations = 6  # защита от бесконечного цикла

        for iteration in range(max_iterations):
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            # Если нет вызовов инструментов — финальный ответ
            if not getattr(response, "tool_calls", None):
                final_response = response.content
                break

            # Выполняем все запрошенные инструменты параллельно
            tool_calls = response.tool_calls
            await self._send_status(
                f"🔧 Выполняю: {', '.join(tc['name'] for tc in tool_calls)}",
                request_id,
            )

            tool_results = await asyncio.gather(
                *[self._invoke_tool(tools_by_name, tc) for tc in tool_calls],
                return_exceptions=True,
            )

            for tc, tr in zip(tool_calls, tool_results):
                tool_name = tc["name"]
                used_tools.append(tool_name)
                content = str(tr) if not isinstance(tr, Exception) else f"Ошибка инструмента: {tr}"
                from langchain_core.messages import ToolMessage
                messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))

        else:
            # Превышен лимит итераций
            final_response = "Достигнут лимит шагов. Попробуйте переформулировать запрос."

        # Сохраняем пару в память
        self._save_turn(user_id, user_input, final_response)

        return {
            "input": user_input,
            "message": final_response,
            "used_tools": used_tools,
            "action": used_tools[0] if len(used_tools)> 0 else '',
            "success": True,
        }

    async def _invoke_tool(self, tools_by_name: dict, tool_call: dict) -> str:
        """Вызвать один инструмент по его имени и аргументам."""
        name = tool_call["name"]
        args = tool_call.get("args", {})
        tool_fn = tools_by_name.get(name)
        if tool_fn is None:
            return f"Инструмент '{name}' не найден."
        try:
            result = await tool_fn.ainvoke(args)
            return str(result)
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}")
            return f"Ошибка при выполнении {name}: {e}"

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

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

    def pluralize_vectors(self, n: int) -> str:
        if n % 10 == 1 and n % 100 != 11:
            return "вектор"
        elif 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
            return "вектора"
        return "векторов"

    async def auto_categories(self) -> str:
        await self.obs_loader.download_metadata()
        base_dir = Path(__file__).resolve().parent
        json_file = base_dir / "knowledge_base" / "metadata.json"
        updater = MetadataUpdater(
            metadata_file=json_file,
            articles_path=self.articles_path,
            max_retries=1,
            retry_delay=30,
        )
        message = await updater.run()

        with open(json_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        self._delete_irrelevant_md_local(metadata)
        await self.obs_loader.delete_irrelevant_md(metadata)
        await self.obs_loader.put_json()
        return message

    def _delete_irrelevant_md_local(self, metadata: list):
        deleted = 0
        for item in metadata:
            if item.get("is_relevant") or item.get("category") != "Не релевантна PM":
                continue
            file_article = item.get("file_article", "")
            if not file_article or not file_article.endswith(".md"):
                continue
            local_path = self.articles_path / file_article
            if local_path.exists():
                local_path.unlink()
                deleted += 1
        logger.info(f"Локально удалено {deleted} нерелевантных .md файлов.")

    async def make_reviews(self) -> None:
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

        new_digest_files = [
            r["digest_file"] for r in results
            if r["status"] == "created" and r.get("digest_file")
        ]
        if new_digest_files:
            email_result = await asyncio.to_thread(
                self.email_handler.send_auto_digest, new_digest_files, self.obs_loader
            )
            logger.info(f"Результат автоотправки: {email_result}")

        changes = await asyncio.to_thread(self.monitor.get_changes)
        deleted = changes.get("deleted", [])
        if deleted:
            await asyncio.to_thread(self.vector_db.delete_documents, deleted)

        count = await asyncio.to_thread(self.vector_db.rebuild_index)
        word = self.pluralize_vectors(count)
        if count == 0:
            logger.warning("Не удалось создать векторный индекс")
        else:
            logger.info(f"Векторный индекс построен: {count} {word}.")