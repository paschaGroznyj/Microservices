import asyncio
import logging
import json
import contextvars
import os
import re
import ast
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain.tools import tool
from langchain_core.messages import BaseMessage
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import ToolMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from .core.agent_provider import (get_llm_lite_model, get_llm_model,
get_llm_model_gpt, get_llm_model_claude, get_llm_model_gemini,
get_llm_lite_model_gpt_mini, get_llm_lite_model_gemini_flash)
from .core.botocore_client import OBSWorker
from .core.config import (
    ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH,
    METADATA_FILE, PORT_GATEWAY_PORT, PORT_GATEWAY_SIGMA_PORT, ADMINS_INFO,
    PORT_PARSERS_SERVICE, get_role
)
from .core.settings import TOOLS_SETTINGS, UNIVERSAL_TOOLS
from .prompts.summary_prompt import SUMMARIZE_CUT_PROMPT
from .prompts.system_prompt import SYSTEM_PROMPT, SCIENTIFIC_RESEARCH_MODULE
from .title_generator import generate_chat_title, is_first_turn

from datetime import datetime
from .digest_email_handler import DigestEmailHandler
from .monitor import StorageMonitor
from .summary import SummaryGenerator
from .summary_period import agent_composite_digest_tool
from .update_metadata import MetadataUpdater
from .vector_db import VectorDBManager
from .download_pdf_and_make import download_and_parse_pdf, generate_review
from .favorites_digest import build_user_favorites_review_digest_from_query
from .tool_status_messages import get_tool_status, get_thinking_status, get_tools_status
from .tools.sessions.tool_request_extension import make_extension_tool
from .tools.subAgents.make_subagent import make_subagent_tool
from .tools.journal.tool_agent_journal import make_journal_tool
from .judge import judge_final_answer
from .supervisor import run_supervisor
from .context_compactor import compact_messages, TOKEN_SOFT_LIMIT
import hashlib

GATEWAY_URL = f"http://api_gateway:{PORT_GATEWAY_PORT}"
GATEWAY_SIGMA_URL = f"http://api_gateway_sigma:{PORT_GATEWAY_SIGMA_PORT}"

# Redis-канал для стриминга чанков на sigma-гейтвей (у sigma нет WS, стрим льётся через pub/sub).
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

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
    database, rebuild_errors: bool = True,
    ) -> Dict[str, Any]:
        updater = MetadataUpdater(
            database=database, articles_path=article_path, max_retries=1, retry_delay=30
        )
        message = await updater.run(rebuild_errors)
        # await obs_loader.put_json()
        return {"status": "success", "message": message}


# async def tool_send_digest_email(
#     email_handler: DigestEmailHandler,
#     user_input: str,
#     llm,
#     obs_loader: OBSWorker,
#     name_meta_dir: Path,
#     digest_path: Path,
#     database
# ) -> Dict[str, Any]:
#     return await email_handler.execute(
#         user_input=user_input,
#         llm=llm,
#         obs_loader=obs_loader,
#         name_meta_dir=name_meta_dir,
#         digest_path=digest_path,
#         database=database
#     )

async def tool_summarize_all(
    articles_path: Path,
    name_meta_dir: Path,
    digest_path: Path,
    summarizer: SummaryGenerator,
    obs_loader: OBSWorker,
    database
) -> Dict[str, Any]:
    # json_file = name_meta_dir / "metadata.json"

    # def _load():
    #     with open(json_file, "r", encoding="utf-8") as f:
    #         return json.load(f)

    articles_data = await database.get_metadata_as_json()
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
    logger.info(f"Нужно ревью для {need_reviews} статей")
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
    llm,
    user_input: str,
    name_meta_dir: Path,
    digest_path: Path,
    obs_loader: OBSWorker,
    database,
) -> Dict[str, Any]:
    # Явно передаем query первыми или всеми ключевыми аргументами
    results = await agent_composite_digest_tool(
        query=user_input,
        llm_client=llm,
        database=database,
        md_folder=digest_path,
    )

    await obs_loader.upload_single_digest_cond(
        results["digest_file"], results["filename"]
    )
    base_url = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"
    return {
        "status": "success",
        "found_articles": results["found_articles"],
        "filename": results["filename"],
        "download_url": f"{base_url}{results['filename']}",
        "without_review": results.get("without_review", 0),
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


async def process_single_article_safe(
    article: dict, db, user_id: int, llm,
    obs_loader: Optional[OBSWorker] = None,
    articles_path: Optional[Path] = None,
) -> bool:
    article_id = article["article_id"]
    pdf_url = article["pdf_url"]
    title = article["title"]
    try:
        text_content = await download_and_parse_pdf(pdf_url)
        if not text_content or len(text_content) < 50:
            raise ValueError("Недостаточно текста для анализа")
        review_text = await generate_review(llm, text_content, title, pdf_url)
        await db.save_review(user_id, article_id, review_text)

        # Сохраняем ревью как .md и загружаем в OBS
        if obs_loader and articles_path:
            try:
                import hashlib
                url_hash = hashlib.sha256(title.encode()).hexdigest()[:12]
                date_sub = article.get("date_sub", datetime.now().strftime("%Y-%m-%d"))
                md_name = f"{date_sub}_{url_hash}.md"
                md_path = articles_path / md_name
                articles_path.mkdir(parents=True, exist_ok=True)

                md_content = f"# {title}\n\n**Источник:** {pdf_url}\n\n{review_text}"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                logger.info(f"Review MD saved: {md_path.name}")

                await obs_loader.upload_all_md()
                logger.info(f"Review MD uploaded to OBS: articles/{md_name}")

                # Сохраняем имя файла в статье для последующего использования
                if "file_article" not in article or not article["file_article"]:
                    article["file_article"] = md_name
            except Exception as obs_err:
                logger.warning(f"OBS upload failed for {article_id}: {obs_err}")

        return True
    except Exception as e:
        logger.error(f"Failed to process article {article_id} ({title}): {e}")
        return False


async def tool_make_reviews_basket(
    db, user_id, llm_client,
    obs_loader: Optional[OBSWorker] = None,
    articles_path: Optional[Path] = None,
) -> Dict[str, Any]:
    rows = await db.get_reviews(user_id)
    articles_data = [dict(row) for row in rows]
    articles_to_process = [a for a in articles_data if not a.get("review_text")]

    if not articles_data:
        return {"status": "success", "basket_empty": True, "total": 0}
    if not articles_to_process:
        return {"status": "success", "basket_empty": False, "all_done": True, "total": len(articles_data)}

    tasks = [
        process_single_article_safe(
            article=a, db=db, user_id=user_id, llm=llm_client,
            obs_loader=obs_loader, articles_path=articles_path,
        )
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


async def tool_get_schema(database) -> str:
    query = """
        SELECT 
            c.table_name,
            pg_catalog.obj_description(c.table_name::regclass, 'pg_class') AS table_comment,
            c.column_name,
            c.data_type,
            pg_catalog.col_description(c.table_name::regclass, c.ordinal_position) AS column_comment,
            
            -- Права текущей роли на уровне таблицы
            has_table_privilege('knowledge_manager', c.table_name, 'SELECT') AS can_select,
            has_table_privilege('knowledge_manager', c.table_name, 'INSERT') AS can_insert,
            has_table_privilege('knowledge_manager', c.table_name, 'UPDATE') AS can_update,
            has_table_privilege('knowledge_manager', c.table_name, 'DELETE') AS can_delete

        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
        AND c.table_name IN (
            'knowledge', 'knowledge_review',
            'papers_metadata', 'users', 'chats', 
            'reviews', 'agent_journal', 'session_summaries',
            'entity_relations', 'wiki_pages', 'knowledge_log'
        )
        ORDER BY c.table_name, c.ordinal_position;
    """
    
    rows = await database.pool.fetch(query)
    
    if not rows:
        return "Схема пуста или нет доступа к указанным таблицам."

    # Группируем колонки по таблицам
    tables = {}
    for r in rows:
        t_name = r["table_name"]
        if t_name not in tables:
            # Собираем список доступных прав списком
            perms = []
            if r["can_select"]: perms.append("SELECT")
            if r["can_insert"]: perms.append("INSERT")
            if r["can_update"]: perms.append("UPDATE")
            if r["can_delete"]: perms.append("DELETE")
            
            tables[t_name] = {
                "comment": r["table_comment"],
                "permissions": ", ".join(perms) if perms else "NO ACCESS",
                "columns": []
            }

        tables[t_name]["columns"].append({
            "name": r["column_name"],
            "type": r["data_type"],
            "comment": r["column_comment"]
        })

    # Формируем читаемый вывод для модели
    output = []
    for t_name, meta in tables.items():
        output.append(f"### Таблица: `{t_name}`")
        output.append(f"• Описание: {meta['comment']}")
        output.append(f"• Права роли ({'CURRENT_USER'}): **[{meta['permissions']}]**")
        output.append("• Колонки:")
        
        for col in meta["columns"]:
            comment_str = f" — {col['comment']}" if col['comment'] else ""
            output.append(f"  - `{col['name']}` ({col['type']}){comment_str}")
        
        output.append("\n" + "─" * 40 + "\n")

    return "\n".join(output)

import sqlparse

FORBIDDEN_KEYWORDS = {
    "insert", "update", "delete", "drop", "alter", "truncate",
    "grant", "revoke", "create", "copy", "call", "do", "execute",
    "vacuum", "reindex", "attach", "into",  # SELECT ... INTO тоже блокируем
}

def _validate_select_only(sql: str) -> Optional[str]:
    parsed = sqlparse.parse(sql)
    if len(parsed) != 1:
        return "Разрешён только один SQL-оператор за раз."
    stmt = parsed[0]
    if stmt.get_type() != "SELECT":
        return "Разрешены только SELECT-запросы."
    tokens_lower = sql.lower()
    # for kw in FORBIDDEN_KEYWORDS:
    #     if re.search(rf"\b{kw}\b", tokens_lower):
    #         return f"Обнаружено запрещённое ключевое слово: {kw}"
    if ";" in sql.strip().rstrip(";"):
        return "Множественные запросы через ';' запрещены."
    return None


async def tool_execute_sql_query(database, sql: str, max_rows: int = 10, sql_query_admin=True) -> Dict[str, Any]:
    # Очищаем запрос от лишних пробелов и точек с запятой в конце
    clean_sql = sql.strip().rstrip(";")
    first_word = clean_sql.split()[0].upper() if clean_sql else ""
    if sql_query_admin:
        database_pool = database.readonly_pool
    else:
        database_pool = database.base_agent_pool
    # Добавляем LIMIT только для SELECT запросов, если его там нет
    if first_word == "SELECT" and not re.search(r"\blimit\b", clean_sql, re.IGNORECASE):
        clean_sql += f" LIMIT {max_rows}"

    try:
        async with database_pool.acquire() as conn:
            if first_word == "SELECT" or "RETURNING" in clean_sql.upper():
                rows = await asyncio.wait_for(conn.fetch(clean_sql), timeout=10)
                return {
                    "status": "success",
                    "rows": [dict(r) for r in rows],
                    "count": len(rows),
                }
            else:
                # Для UPDATE, INSERT, DELETE используем execute()
                status_str = await asyncio.wait_for(conn.execute(clean_sql), timeout=10)
                
                # Парсим количество измененных строк (например, "UPDATE 1")
                affected = 0
                if status_str and len(status_str.split()) > 1:
                    try:
                        affected = int(status_str.split()[-1])
                    except ValueError:
                        affected = 0

                return {
                    "status": "success",
                    "rows": None,
                    "affected_rows": affected,
                    "count": 0
                }

    except asyncio.TimeoutError:
        return {"status": "error", "message": "Запрос выполнялся слишком долго (timeout)."}
    except Exception as e:
        return {"status": "error", "message": f"Ошибка выполнения запроса: {e}"}

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
MAX_STDOUT_CHARS = 2000  # Настройте максимальную длину (например, 2000 символов)

# Предохранитель против жадных тулов (Вариант C, часть A): максимальный размер
# тела ЛЮБОГО ToolMessage перед добавлением в стек агента. Один вызов, вернувший
# десятки тысяч символов (shell/analyze_image/search_internet), иначе раздувает
# input_tokens так, что реактивный компактор не успевает (свежая пара в keep_last).
# ~8000 символов ≈ 2.5-3k токенов — достаточно чтобы модель "увидела" суть.
MAX_TOOL_MSG_CHARS = int(os.getenv("MAX_TOOL_MSG_CHARS", "8000"))

def _truncate_text(text: str, max_chars: int = MAX_STDOUT_CHARS) -> str:
    """Обрезает текст, если он превышает лимит, добавляя плашку с указанием перерасхода."""
    if not isinstance(text, str) or len(text) <= max_chars:
        return text
    
    truncated_count = len(text) - max_chars
    # Берем начало и конец вывода для сохранения контекста (лог/ошибка часто в конце)
    head_len = max_chars // 2
    tail_len = max_chars // 2
    
    return f"{text[:head_len]}\n\n... [вывод обрезан, пропущено {truncated_count} символов] ...\n\n{text[-tail_len:]}"


# Тулы, чьи вызовы стоит запоминать в истории диалога
STATEFUL_TOOLS = {
    "execute_sql_query",
    "download_text_articles",
    "upload_articles",
    "generate_reviews_for_articles",
    "summarize_file",
    "summarize_all_articles",
    "summarize_new_articles",
    "rebuild_categories",
    "make_basket_reviews",
    "digest_basket",
    # "send_digest_email",
    # "research_prompt",
    "search_internet",
    "parse_web_page",
    "generate_image",
    "find_generated_image",
    "analyze_image",
    "list_tree",
    "read_file",
    "grep_search",
    "find_files",
    "write_sandbox_file",
    "edit_sandbox_file",
    "execute_python_code",
    "send_file_to_user",
    "work_with_agent_journal",
    "request_more_steps",
    "submit_task", "check_task_status",
    "create_workspace", "list_workspace_files", "download_workspace_file",
    "delete_workspace", "cleanup_workspaces",
    "call_subagent", "execute_shell_commands",
    "upload_to_workspace",

    "read_workspace_file",
    "edit_workspace_file",
}

def _summarize_tool_call(tc: dict, result: Any) -> str | None:
    name = tc["name"]
    if name not in STATEFUL_TOOLS:
        return None  # не попадает в trace вообще

    args = tc["args"]
    if name == "execute_sql_query":
        return f"execute_sql_query('{args.get('sql', '')[:500]}...') результат: {str(result)[:500]}"
    # if name == "send_digest_email":
    #     return f"send_digest_email(to={args.get('email', '?')}) → отправлено"
    # if name == "delete_files":
    #     return f"delete_files({args.get('files', args)}) → удалено"
    if name in ("download_text_articles", "upload_articles"):
        return f"{name}({args}) → выполнено"
    if name in ("summarize_file", "generate_reviews_for_articles",
                "summarize_all_articles", "summarize_new_articles"):
        return f"{name}({args}) → ревью создано"
    if name == "rebuild_categories":
        return "rebuild_categories() → категории пересчитаны"
    if name in ("make_basket_reviews", "digest_basket"):
        return f"{name}() → дайджест сформирован"
    if name == "search_internet":
        # результат — многострочный текст с [S1]...[S5], вытащим только заголовки+URL
        lines = str(result).split("\n")
        titles = [l for l in lines if l.startswith("[S")]
        compact = " | ".join(titles)
        return f"search_internet(query='{args.get('query')}') → {compact}"
    if name == "parse_web_page":
        return f"{name} Передаваемые ссылки в парсер ({args}). -> {str(result)[:1000]}"
    if name == "generate_image":
        # КРИТИЧНО для памяти: в trace должны осесть промпт и результат
        # (пути + id записей в generated_images), иначе в следующем ходе агент
        # не вспомнит, что и по какому описанию он нарисовал, и куда картинка легла.
        gi_prompt = str(args.get("prompt", ""))[:300]
        gi_aspect = args.get("aspect_ratio", "16:9")
        return (
            f"generate_image(prompt='{gi_prompt}', aspect_ratio={gi_aspect}, "
            f"n={args.get('n', 1)}) →\n{str(result)[:1200]}"
        )
    if name == "find_generated_image":
        return (
            f"find_generated_image(query={args.get('query')!r}, "
            f"file_path={args.get('file_path')!r}) →\n{str(result)[:1200]}"
        )
    if name == "analyze_image":
        # Запоминаем, что именно спрашивали у vision-модели и её вывод —
        # чтобы в дальнейшем диалоге агент помнил содержимое картинки.
        ai_prompt = str(args.get("prompt", ""))[:300]
        return (
            f"analyze_image(prompt='{ai_prompt}', "
            f"image_index={args.get('image_index', 0)}) →\n{str(result)[:1200]}"
        )
    if name == "call_subagent":
        return f"{name} ({args}). ОТВЕТ СУБАГЕНТА - {str(result)}"
    if name in ("list_tree", "read_file", "grep_search", "find_files", 
                "write_sandbox_file", "edit_sandbox_file", "send_file_to_user",
                "work_with_agent_journal", "read_workspace_file", "edit_workspace_file"):
        return f"{name}({args}) → {str(result)[:1000]}"
    if name == "execute_python_code":
        result_dict = result if isinstance(result, dict) else {}
        ok = result_dict.get("ok", False)

        if ok:
            # Успех — код уже лежит в saved_script_path, дублировать не нужно.
            # В трейс идёт только компактная сводка результата.
            saved_files = result_dict.get("saved_files", [])
            files_note = f", файлы: {[f['filename'] for f in saved_files]}" if saved_files else ""
            script_path = result_dict.get("saved_script_path")
            script_note = f", скрипт: {script_path}" if script_path else ""
            return f"execute_python_code() [успех]{files_note}{script_note}"
        else:
            # Ошибка — код НЕ сохраняется в executed_scripts (see main.py логика),
            # значит единственный способ его увидеть в будущем — сохранить в trace,
            # иначе агент забудет, что именно он написал и почему упало.
            code = result_dict.get("code", args.get("code", ""))
            error = result_dict.get("error") or result_dict.get("stderr", "")[:300]
            return (
                f"execute_python_code() [ОШИБКА]\n"
                f"код:\n{code[:800]}\n"
                f"ошибка: {error}"
            )
    if name == "request_more_steps":
        return f"{name} ШАГ-БУСТ → {str(result)}"
    # if name == "research_prompt":
    #     query = args.get("query", args.get("topic", ""))
    #     return f"research_prompt('{query}') → найдены источники"
    return f"{name}({args} -> {result[:1000]})"


async def _invoke_with_retry(llm, messages, iteration: int, max_retries: int = 3):
    backoff = [2, 5, 10]
    last_error = None
    for attempt in range(max_retries):
        try:
            return await llm.ainvoke(messages)
        except Exception as e:
            last_error = e
            logging.warning(
                f"[iteration={iteration}] ainvoke попытка {attempt + 1}/{max_retries} упала: {type(e).__name__}: {e}"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff[attempt])
    logging.error(f"[iteration={iteration}] Все {max_retries} попытки провалены: {last_error}")
    return None


def _sanitize_messages(messages):
    """Убирает human/tool сообщения с пустым content перед отправкой в LLM.

    Провайдер (cloud.ru) отбивает весь запрос с 400 "messages.N: user messages
    must have non-empty content", если у любого human/tool-сообщения content
    пустой. AIMessage с tool_calls и пустым content — валиден (мы сами
    гасим response.content при reasoning), его НЕ трогаем. SystemMessage тоже
    оставляем всегда.
    """
    out = []
    for m in messages:
        mtype = getattr(m, "type", None)
        if mtype in ("human", "tool"):
            c = getattr(m, "content", None)
            if c is None or (isinstance(c, str) and not c.strip()):
                continue
        out.append(m)
    return out


async def _astream_collect(llm, messages, on_chunk=None, iteration: int = 0, error_info: dict | None = None):
    """Стриминговая версия _invoke_with_retry.

    Собирает ответ через llm.astream(), аккумулируя AIMessageChunk сложением (+=).
    Финальный AIMessage идентичен тому, что вернул бы ainvoke: content, tool_calls,
    usage_metadata — всё на месте.

    on_chunk(text) — опциональный коллбэк на каждый текстовый чанк. Вызывается ТОЛЬКО
    для чистого текста (reasoning/финал). При tool_call-чанках content пустой,
    поэтому on_chunk для них не сработает.

    Стратегия отказоустойчивости: если astream упал — фолбэк на обычный ainvoke.
    """
    messages = _sanitize_messages(messages)
    backoff = [2, 5, 10]
    last_error = None
    for attempt in range(3):
        try:
            aggregate = None
            async for chunk in llm.astream(messages):
                aggregate = chunk if aggregate is None else aggregate + chunk

                # --- reasoning-канал (gpt → reasoning, gemini → reasoning_content) ---
                ak = getattr(chunk, "additional_kwargs", {}) or {}
                reasoning = ak.get("reasoning_content") or ak.get("reasoning")
                if reasoning and on_chunk is not None:
                    await on_chunk(reasoning, kind="reasoning")

                # --- content-канал ---
                raw = getattr(chunk, "content", None)
                if isinstance(raw, list):
                    # claude: content приходит списком блоков thinking/text
                    for block in raw:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "thinking" and on_chunk is not None:
                            t = block.get("thinking", "")
                            if t:
                                await on_chunk(t, kind="reasoning")
                        elif btype == "text" and on_chunk is not None:
                            t = block.get("text", "")
                            if t:
                                await on_chunk(t, kind="answer")
                elif raw and on_chunk is not None:
                    await on_chunk(raw, kind="answer")
            if aggregate is not None:
                return aggregate
            # пустой стрим — уходим в фолбэк ainvoke
            return await llm.ainvoke(messages)
        except Exception as e:
            last_error = e
            logging.warning(
                f"[iteration={iteration}] astream попытка {attempt + 1}/3 упала: "
                f"{type(e).__name__}: {e}"
            )
            if attempt < 2:
                await asyncio.sleep(backoff[attempt])
    # Все стриминговые попытки провалены — последний шанс через ainvoke без стрима
    logging.error(f"[iteration={iteration}] astream провален, фолбэк на ainvoke: {last_error}")
    try:
        return await llm.ainvoke(messages)
    except Exception as e:
        logging.error(f"[iteration={iteration}] ainvoke-фолбэк тоже упал: {e}")
        if error_info is not None:
            error_info["reason"] = f"{type(e).__name__}: {e}"
        return None

CODE_TOOLS = {"execute_python_code", "write_sandbox_file", "edit_sandbox_file"}
MAX_ARG_LEN   = 1200   # макс. символов для обычного аргумента
MAX_CODE_LEN  = 800    # макс. символов для аргумента-кода
MAX_RESULT_LEN = 200  # макс. символов результата в лог


def _fmt_args(name: str, args: dict) -> str:
    """Форматирует аргументы для лога — код сокращает."""
    parts = []
    for k, v in args.items():
        s = str(v)
        # Если тулза кодовая и аргумент похож на код — обрезаем жёстче
        is_code = name in CODE_TOOLS or k in ("code", "content", "new_str", "old_str")
        limit = MAX_CODE_LEN if is_code else MAX_ARG_LEN
        if len(s) > limit:
            s = s[:limit].rstrip() + f"…[+{len(s)-limit}]"
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)

def _normalize(t):
    """Превращает tool или list[tool] в list."""
    return t if isinstance(t, list) else [t]


# SYSTEM_PROMPT и SCIENTIFIC_RESEARCH_MODULE вынесены в prompts/system_prompt.py


class KnowledgeAgent:
    """PMыч"""
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
        self.AGENT_MODELS = {'coder': get_llm_model_gpt(),
                            'designer': get_llm_model_claude(),
                            'universal': get_llm_model_gemini()
                            }
        self.llm_lite = get_llm_lite_model()
        # Цепочка фолбэков для лайт-модели (суммаризация и прочие служебные
        # вызовы, летящие с фронта): по умолчанию haiku, при отказе —
        # gpt-5-mini, затем gemini-3-flash-preview.
        self.llm_lite_fallbacks = [
            get_llm_lite_model_gpt_mini(),
            get_llm_lite_model_gemini_flash(),
        ]
        self.obs_loader = OBSWorker()
        self.email_handler = DigestEmailHandler()
        self.skip_startup_autoprocess = skip_startup_autoprocess
        self.services = {
                            "parsers": {
                                "url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",
                                }
                        }

        # Память: последние 10 сообщений (5 пар human/ai)
        self._memories: Dict[str, ConversationBufferWindowMemory] = {}
        self._memory_loaded: Dict[str, bool] = {}

        self._http_client: Optional[httpx.AsyncClient] = None

        # Ленивый Redis-клиент для стриминга чанков на sigma-гейтвей (pub/sub).
        # Поднимается при первом стриме на sigma; fail-open (Redis лёг — стрим не льётся,
        # но ход не падает: финал всё равно доедет через Kafka).
        self._redis = None
        self._redis_failed = False

        # Request-local контекст для параллельных запросов.
        # Никакого разделяемого mutable-состояния между тасками.
        self._request_ctx: contextvars.ContextVar = contextvars.ContextVar(
            "agent_request_ctx", default=None
        )

    # ------------------------------------------------------------------
    # Память per-user с загрузкой из БД через AgentDB
    # ------------------------------------------------------------------

    async def _load_history_from_db(self, chat_id: str, limit: int) -> List[BaseMessage]:
        if not self.db:
            logger.warning("БД не подключена, история не загружена")
            return []

        try:
            rows = await self.db.get_chat_history(chat_id, limit)

            messages = []
            for row in rows:
                # САНИТАЙЗЕР: провайдер (cloud.ru) отбивает запрос с 400
                # "messages.N: user messages must have non-empty content", если в любом
                # сообщении content пустой. Такие строки попадают в БД от
                # проактивных/телеграм-триггеров (initiated с message="").
                # Дропаем пустые сообщения, чтобы не ломать весь цикл.
                _txt = row.get("message_text")
                if _txt is None or not str(_txt).strip():
                    continue
                kwargs = {"db_id": row["id"]}
                if row["is_from_bot"]:
                    # РАЗДЕЛЕНИЕ СООБЩЕНИЙ И TOOL TRACE.
                    # В content кладём ТОЛЬКО чистую реплику агента (message_text).
                    # tool_trace НЕ приклеиваем к content — иначе на длинных задачах
                    # трейс раздувает каждый AIMessage до тысяч токенов, trim_messages
                    # выкидывает целые пары human/ai, и реальная память по диалогу
                    # схлопывается до 1-2 ходов. Трейс кладём отдельно в
                    # additional_kwargs["tool_trace"] — он НЕ участвует в подсчёте
                    # токенов основного окна (в content его нет), но доступен при
                    # сжатии (_summarize_cut_messages), чтобы факты из трейса
                    # (пути, workspace_id, ссылки) не терялись, а уходили в rolling summary.
                    if row.get("tool_trace"):
                        kwargs["tool_trace"] = row["tool_trace"]
                    messages.append(AIMessage(content=row["message_text"], additional_kwargs=kwargs))
                else:
                    messages.append(HumanMessage(content=row["message_text"], additional_kwargs=kwargs))

            logger.info(f"📚 Загружено {len(messages)} сообщений из БД для chat={chat_id}")
            return messages

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки истории из БД: {e}")
            return []

    def _trim_tool_pairs(
        self,
        messages: list,
        keep_last: int = 3,
    ) -> list:
        """
        Оставляет только keep_last последних пар AIMessage+ToolMessage.
        Старые пары удаляет из стека (не заменяет заглушкой — просто дропает).
        SystemMessage и HumanMessage не трогает.
        """
        # Находим индексы всех AIMessage с tool_calls
        ai_tool_indices = [
            i for i, m in enumerate(messages)
            if hasattr(m, "tool_calls") and m.tool_calls
        ]

        if len(ai_tool_indices) <= keep_last:
            return messages  # ничего не делаем

        # Индексы пар которые дропаем (всё кроме последних keep_last)
        to_drop_starts = ai_tool_indices[:-keep_last]

        drop_set = set()
        for ai_idx in to_drop_starts:
            drop_set.add(ai_idx)
            # Собираем ToolMessage сразу после этого AIMessage
            j = ai_idx + 1
            while j < len(messages) and messages[j].type == "tool":
                drop_set.add(j)
                j += 1

        return [m for i, m in enumerate(messages) if i not in drop_set]

    # ------------------------------------------------------------------
    # Память per-user
    # ------------------------------------------------------------------

    async def _get_memory(self, chat_id: str, limit: int = 10) -> ConversationBufferWindowMemory:
        """Получить память для пользователя (с автозагрузкой из БД)"""
        key = f"{chat_id}"
        
        if key not in self._memories:
            self._memories[key] = ConversationBufferWindowMemory(
                k=10,
                return_messages=True,
                memory_key="history",
            )
            self._memory_loaded[key] = False
        
        # Загружаем историю из БД при первом обращении
        if not self._memory_loaded.get(key, False):
            messages = await self._load_history_from_db(chat_id, limit)
            
            # Заполняем память
            for msg in messages:
                self._memories[key].chat_memory.messages.append(msg)
            
            self._memory_loaded[key] = True
            logger.info(f"✅ Память инициализирована для {key} ({len(messages)} сообщений)")
        return self._memories[key]

    async def _build_history(self, chat_id: str, limit: int = 40) -> List[BaseMessage]:
        """
        Всегда подгружает свежий срез из БД (не растущий in-memory буфер).
        limit — верхняя граница, реальную обрезку по токенам делает trim_messages выше по стеку.
        """
        return await self._load_history_from_db(chat_id, limit)

    async def _save_turn(self, chat_id, human_text: str, ai_text: str, tool_trace: list[str] | None = None) -> None:
        mem = await self._get_memory(chat_id)
        mem.chat_memory.add_user_message(human_text)

        # A1: в in-memory буфер кладём ТОЛЬКО чистую реплику агента, без склейки
        # tool_trace в content (как и при загрузке из БД в _load_history_from_db).
        # Трейс сохраняется отдельной колонкой в chats.tool_trace через _save_message
        # и подтягивается в additional_kwargs при следующей загрузке истории.
        mem.chat_memory.add_ai_message(ai_text)


    # ------------------------------------------------------------------
    # LangChain tools — замыкания, захватывающие self
    # ------------------------------------------------------------------

    def _build_tools(
        self,
        user_id,
        chat_id,
        user_input: str,
        selected_tools: list[str] | None = None,
        allow_all: bool = False,
        images: list[dict] | None = None,
        agent_model: str | None = None,
        is_telegram: bool = False,
    ):
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
        async def summarize_all_articles() -> str:
            """Создать ревью для ВСЕХ статей, у которых его ещё нет."""
            result = await tool_summarize_all(
                agent.articles_path, agent.name_meta_dir,
                agent.digest_path, agent.summarizer, agent.obs_loader, agent.db
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
                agent.llm, filter_query,
                agent.name_meta_dir, agent.digest_path, agent.obs_loader, agent.db
            )
            if result.get("found_articles", 0) == 0:
                return "По заданным параметрам статей не найдено. Попробуйте другой период или категорию."
            return (
                f"Дайджест готов. По {result['found_articles']} статьям. Статей без ревью: {result['without_review']}\n"
                f"Файл: {result['filename']}\n"
                f"Скачать: {result['download_url']}"
            )

        @tool
        async def rebuild_categories() -> str:
            """Переопределить категории статей в метаданных (перезапустить классификацию)."""
            result = await tool_rebuild_categories(
                agent.obs_loader, agent.name_meta_dir, agent.articles_path, agent.db
            )
            return result.get("message", "Категории обновлены.")

        @tool
        async def make_basket_reviews() -> str:
            """Создать ревью для статей из корзины избранного пользователя."""
            result = await tool_make_reviews_basket(
                agent.db, user_id, agent.llm,
                obs_loader=agent.obs_loader, articles_path=agent.articles_path,
            )
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


        @tool
        async def upload_articles():
            """Загрузить статьи в объектное хранилище (OBS)"""
            await agent.obs_loader.upload_all_md()
            return (f"Статьи успешно загружены в объектное хранилище по пути articles/")

        @tool
        async def download_text_pdf(url: str):
            """
            Скачивает документ в формате PDF по URL-ссылке, конвертация в Markdown. Не сохраняет локально.
            Только получение текста.
            """
            text_content = await download_and_parse_pdf(url)
            return (f"Содержимое документа: {text_content}")
        
        @tool
        async def download_text_articles(request_id: str = ""):
            """
            Скачивает статьи в формате PDF по URL-ссылкам из базы данных,
            конвертирует их в Markdown и сохраняет локально.
            Возвращает отчет о скачанных и упавших статьях.
            """
            all_articles = await agent.db.get_metadata_as_json()

            articles = [a for a in all_articles if not a.get("pdf_downloaded")]
            total_articles = len(articles)
            logger.info(f"Статей для скачивания: {total_articles}, а всего статей {len(all_articles)}")

            success_count = 0
            failed_urls = []

            articles_path = agent.articles_path

            for idx, article in enumerate(articles, 1):
                article_id = article.get("article_id")
                pdf_url = article.get("pdf_url")
                title = article.get("title", "Untitled")
                external_id = article.get("external_id")

                if not pdf_url:
                    failed_urls.append(
                        {"title": title, "url": "N/A", "reason": "URL отсутствует"}
                    )
                    continue

                try:
                    # Скачивание и парсинг PDF
                    text_content = await download_and_parse_pdf(pdf_url)

                    # Формирование MD файла
                    url_hash = hashlib.sha256(title.encode()).hexdigest()[:12]
                    date_sub = article.get(
                        "date_sub", datetime.now().strftime("%Y-%m-%d")
                    )
                    md_name = f"{date_sub}_{url_hash}.md"
                    md_path = articles_path / md_name
                    articles_path.mkdir(parents=True, exist_ok=True)

                    md_content = f"# {title}\n\n**Источник:** {pdf_url}\n\n{text_content}"
                    with open(md_path, "w", encoding="utf-8") as f:
                        f.write(md_content)

                    if external_id: # Проставка флага в метаданных на успешное скачивание статьи на локалку
                        await agent.db.update_pdf_downloaded_status(
                            external_id, downloaded=True
                        )

                    success_count += 1
                    logger.info(f"Review MD saved: {md_path.name}")

                except Exception as e:
                    logger.error(
                        f"Ошибка обработки статьи '{title}' ({pdf_url}): {e}"
                    )
                    failed_urls.append(
                        {"title": title, "url": pdf_url, "reason": str(e)}
                    )

                # Отправка статуса каждые 5 статей (или на последней)
                if idx % 5 == 0 or idx == total_articles:
                    status_msg = f"⏳ Обработано статей: {idx}/{total_articles} (Успешно: {success_count}, Ошибок: {len(failed_urls)})"
                    await self._send_status(status_msg, request_id)

            # Формируем итоговый отчёт для контекста LLM
            report = [
                f"Завершено. Успешно обработано: {success_count} из {total_articles}."
            ]

            if failed_urls:
                report.append("\nСписок ссылок, которые не удалось скачать:")
                for item in failed_urls:
                    report.append(
                        f"- [{item['title']}]({item['url']}): {item['reason']}"
                    )

            return "\n".join(report)
        
        @tool
        async def get_db_schema() -> str:
            """Показать актуальную схему таблиц базы знаний (для составления SQL-запроса)."""
            return await tool_get_schema(agent.db)
        
        @tool
        async def execute_sql_query(sql: str) -> str:
            """
            Выполнить SQL-запрос к базе данных (SELECT, INSERT, UPDATE).
            
            КАРТА ДОСТУПА И ПРАВ (ROLE: knowledge_manager):

            ЗАКОН ПРИ РАБОТЕ С БД:
            Если SQL-запрос вернул "результатов нет" при поиске конкретных статей/фактов, 
            которые ты собирался процитировать — ОБЯЗАН сообщить пользователю: 
            "Не нашёл точных совпадений в базе для темы X, могу поискать шире / 
            дать общий обзор без привязки к конкретным ID". 
            ЗАПРЕЩЕНО подменять неудачный результат правдоподобно звучащими названиями, 
            особенно с формулировками вида 'я проверил/уточнил через SQL', 
            если фактический результат запроса был пуст или нерелевантен.

            Утверждение о сохранении данных в БД без фактического вызова execute_sql_query 
            в этом же ходу — это создание ложной информации и категорически запрещено.
            
            СТРОГИЕ ОГРАНИЧЕНИЯ:
            - Прямые операции DELETE СТРОГО ЗАПРЕЩЕНЫ на уровне прав роли.
            - Для предложения удаления записи из 'knowledge' делай INSERT в 'knowledge_review' 
            с указанием 'knowledge_id' и 'reason'.
            - Перед формированием запросов обязательно вызови 'get_db_schema' для проверки структуры.

            СТРАТЕГИЯ РАБОТЫ С ТАБЛИЦЕЙ knowledge:
            - НОВАЯ ЗАПИСЬ (INSERT): создавай всегда когда источник новый (новая статья, новый URL, новый автор).
            Одна статья = одна запись. Не объединяй разные источники в одну строку.

            - ОБНОВЛЕНИЕ (UPDATE): только если запись с этим источником уже существует
            и нужно дополнить или исправить текст. Проверяй через SELECT перед UPDATE.

            - ВСЕГДА ПРОВЕРЯЙ разницу времени между временем создания записи и её обновлением. НИКОГДА не говори
            пользователю, что запись обновлена. Если разницы нет, то сделай самостоятельное обновление. 

            - ОБЪЕДИНЕНИЕ: НЕ объединяй записи из разных источников даже если темы похожи.
            Смежность тем — не повод для слияния. Семантический поиск сам найдёт связанные записи.

            - В metadata_knowledge всегда указывай source (URL или идентификатор статьи),
            чтобы можно было отследить происхождение знания. Без явной ссылки, можешь оставить пустым.

            - ТОЧЕЧНОЕ ОБНОВЛЕНИЕ ТЕКСТА (UPDATE + REPLACE):
            Если нужно заменить только конкретную фразу/слово, а не переписывать весь текст:
            1. Сначала сделай SELECT text_knowledge WHERE id = N, чтобы усмотреть точную формулировку.
            2. Выполни подстановку через REPLACE:
                UPDATE knowledge 
                SET text_knowledge = REPLACE(text_knowledge, 'строго точный старый текст', 'новый текст'),
                    updated_at = NOW()
                WHERE id = N;

            :param sql: Валидный PostgreSQL запрос.
            """
            result = await tool_execute_sql_query(agent.db, sql)
            
            if result["status"] == "error":
                return f"Ошибка выполнения SQL: {result['message']}"
            
            # Если это был INSERT или UPDATE без RETURNING (rows отсутствует или None)
            if result.get("rows") is None:
                return f"Запрос успешно выполнен. Затронуто строк: {result.get('affected_rows', 1)}"

            if result["count"] == 0:
                return "Запрос выполнен, но результатов нет."

            preview = json.dumps(result["rows"][:20], ensure_ascii=False, default=str, indent=2)
            return f"Найдено/возвращено строк: {result['count']}\n{preview}"

        @tool
        async def research_prompt() -> str:
            """
            === РАБОТА С НАУЧНЫМИ СТАТЬЯМИ ===
            Если пользователь просит найти, проанализировать, спарсить или добавить научные статьи по Process Mining:
            1. Сначала вызови инструмент `research_prompt`.
            2. Строго следуй полученным из него инструкциям и алгоритму.
            """
            return SCIENTIFIC_RESEARCH_MODULE
        @tool
        async def no_tools_selected() -> str:
            """
            Вызывай ТОЛЬКО если тебе нужно выполнить действие, требующее инструмента,
            а ни один инструмент недоступен. Сообщи пользователю, что нужно выбрать
            хотя бы одну категорию инструментов в панели настроек (кнопка со значком
            гаечного ключа рядом с полем ввода), и объясни, какая категория подошла бы
            для его запроса. НЕ выдумывай данные и НЕ пытайся ответить по памяти —
            задача без инструментов не может быть выполнена корректно.
            """
            return (
                "⚠️ Инструменты не выбраны. Чтобы выполнить этот запрос, нужно "
                "включить хотя бы одну категорию инструментов на главной странице: "
                "нажмите на кнопку настройки инструментов (значок гаечного ключа "
                "рядом с полем ввода) и выберите подходящую категорию, затем "
                "повторите запрос. Без включённых инструментов я не могу собрать "
                "данные и не буду отвечать по памяти, чтобы не ввести вас в заблуждение."
            )

        # 1. Генерация новых ревью
        from .tools.reviews.tool_generate_reviews import make_generate_reviews_tool
        generate_reviews_for_articles = make_generate_reviews_tool(agent)
        # 2. Парсинг одиночной страницы, не сырой текст, очищенный, ссылка отправляет в сервис parsers
        from .tools.parsers.tool_parse_page import make_parse_page_tool
        parse_web_page = make_parse_page_tool(agent)
        # 3. Поиск в интернете с применением поисковика
        from .tools.parsers.web_search import make_web_search
        search_internet = make_web_search()
        # 4. Отправка дайджестов или сообщений на почту
        from .tools.send_messages.tool_send_email import make_send_digest_email_tool
        send_digest_email = make_send_digest_email_tool(agent)
        # 5. Точка эндпоинта для создания сессии start/end
        from .tools.sessions.tool_session_point import make_session_point_tool
        session_point = make_session_point_tool(agent)
        # 6. Тулзы для работы с кодовой базой проекта и песочницы. tree/grep/cat
        from .tools.filesystem.tool_read_codebase import make_filesystem_tools
        fs_tools = make_filesystem_tools(agent)
        # 7. Редактирование существующих файлов в песочницу sanbox/
        from .tools.filesystem.tool_edit_sandbox import make_sandbox_edit_tool
        edit_sandbox_file = make_sandbox_edit_tool(agent)
        # 8. Написание кода и сохранение в песочнице 
        from .tools.filesystem.tool_write_sandbox import make_sandbox_write_tool
        write_sandbox_file = make_sandbox_write_tool(agent)
        # 9. Отправка кода на исполнение в отдельный сервис. Результаты файлы, ок/не ок
        from .tools.sandbox.tool_execute_sandbox import make_sandbox_execute_tool
        execute_python_code = make_sandbox_execute_tool(agent)
        # 10. Работа с кодовой базой, вставка описания, поиск существующих скриптов
        from .tools.code_base.tool_work_with_codebase import make_work_codebase_tool
        save_or_search_code = make_work_codebase_tool(agent)
        # 11. Генерация графа (под вопросом)
        from .tools.make_graph.tool_knowledge_graph import make_knowledge_graph_tool
        knowledge_graph_tool = make_knowledge_graph_tool(agent)
        # 12. Отправка файла в телегу, на входе только название файла
        from .tools.telegram.tool_send_file import make_send_file_tool
        send_file_tool = make_send_file_tool(agent)
        # 13. Отправка сообщений в ТГ
        from .tools.telegram.tool_send_message import make_push_telegram_tool
        send_message_tool = make_push_telegram_tool(agent)
        # 14. Работа с почтой
        from .tools.email.tool_email_connect import make_send_by_email_tool
        work_with_email = make_send_by_email_tool()
        # 14b. Универсальная отправка письма (текст + вложения, лимит 20МБ)
        from .tools.email.tool_send_email_universal import make_send_email_tool
        send_email = make_send_email_tool(agent)
        # 15. Работа с удаленным репозиторием
        from .tools.git.tool_to_connect_mcp import connect_mcp_git
        # 16. Публикация задачи в планировщик-песок
        from .tools.sandbox.tool_submit_task import make_submit_tools
        submit_task = make_submit_tools(agent)
        # 17. Чтение и точечная правка файлов внутри Фабрики
        from .tools.sandbox.tool_workspace_codeedit import make_workspace_codeedit_tools
        codeedit_tools = make_workspace_codeedit_tools(agent)
        # 18. Библиотека скиллов (вынесена в tools)
        from .tools.skills.tool_manage_skills import make_manage_skills_tool
        manage_skills = make_manage_skills_tool(agent, user_id)


        # Тул анализа изображений (JPEG/PNG) через claude-opus-4.8.
        # base64 картинок захватывается в замыкание тула из images —
        # модель передаёт только промпт, base64 не идёт в контекст.
        from .tools.vision.tool_analyze_image import make_analyze_image_tool
        analyze_image = make_analyze_image_tool(agent, images, agent_model=agent_model)

        # Тул генерации изображений (gemini-3.1-flash-image-preview).
        # Пресет качества из карточки #127. Сгенерированные файлы копятся
        # в agent._pending_generated_images и авто-отправляются пользователю.
        from .tools.content.tool_generate_image import (
            make_generate_image_tool,
            make_find_generated_image_tool,
        )
        generate_image = make_generate_image_tool(agent, images)
        # Поиск ранее сгенерированных картинок БЕЗ SQL (хардкод-запросы в туле):
        # по ключевым словам из промпта или по точному пути. Так агент помнит,
        # что он рисовал, и может вернуться к картинке (image-to-image).
        find_generated_image = make_find_generated_image_tool(agent)

        base_tools = [
            search_db,
            analyze_image,
            generate_image,
            find_generated_image,
            summarize_by_filter,
            rebuild_categories,
            send_digest_email,
            make_basket_reviews,
            digest_basket,
            upload_articles,
            download_text_articles,
            get_db_schema,
            execute_sql_query,
            generate_reviews_for_articles,
            parse_web_page,
            search_internet,
            download_text_pdf,
            session_point,
            send_file_tool,
            send_message_tool,
            work_with_email,
            send_email,
            connect_mcp_git
            # knowledge_graph_tool
        ]

        all_tools = (
            base_tools
            + [manage_skills]
            + _normalize(fs_tools)
            + [edit_sandbox_file, write_sandbox_file]
            + _normalize(execute_python_code)
            + _normalize(save_or_search_code)
            + submit_task
            + codeedit_tools
        )

        if allow_all:
            return all_tools

        allowed_names: set[str] = set()
        for category in selected_tools:
            allowed_names.update(TOOLS_SETTINGS.get(category, []))

        # Универсальные тулы добавляются всегда, вне зависимости от выбранных
        # категорий — они не привязаны к режиму (напр. анализ изображений).
        allowed_names.update(UNIVERSAL_TOOLS)

        filtered = [t for t in all_tools if t.name in allowed_names]

        # Telegram: жёстко режем админские тулы даже если категория выбрана
        # в настройках чата (execute_sql_query / get_db_schema / shell).
        # Настройки TG задаёт /start в самой группе — админку не отдаём наружу.
        if is_telegram:
            TG_FORBIDDEN = {"execute_sql_query", "get_db_schema", "execute_shell_commands"}
            filtered = [t for t in filtered if t.name not in TG_FORBIDDEN]

        # Даже если пользователь не выбрал ни одной категории, универсальные
        # тулы должны остаться доступны — не подменяем их заглушкой.
        universal_present = [t for t in all_tools if t.name in UNIVERSAL_TOOLS]

        # Если после фильтрации не осталось НИ ОДНОЙ рабочей категории
        # (пользователь не выбрал инструменты в панели на главной странице),
        # всегда добавляем no_tools_selected. Так модель не пытается решить
        # задачу вслепую/выдумать ответ, а явно просит прожать инструменты.
        # Универсальные тулы (напр. analyze_image) остаются доступны рядом.
        if not filtered:
            return universal_present + [no_tools_selected]

        return filtered


    async def _lite_ainvoke(self, messages: list[BaseMessage]):
        """Вызов лайт-модели с цепочкой фолбэков.

        По умолчанию haiku (self.llm_lite). Если отказал — по очереди
        gpt-5-mini и gemini-3-flash-preview (self.llm_lite_fallbacks).
        Возвращает ответ первой успешной модели. Если упали все —
        пробрасывает последнее исключение.
        """
        chain = [self.llm_lite, *getattr(self, "llm_lite_fallbacks", [])]
        last_error: Exception | None = None
        for i, model in enumerate(chain):
            try:
                resp = await model.ainvoke(messages)
                if i > 0:
                    logging.warning(
                        f"Лайт-модель: сработал фолбэк #{i} "
                        f"({getattr(model, 'model_name', getattr(model, 'model', '?'))})"
                    )
                return resp
            except Exception as e:
                last_error = e
                logging.error(
                    f"Лайт-модель #{i} "
                    f"({getattr(model, 'model_name', getattr(model, 'model', '?'))}) "
                    f"упала: {type(e).__name__}: {e}"
                )
        # Все модели цепочки провалились
        raise last_error if last_error else RuntimeError("Лайт-модель: пустая цепочка")

    async def _main_ainvoke(self, messages: list[BaseMessage]):
        """Вызов ОСНОВНОЙ (тяжёлой) LLM — той же, на которой работает агент.

        Модель определяется текущим выбором пользователя (self.current_agent_model,
        выставляется в начале run_agent). Нужен служебным модулям, которым важно
        рассуждать на том же уровне, что и агент (например, фоновый супервизор цели),
        а не на дешёвой лайт-модели.

        Fallback: если по какой-то причине текущая модель не выбрана —
        берём 'universal', а если и её нет — тихо деградируем на лайт-модель.
        """
        agent_model = getattr(self, "current_agent_model", None) or "universal"
        model = self.AGENT_MODELS.get(agent_model)
        if model is None:
            logging.warning(
                f"_main_ainvoke: модель '{agent_model}' не найдена в AGENT_MODELS, "
                f"деградирую на лайт-модель"
            )
            return await self._lite_ainvoke(messages)
        return await model.ainvoke(messages)

    async def _summarize_cut_messages(
        self,
        cut_messages: list[BaseMessage],
        previous_summary: str | None,
    ) -> str:
        """
        Сжимает сообщения, которые trim_messages собирается отбросить,
        сохраняя конкретные факты (пути файлов, id инструментов, договорённости).
        Возвращает НОВОЕ саммари, которое полностью заменяет предыдущее.
        """
        if not cut_messages:
            return previous_summary or ""

        # При A1 tool_trace больше не приклеен к content, а лежит отдельно в
        # additional_kwargs["tool_trace"]. Чтобы факты из трейса (пути, workspace_id,
        # ветки, ссылки) не терялись при сжатии отсечённых сообщений — подмешиваем
        # компактный кусок трейса прямо в chunk_text (с жёсткой обрезкой на сообщение).
        def _fmt_cut(m: BaseMessage) -> str:
            role = getattr(m, "type", m.__class__.__name__)
            body = str(getattr(m, "content", "") or "")[:1000]
            trace = (getattr(m, "additional_kwargs", None) or {}).get("tool_trace")
            if trace:
                body = f"{body}\n  (tool_trace: {str(trace)[:1200]})"
            return f"[{role}]: {body}"

        chunk_text = "\n".join(_fmt_cut(m) for m in cut_messages)

        previous_block = (
            f"Предыдущее саммари (актуальное на момент этого сжатия):\n{previous_summary}\n"
            if previous_summary else "Предыдущего саммари нет — это первое сжатие для этого чата."
        )

        prompt = SUMMARIZE_CUT_PROMPT.format(previous_block=previous_block, chunk_text=chunk_text)

        try:
            response = await self._lite_ainvoke([
                SystemMessage(content="Ты — модуль сжатия истории диалога. Отвечай только сводкой, без преамбул."),
                HumanMessage(content=prompt),
            ])
            return response.content.strip()
        except Exception as e:
            logging.error(f"Не удалось засаммаризовать отсечённую историю: {e}", exc_info=True)
            # Фолбэк — не терять предыдущее саммари молча, если LLM-вызов упал
            return previous_summary or "[Часть истории была отсечена, сжатие не удалось]"
        


    # ------------------------------------------------------------------
    # HTTP helper
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Redis helper (стриминг чанков на sigma-гейтвей)
    # ------------------------------------------------------------------

    async def _get_redis(self):
        """Ленивый async Redis-клиент для pub/sub-стрима на sigma.

        Fail-open: если redis недоступен или пакет не стоит — возвращаем None
        и больше не пытаемся (self._redis_failed), чтобы не дёргать его на каждый чанк.
        Стрим — необязательный канал: финал всё равно доедет через Kafka."""
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            return None
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            return self._redis
        except Exception as e:
            self._redis_failed = True
            logger.warning(f"[stream] Redis недоступен, стриминг на sigma отключён: {e}")
            return None

    async def _publish_sigma_stream(self, request_id: str, payload: dict) -> None:
        """Опубликовать один стрим-фрейм в канал stream:{request_id}.

        sigma-гейтвей подписан на этот канал и релеит фрейм в SSE. Все ошибки
        глушим — стриминг не должен ронять ответ."""
        r = await self._get_redis()
        if r is None:
            return
        try:
            await r.publish(f"stream:{request_id}", json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[stream] redis publish не удался: {e}")

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    async def _probe_providers_status(self) -> str:
        """Дёргает /internal/providers_status и возвращает человекочитаемую
        строку с доступностью моделей (coder/designer/universal).

        Использует готовый health-check эндпоинт того же процесса
        (кеш TTL 30с на его стороне). При любой ошибке — мягкий фолбэк."""
        _labels = {
            "coder": "Кодер",
            "designer": "Дизайнер",
            "universal": "Универсал",
        }
        try:
            client = await self._get_http_client()
            port = os.getenv("PORT_PM_AGENT") or "9001"
            resp = await client.get(
                f"http://127.0.0.1:{port}/internal/providers_status",
                timeout=12.0,
            )
            resp.raise_for_status()
            providers = (resp.json() or {}).get("providers", {}) or {}
        except Exception as e:
            logger.warning(f"[providers_status] проба не удалась: {e}")
            return "Не удалось опросить статус моделей (health-check недоступен)."

        if not providers:
            return "Статус моделей получить не удалось."

        up_list, down_list = [], []
        for mid, label in _labels.items():
            info = providers.get(mid) or {}
            if info.get("up"):
                up_list.append(label)
            else:
                down_list.append(label)

        lines = ["Доступность моделей сейчас:"]
        for mid, label in _labels.items():
            info = providers.get(mid) or {}
            mark = "✅" if info.get("up") else "❌"
            lines.append(f"{mark} {label}")

        if not up_list:
            lines.append("\nСейчас недоступна ни одна модель — ждите восстановления провайдера.")
        else:
            lines.append(f"\nМожно переключиться на: {', '.join(up_list)}.")
        return "\n".join(lines)

    def _ctx_get(self, key: str, default=None):
        """Читает request-local значение из ContextVar."""
        ctx = self._request_ctx.get()
        if isinstance(ctx, dict):
            return ctx.get(key, default)
        return default

    def _ctx_append_pending_image(self, item: dict) -> None:
        """Складывает сгенерированную картинку в request-local буфер."""
        ctx = self._request_ctx.get()
        if not isinstance(ctx, dict):
            return
        ctx.setdefault("pending_generated_images", []).append(item)

    def _resolve_gateway_url(self) -> str:
        target = (
            self._ctx_get("gateway_target", None)
            or getattr(self, "_gateway_target", "main")
            or "main"
        ).lower()
        if target == "sigma":
            return GATEWAY_SIGMA_URL
        return GATEWAY_URL

    async def _send_status(self, status: str, request_id: str) -> None:
        if not request_id:
            return
        try:
            client = await self._get_http_client()
            await client.post(
                f"{self._resolve_gateway_url()}/internal/bot_status",
                json={"request_id": request_id, "status": status},
                timeout=2,
            )
        except Exception as e:
            logger.debug(f"Не удалось отправить статус: {e}")

    async def _send_chunk(
        self,
        chunk: str,
        request_id: str,
        msg_id: str | None = None,
        kind: str = "final",
    ) -> None:
        """Отправка чанка напрямую в gateway (минуя Kafka).

        kind:
          "live"          — живой чанк текста. Фронт печатает его СРАЗУ в
                            приглушённом reasoning-стиле (курсив, мельче), т.к.
                            заранее неизвестно, финал это или размышление;
          "reasoning_end" — маркер: живой блок был размышлением перед тулзой,
                            фронт закрывает reasoning-блок (chunk = "");
          "final_commit"  — маркер: живой блок оказался финальным ответом,
                            фронт "повышает" его до основного стиля (chunk = "").

        Стриминг не должен ронять ответ — все ошибки глушим в debug."""
        if not request_id:
            return
        # Для управляющих маркеров пустой chunk допустим
        if not chunk and kind not in ("reasoning_end", "final_commit"):
            return

        target = (getattr(self, "_gateway_target", "main") or "main").lower()
        payload = {
            "request_id": request_id,
            "chunk": chunk,
            "id": msg_id,
            "kind": kind,
        }

        # SIGMA: у гейтвея нет WebSocket — льём чанки через Redis pub/sub,
        # sigma-гейтвей подписан на stream:{request_id} и релеит в SSE.
        if target == "sigma":
            await self._publish_sigma_stream(request_id, payload)
            return

        # MAIN: прямой HTTP в gateway → WebSocket (как раньше).
        try:
            client = await self._get_http_client()
            await client.post(
                f"{self._resolve_gateway_url()}/internal/bot_stream",
                json=payload,
                timeout=2,
            )
        except Exception as e:
            logger.debug(f"Не удалось отправить чанк стриминга: {e}")


    # ------------------------------------------------------------------
    # Основной цикл: ReAct-style через bind_tools + рекурсивный вызов
    # ------------------------------------------------------------------

    async def process_query(
        self,
        user_input: str,
        request_id: str,
        user_id: int,
        chat_id,
        database,
        agent_model,
        source=None,
        selected_tools=None,
        context_size=None,
        images=None,
        gateway_target: str = "main",
    ) -> Dict[str, Any]:
        if not self.db:
            self.db = database

        # Статус "думаю" больше НЕ шлём: пользователь видит реальный поток
        # размышлений модели (живой reasoning-стрим), а не служебный индикатор.
        # await self._send_status(get_thinking_status(), request_id)
        # Request-local контекст для текущего хода.
        # Это устраняет гонки при параллельной обработке разных чатов.
        ctx_token = self._request_ctx.set({
            "request_id": request_id,
            "gateway_target": gateway_target or "main",
            "chat_id": chat_id,
            "user_id": user_id,
            "message_id": None,
            "agent_model": agent_model,
            "pending_generated_images": [],
            # Защита от бесконечного polling check_task_status: >3 подряд блокируем
            "check_status_streak": 0,
            "last_tool_name": "",
        })

        # Legacy-поля оставляем как fallback для старого кода,
        # но новый код читает только из request-local контекста.
        self.current_request_id = request_id
        self._gateway_target = gateway_target or "main"
        self.current_chat_id = chat_id
        self.current_user_id = user_id
        self.current_message_id = None  # message_id генерится позже, в app.py; необязателен
        cleaned_user_input = user_input
        try:
            # ── Настройки контекста ────────────────────────────────────────
            CONTEXT_PRESETS = {
                "small":  {"limit_mem": 8,  "max_tokens": 6000},
                "medium": {"limit_mem": 10, "max_tokens": 15000},
                "large":  {"limit_mem": 12, "max_tokens": 20000},
            }
            logging.info(f'Выбрана модель {agent_model}')
            llm = self.AGENT_MODELS[agent_model]
            # Текущая модель нужна субагентам для корректного выбора LLM
            # при изолированном запуске через call_subagent.
            self.current_agent_model = agent_model
 
            final_response = ""
            used_tools = []
            tool_trace: list[str] = []
            AUTO_SEND_FILE_TOOLS = {"send_file_to_user"}
            files_to_send = []
            seen_paths = set()
            # request-local pending_generated_images уже инициализирован в ContextVar
            # при старте process_query.
            # SOFT_LIMIT временно отключён (не спамим на длинных задачах); предупреждаем только когда steps_left <= 3 (см. ниже)
            SOFT_LIMIT = 10   # DEPRECATED, оставлен для loop_state-совместимости
            HARD_LIMIT = 100  # поднято с 14; контекст не пухнет благодаря in-loop compaction
            STEPS_LEFT_WARN = 3  # предупреждаем про request_more_steps когда осталось <= N шагов
            JUDGE_LIMIT = 5
            # ФОНОВЫЙ reasoning-супервизор цели: триггер каждые N шагов, неблокирующе.
            SUPERVISOR_EVERY_N = 5   # как часто запускать фоновый надзор (0 = выкл)
            SUPERVISOR_MIN_ITER = 4  # не трогаем первые шаги (агент только разогнался)
            total_input_tokens  = 0
            total_output_tokens = 0

            preset = CONTEXT_PRESETS.get(context_size, CONTEXT_PRESETS["medium"])
            limit_mem = preset["limit_mem"]
            max_tokens = preset["max_tokens"]

            # Telegram: allow_all ВЫКЛЮЧЕН. Тулы берутся строго из настроек
            # чата (selected_tools пришли из telegram_chat_settings), минус
            # админские (режутся ещё в telegram_service/agent). Журнал в ТГ включаем.
            is_telegram = (source == "telegram")
            allow_all_tools = False
            selected_tools = selected_tools or []
            use_journal = is_telegram or ("sessions" in selected_tools)

            logging.info(f"🙂 {user_input[:200]}")
            tools = self._build_tools(
                user_id,
                chat_id,
                user_input,
                selected_tools=selected_tools,
                allow_all=allow_all_tools,
                images=images,
                agent_model=agent_model,
                is_telegram=is_telegram,
            )
            loop_state = {
                "iteration": 0,
                "soft_limit": SOFT_LIMIT,
                "hard_limit": HARD_LIMIT,
                "judge_limit": JUDGE_LIMIT,
                "steps_left_warn": STEPS_LEFT_WARN,
                "extension_used": False,
            }

            extension_tool = make_extension_tool(loop_state)
            tools.append(extension_tool)          # тулза аппендится прямо внутри цикла, офигеть имбово

            subagent_tl = make_subagent_tool(self)
            tools.append(subagent_tl)

            print(f"ЛИМИТ ПО КОНТЕКСТУ = {limit_mem}, ЧИСЛО ТОКЕНОВ = {max_tokens}")
            # Привязываем инструменты к модели
        

            current_datetime = datetime.now().strftime("%d.%m.%Y %H:%M, %A")

            # РОЛЬ РЕШАЕТСЯ КОДОМ по verified user_id (не моделью, не по тексту/табельному/тегу).
            role_block = get_role(user_id)
            system_content = SYSTEM_PROMPT.format(datetime=current_datetime, role_block=role_block)
            # Telegram: подмешиваем HTML-инструкцию форматирования + per-chat промпт из БД.
            if is_telegram:
                try:
                    from agent_pm.telegram.tg_integration import (
                        get_tg_chat_settings, build_tg_system_suffix,
                    )
                    _tg_settings = await get_tg_chat_settings(self.db, chat_id)
                    _tg_name = _tg_settings.get("agent_name")
                    if _tg_name and _tg_name.strip():
                        system_content += (
                            "\n\nВ этом чате тебя зовут «" + _tg_name.strip() + "». "
                            "Участники обращаются к тебе по этому имени — воспринимай его как своё и при необходимости так и представляйся."
                        )
                    system_content += "\n\n" + build_tg_system_suffix(_tg_settings.get("system_prompt"))
                except Exception as _e:
                    logging.warning(f"[tg] system suffix fail: {_e}")

            # Собираем историю из памяти
            history = await self._build_history(chat_id, limit=30)
            journal_text = None
            if use_journal:
                print(f"USER ID: {user_id}")
                tools.append(make_journal_tool(self, chat_id, user_id))
                journal_text = await self.db.get_agent_journal(chat_id)
                print(f"ЖУРНАЛ АГЕНТА: длина = {len(journal_text)   }")
                # if source == "telegram":
                #     journal_text = await self.db.get_agent_journal(56) # Пока что мой id

            tools_by_name = {t.name: t for t in tools}
            # Имена реально доступных агенту тулов — для каталога фонового супервизора
            loop_state["tool_names"] = list(tools_by_name.keys())
            llm_with_tools = llm.bind_tools(tools)
            session_summary = await self.db.get_last_session_summary(chat_id)

            logger.info(f"[ИСТОРИЯ ДО ОБРЕЗКИ] {len(history)}")
            # Режем историю чатов
            trimmed_history = trim_messages(
                history,
                max_tokens=max_tokens,
                token_counter=count_tokens_approximately,
                strategy='last',
                start_on='human',
                include_system=True,
            )
        
            # ── Саммаризация того, что было отсечено ────────────────────────────
            cut_count = len(history) - len(trimmed_history)
            rolling_summary_text = None

            previous_rolling = await self.db.get_rolling_summary(chat_id)
            previous_covered_id = previous_rolling["covered_up_to_id"] if previous_rolling else 0
            previous_summary_text = previous_rolling["summary_text"] if previous_rolling else None

            logging.info(f"ЧИСЛО РАЗНИЦА = {cut_count}")
            if cut_count > 0:
                cut_messages_all = history[:cut_count]
                # берём только те, что ЕЩЁ не входили в прошлый summary
                new_cut_messages = [
                    m for m in cut_messages_all
                    if m.additional_kwargs.get("db_id", 0) > previous_covered_id
                ]

                if new_cut_messages:
                    rolling_summary_text = await self._summarize_cut_messages(new_cut_messages, previous_summary_text)
                    max_covered_id = max(m.additional_kwargs.get("db_id", 0) for m in cut_messages_all)
                    await self.db.upsert_rolling_summary(chat_id, rolling_summary_text, covered_up_to_id=max_covered_id)
                    logger.info(
                        f"[ROLLING SUMMARY] Новых сообщений в сжатии: {len(new_cut_messages)} "
                        f"(из {len(cut_messages_all)} отсечённых, {len(cut_messages_all) - len(new_cut_messages)} уже были покрыты), "
                        f"длина саммари={len(rolling_summary_text)}"
                    )
                else:
                    # всё что отсекли — уже было в прошлом summary, реально нового сжимать не нужно
                    rolling_summary_text = previous_summary_text
                    logger.info("[ROLLING SUMMARY] Все отсечённые сообщения уже покрыты предыдущим саммари, LLM-вызов пропущен")
        
            else:
                rolling_summary_text = previous_summary_text

            messages: List[BaseMessage] = []
            if journal_text is not None:
                messages.append(SystemMessage(content=f"📜 БОРТОВОЙ ЖУРНАЛ (Память):\n{journal_text}"))
            elif use_journal and journal_text is None:
                messages.append(SystemMessage(content=f"📜 БОРТОВОЙ ЖУРНАЛ (Память): ПУСТОЙ"))
            messages.append(SystemMessage(content=system_content))

            if rolling_summary_text:
                messages.append(
                    SystemMessage(
                        content=f"🧵 СЖАТАЯ ИСТОРИЯ ДИАЛОГА (то, что было отсечено из контекста ранее):\n{rolling_summary_text}"
                    )
                )

            if session_summary:
                messages.append(
                    SystemMessage(
                        content=(
                            f"📋 САММАРИ ПРОШЛОЙ СЕССИИ (session_id={session_summary['session_id']}):\n"
                            f"{session_summary['summary_text']}"
                        )
                    )
                )

            messages += [
                *trimmed_history,
                HumanMessage(content=user_input),
            ]

            async def agent_event_loop(messages: list):
                nonlocal total_input_tokens, total_output_tokens # Не видит из-за +=
                iteration = 0
                # ФОНОВЫЙ супервизор: храним текущий asyncio-таск (или None).
                supervisor_task = None
                while iteration < loop_state["hard_limit"]:
                    loop_state["iteration"] = iteration

                    # ── ФОНОВЫЙ reasoning-СУПЕРВИЗОР ЦЕЛИ ─────────────────────
                    # (а) ПОДХВАТ готового результата НЕБЛОКИРУЮЩЕ: если прошлый
                    # таск уже завершён и вернул директиву — мягко подмешиваем
                    # её SystemMessage-ом. Основной цикл НИКОГДА не ждёт супервизора.
                    if supervisor_task is not None and supervisor_task.done():
                        try:
                            hint = supervisor_task.result()
                        except Exception as _se:
                            hint = None
                            logger.error(f"[supervisor] таск упал: {_se}")
                        supervisor_task = None
                        if hint:
                            messages.append(SystemMessage(content=(
                                "[ФОНОВЫЙ КОНТРОЛЁР ЦЕЛИ] Внешний наблюдатель за твоей "
                                "работой заметил возможное отклонение от цели. "
                                "Это ПОДСКАЗКА, не приказ — учти, если по делу, "
                                "иначе продолжай свой план:\n" + hint
                            )))
                            logger.info(f"[supervisor] директива подмешана на шаге {iteration}")

                    # (б) ТРИГГЕР нового фонового прогона раз в N шагов,
                    # только если предыдущий уже завершён (guard от накладки).
                    # Читаем СНАПШОТ (копия среза), чтобы не читать мутируемый список.
                    if (SUPERVISOR_EVERY_N > 0 and iteration >= SUPERVISOR_MIN_ITER
                            and iteration % SUPERVISOR_EVERY_N == 0
                            and supervisor_task is None):
                        snapshot = list(messages)
                        supervisor_task = asyncio.create_task(
                            run_supervisor(self, user_input, snapshot, loop_state)
                        )
                    # ────────────────────────────────────────────────────────

                    # ТОЧКА 2: тулзы снимаем только на последнем шаге
                    is_last_step = iteration >= loop_state["hard_limit"] - 1
                    can_still_extend = not loop_state["extension_used"]
                    current_llm = llm if (is_last_step and not can_still_extend) else llm_with_tools

                    # ТОЧКА 3: адресный триггер на КАЖДОМ шаге окна, а не однократно.
                    steps_left = loop_state["hard_limit"] - iteration - 1
                    # SOFT_LIMIT-предупреждение привязано к КОНЦУ окна, а не к фикс. номеру:
                    # при hard_limit=100 нельзя спамить 90 SystemMessage. Шлём подсказку
                    # про request_more_steps только когда до потолка осталось <= STEPS_LEFT_WARN.
                    if steps_left <= loop_state["steps_left_warn"] and can_still_extend:
                        messages.append(SystemMessage(content=(
                            f"[Контроль лимита] Осталось шагов: {steps_left}. "
                            f"Если НЕ успеваешь завершить задачу за оставшиеся шаги — "
                            f"вызови request_more_steps ПРЯМО СЕЙЧАС (расширение одноразовое). "
                            f"Если успеваешь — продолжай и дай финальный ответ."
                        )))
                    elif steps_left <= loop_state["steps_left_warn"]:
                        messages.append(SystemMessage(
                            content=f"[Прогресс: шаг {iteration} из {loop_state['hard_limit']}]"
                        ))


                    # ── Живой стриминг текста модели ──────────────────────────
                    # Заранее неизвестно, окажется ли текущий шаг финалом (без
                    # tool_calls) или reasoning'ом перед вызовом тулзы. Поэтому
                    # чанки шлём оптимистично с kind="live" — фронт печатает их в
                    # текущем "живом" блоке. Как только шаг завершился и стал ясен
                    # его тип, шлём УПРАВЛЯЮЩИЙ маркер:
                    #   • есть tool_calls  → kind="reasoning_end": фронт сворачивает
                    #     живой блок в приглушённый reasoning-блок (курсив, мельче);
                    #   • нет tool_calls   → kind="final_commit": фронт фиксирует
                    #     живой блок как финальный ответ (основной шрифт).
                    stream_state = {"sent": False}

                    async def _on_chunk(text: str, kind: str = "answer"):
                        # kind из коллектора: "reasoning" | "answer".
                        # Оба канала шлём оптимистично как live — фронт
                        # печатает в живом блоке; тип шага (reasoning_end /
                        # final_commit) доопределяется по tool_calls после шага.
                        stream_state["sent"] = True
                        await self._send_chunk(
                            text, request_id, msg_id=str(request_id), kind="live"
                        )

                    _llm_error = {}
                    try:
                        response = await _astream_collect(
                            current_llm, messages, on_chunk=_on_chunk, iteration=iteration,
                            error_info=_llm_error
                        )
                    except Exception as _exc:
                        _llm_error["reason"] = f"{type(_exc).__name__}: {_exc}"
                        response = None

                    if response is None:
                        _err_reason = _llm_error.get("reason", "unknown")
                        logging.error(f"[iteration={iteration}] Все попытки llm провалены, прерываю цикл: {_err_reason}")
                        _is_provider_down = any(kw in str(_err_reason).lower() for kw in (
                            "all providers failed", "503", "service unavailable",
                            "connection", "timeout", "unreachable"
                        ))
                        if _is_provider_down:
                            _status_line = await self._probe_providers_status()
                            final_response = (
                                "⚠️ Провайдер моделей (cloud.ru) сейчас недоступен или отвечает ошибками "
                                "(503/Service Unavailable). Это не проблема агента — проблема на стороне провайдера.\n\n"
                                f"{_status_line}\n\n"
                                "Попробуйте повторить запрос через несколько минут."
                            )
                        else:
                            final_response = "Ошибка внутри цикла агента. Но агент запомнил шаги, попробуйте вернуться к запросу"
                        break

                    _has_tool_calls = bool(getattr(response, "tool_calls", None))
                    _had_text = bool(getattr(response, "content", None))

                    if _has_tool_calls and _had_text:
                        # Это был reasoning перед тулзой. Оставляем его на фронте,
                        # но переклассифицируем живой блок в свёрнутый reasoning-блок.
                        loop_state.setdefault("reasoning_trace", []).append(response.content)
                        if stream_state["sent"]:
                            await self._send_chunk(
                                "", request_id, msg_id=str(request_id), kind="reasoning_end"
                            )
                        # В стек reasoning не тащим (как и раньше) — гасим content
                        response.content = ""
                    elif _has_tool_calls and not _had_text:
                        # Шаг без текста (сразу tool_calls). Если что-то всё же
                        # успело улететь как live (пустой мусор) — закрываем блок.
                        if stream_state["sent"]:
                            await self._send_chunk(
                                "", request_id, msg_id=str(request_id), kind="reasoning_end"
                            )

                    messages.append(response)          # ← единственный аппенд

                    usage = getattr(response, "usage_metadata", None) or {}
                    iter_in  = usage.get("input_tokens", 0)
                    iter_out = usage.get("output_tokens", 0)
                    total_input_tokens  += iter_in
                    total_output_tokens += iter_out
                    logging.info(
                        f"[tokens] iter={iteration} "
                        f"in={iter_in} out={iter_out} "
                        f"total_in={total_input_tokens} total_out={total_output_tokens}"
                    )
                    logger.info(f"🦒Длина стека = {len(messages)}")

                    # ---- IN-LOOP CONTEXT COMPACTION -------------------------------
                    # Триггер ПО ФАКТУ от модели (iter_in), не аппроксимация.
                    # Проверяем НА ГРАНИЦЕ итерации: все ToolMessage текущего шага
                    # уже добавлены ниже по циклу на СЛЕДУЮЩЕМ проходе, поэтому пары
                    # всегда целые. Компактор режет только старые пары, вставляет
                    # SystemMessage-конспект (пути/ссылки/артефакты/workspace) —
                    # tool-protocol не ломается. БД НЕ трогаем.
                    if iter_in and iter_in >= TOKEN_SOFT_LIMIT:
                        logger.info(
                            f"[compactor] iter_in={iter_in} >= {TOKEN_SOFT_LIMIT}, "
                            f"запускаю сжатие стека (итерация {iteration})"
                        )
                        try:
                            messages, _meta = await compact_messages(
                                self,
                                messages,
                                summarize_tool_call=_summarize_tool_call,
                            )
                        except Exception as _e:
                            logger.error(f"[compactor] сбой сжатия, продолжаю без него: {_e}")
                    # --------------------------------------------------------------

                    if not getattr(response, "tool_calls", None):
                        final_response = response.content or ""

                        # Финальный шаг: фиксируем живой блок как финальный ответ.
                        # Если что-то стримилось (обычный случай) — шлём маркер
                        # final_commit, чтобы фронт закрепил блок основным шрифтом.
                        if stream_state["sent"]:
                            await self._send_chunk(
                                "", request_id, msg_id=str(request_id), kind="final_commit"
                            )
                        break

                    # Выполняем все запрошенные инструменты параллельно
                    tool_calls = response.tool_calls
                    tool_names = [tc['name'] for tc in tool_calls]
                    # Статусы тулов больше НЕ шлём: пользователь видит реальный поток
                    # размышлений модели (reasoning-блоки), а не служебные индикаторы.
                    # await self._send_status(get_tools_status(tool_names), request_id)

                    # logging.info(f"Итерация в цикле: {iteration}. Вызванные инструменты: {tool_calls}")
                    tool_results = await asyncio.gather(
                        *[
                            self._invoke_tool(tools_by_name, tc, request_id)
                            for tc in tool_calls
                        ],
                        return_exceptions=True,
                    )
                    # logging.info(f"Результаты тулзов: {tool_results}")

                    for tc, tr in zip(tool_calls, tool_results):
                        tool_name = tc["name"]
                        used_tools.append(tool_name)
                        # logging.info(f"РЕЗУЛЬАТЫ ТУЛЗЫ: {tr}")
                        if isinstance(tr, Exception):
                            content_for_message = f"Ошибка инструмента: {tr}"
                            result_dict = {"ok": False, "error": str(tr)}
                        else:
                            if isinstance(tr, str):
                                try:
                                    result_dict = ast.literal_eval(tr)
                                except (ValueError, SyntaxError):
                                    try:
                                        result_dict = json.loads(tr)
                                    except (json.JSONDecodeError, TypeError):
                                        result_dict = {"raw": tr}
                            elif isinstance(tr, dict):
                                result_dict = tr
                            else:
                                result_dict = {"raw": tr}


                        if isinstance(result_dict, dict) and result_dict.get("saved_files"):
                            should_auto_send = (
                                tool_name in AUTO_SEND_FILE_TOOLS
                                or bool(result_dict.get("auto_send"))
                            )

                            if should_auto_send:
                                for f in result_dict["saved_files"]:
                                    if f["path"] not in seen_paths:
                                        seen_paths.add(f["path"])
                                        files_to_send.append(f)

                        if tool_name == "execute_python_code" and isinstance(result_dict, dict):
                            if "stdout" in result_dict:
                                result_dict["stdout"] = _truncate_text(result_dict["stdout"], max_chars=3000)
                            if "stderr" in result_dict:
                                result_dict["stderr"] = _truncate_text(result_dict["stderr"], max_chars=1000)

                        content_for_message = str(result_dict)

                        # Формируем ToolMessage на основе обрезанных данных
                        if isinstance(tr, Exception):
                            content = f"Ошибка инструмента: {tr}"
                        else:
                            content = content_for_message

                        # ── ПРЕДОХРАНИТЕЛЬ (Вариант C, часть A): универсальная обрезка ──
                        # execute_python_code уже режет stdout/stderr выше, но ЛЮБОЙ
                        # другой тул (execute_shell_commands, analyze_image,
                        # search_internet, git-выводы) кладёт content целиком.
                        # Один жадный вызов раздувает стек до потолка и in-loop
                        # компактор не спасает, т.к. свежая пара защищена keep_last.
                        # Поэтому усекаем тело ЛЮБОГО tool-результата на входе в стек.
                        content = _truncate_text(content, max_chars=MAX_TOOL_MSG_CHARS)

                        messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))

                        summary = _summarize_tool_call(tc, tr)
                        if summary:
                            tool_trace.append(summary)

                    iteration += 1

                else:
                    messages.append(HumanMessage(content=(
                        "ВНИМАНИЕ: Лимит шагов исчерпан. Обязательно верни текстовый ответ Пользователю"
                    )))

                    try:
                        result_response = await llm_with_tools.ainvoke(messages)
                    
                        # Если упрямая модель всё равно вернула tool_calls вместо текста
                        if not result_response.content and getattr(result_response, "tool_calls", None):
                            final_response = f"Агент не уложился в отведенное время и пошел за инструментами. Попробуйте повторить запрос."
                        else:
                            final_response = result_response.content or f"Достигнут лимит шагов. Попробуйте повторить запрос."
                        
                    except Exception as e:
                        final_response = f"Ошибка агента: это не хеппи-энд"
                
                # СЛИВ ФОНОВОГО СУПЕРВИЗОРА: если остался незавершённый таск —
                # отменяем, чтобы не тёк в фон (его вердикт уже не нужен).
                if supervisor_task is not None and not supervisor_task.done():
                    supervisor_task.cancel()
                    try:
                        await supervisor_task
                    except (asyncio.CancelledError, Exception):
                        pass

                return final_response, tool_trace

            # LLM-as-judge: проверяем финальный ответ перед commit.
            # Судья может завернуть ответ только один раз за запрос.
            final_response, tool_trace = await agent_event_loop(messages=messages)

            # --- JUDGE ВРЕМЕННО ОТКЛЮЧЁН (feature/in-loop-context-compaction) ---
#             if loop_state["iteration"] >= loop_state["judge_limit"]:
#                 judge_result = await judge_final_answer(
#                     llm=llm,
#                     user_input=user_input,
#                     final_response=final_response,
#                     tool_trace=tool_trace,
#                 )
#                 logging.info(f"🛑 ВЕРДИКТ СУДЬИ: {judge_result}")
#                 if judge_result.get("verdict") == "revise":
#                     reason = (judge_result.get("reason") or "").strip()
#                     missing = (judge_result.get("missing") or "").strip()
#                     messages.append(HumanMessage(content=(
#                         "[JUDGE] Финальный ответ не принят. Доработай ответ и верни "
#                         "пользователю корректный итоговый результат. "
#                         "Избегай лишних шагов и инструментов, если можно ответить сразу.\n"
#                         f"Причина: {reason or 'не указана'}\n"
#                         f"Что исправить: {missing or 'улучшить релевантность и полноту ответа'}"
#                     )))
#                     final_response, tool_trace = await agent_event_loop(messages=messages)

            tokens_usage = {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                }

            # Авто-отправка изображений, сгенерированных тулом generate_image.
            # Берём request-local буфер из ContextVar, чтобы файлы разных чатов
            # не смешивались при параллельной обработке.
            pending_images = self._ctx_get("pending_generated_images", [])
            for gen_item in pending_images:
                if isinstance(gen_item, dict):
                    dedup = gen_item.get("image_key") or gen_item.get("name")
                    if dedup and dedup not in seen_paths:
                        seen_paths.add(dedup)
                        files_to_send.append({
                            "image_key": gen_item.get("image_key"),
                            "name": gen_item.get("name") or "image.jpg",
                            "kind": "image",
                        })
                else:
                    gen_path = gen_item
                    if gen_path not in seen_paths:
                        seen_paths.add(gen_path)
                        files_to_send.append({
                            "path": gen_path,
                            "name": os.path.basename(gen_path),
                            "kind": "image",
                        })

            # logging.info(f"ФАЙЛЫ К ОТПРАВКЕ {files_to_send}")
            # Автогенерация заголовка чата по первому обмену (логика в title_generator.py).
            # Fail-open: не первый ход / ошибка -> auto_title=None.
            auto_title = None
            try:
                if self.db and chat_id is not None and await is_first_turn(self.db.pool, chat_id):
                    auto_title = await generate_chat_title(
                        self._lite_ainvoke, user_input, final_response
                    )
                    if auto_title:
                        logging.info(f"[title] chat={chat_id} сгенерирован заголовок: {auto_title!r}")
            except Exception as e:
                logging.error(f"[title] ошибка автозаголовка chat={chat_id}: {e}")
            return {
                "input": user_input,
                "message": final_response,
                "used_tools": used_tools,
                "action": used_tools[0] if len(used_tools)> 0 else '',
                "success": True,
                "files_to_send": files_to_send,
                "tool_trace": '; '.join(tool_trace) if tool_trace else None,
                "agent_model": agent_model,
                "tokens_usage": tokens_usage,
                "auto_title": auto_title
            }
        finally:
            # Очищаем request-local контекст текущего хода.
            self._request_ctx.reset(ctx_token)


    async def _invoke_tool(self, tools_by_name: dict, tool_call: dict, request_id: str) -> str:
        """Вызвать один инструмент по его имени и аргументам."""
        name = tool_call["name"]
        args = tool_call.get("args", {})

        # Анти-спам для check_task_status: разрешаем максимум 3 вызова подряд.
        # На 4-й и далее не дёргаем sandbox, а возвращаем инъекцию в контекст модели,
        # чтобы она завершала шаг ответом в чат и ждала асинхронного уведомления.
        ctx = self._request_ctx.get()
        if isinstance(ctx, dict):
            last_tool_name = ctx.get("last_tool_name", "")
            if name == "check_task_status":
                streak = int(ctx.get("check_status_streak", 0))
                streak = streak + 1 if last_tool_name == "check_task_status" else 1
                ctx["check_status_streak"] = streak
                ctx["last_tool_name"] = "check_task_status"

                if streak > 3:
                    logger.warning(
                        "[tool] ⛔ check_task_status blocked: streak=%s request_id=%s",
                        streak,
                        request_id,
                    )
                    return str({
                        "ok": False,
                        "throttled": True,
                        "tool": "check_task_status",
                        "streak": streak,
                        "message": (
                            "Лимит polling: check_task_status вызван более 3 раз подряд. "
                            "Не проверяй статус снова в этом цикле. "
                            "Сформируй пользователю короткий ответ в чат: "
                            "задача ещё выполняется, уведомление придёт автоматически; "
                            "предложи продолжить диалог по другим вопросам."
                        ),
                    })
            else:
                # Любой другой инструмент сбрасывает серию подряд.
                ctx["check_status_streak"] = 0
                ctx["last_tool_name"] = name

        # Автоматически подмешиваем request_id, если инструмент его принимает
        if name == "download_text_articles":
            args["request_id"] = request_id

        tool_fn = tools_by_name.get(name)
        if tool_fn is None:
            logger.warning(f"[tool] ❓ '{name}' не найден")
            return f"Инструмент '{name}' не найден. Следует прожать галочку на главной странице приложения. Гаечный ключ."
        
        fmt = _fmt_args(name, args)
        logger.info(f"[tool] ▶ {name}({fmt})")

        import time
        t0 = time.monotonic()
        try:
            result     = await tool_fn.ainvoke(args)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            result_str = str(result)

            # Сокращаем результат для лога
            preview = result_str[:MAX_RESULT_LEN].replace("\n", " ")
            if len(result_str) > MAX_RESULT_LEN:
                preview += f"…[+{len(result_str)-MAX_RESULT_LEN}]"

            logger.info(f"[tool] ✅ {name} → {elapsed_ms}ms | {preview}")
            return result_str

        except Exception as e:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.error(f"[tool] ❌ {name} → {elapsed_ms}ms | {e}")
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
        # base_dir = Path(__file__).resolve().parent
        # json_file = base_dir / "knowledge_base" / "metadata.json"
        updater = MetadataUpdater(
            database=self.db,
            articles_path=self.articles_path,
            max_retries=1,
            retry_delay=30,
        )
        message = await updater.run()

        with open(json_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        self._delete_irrelevant_md_local(metadata)
        await self.obs_loader.delete_irrelevant_md(metadata)
        # await self.obs_loader.put_json()
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
        articles_data = await db.get_metadata_as_json()
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
        # if new_digest_files:
        #     email_result = await asyncio.to_thread(
        #         self.email_handler.send_auto_digest, new_digest_files, self.obs_loader
        #     )
        #     logger.info(f"Результат автоотправки: {email_result}")

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