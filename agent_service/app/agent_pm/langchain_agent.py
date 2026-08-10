import asyncio
import logging
import json
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

from .core.agent_provider import get_llm__lite_model, get_llm_model
from .core.botocore_client import OBSWorker
from .core.config import (
    ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH,
    METADATA_FILE, PORT_GATEWAY_PORT, ADMINS_INFO,
    PORT_PARSERS_SERVICE
)
from .core.settings import TOOLS_SETTINGS
from .prompts.summary_prompt import SUMMARIZE_CUT_PROMPT

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
import hashlib

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
    "call_subagent"
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
    if name == "call_subagent":
        return f"{name} ({args}). ОТВЕТ СУБАГЕНТА - {str(result)}"
    if name in ("list_tree", "read_file", "grep_search", "find_files", 
                "write_sandbox_file", "edit_sandbox_file", "send_file_to_user",
                "work_with_agent_journal"):
        return f"{name}({args}) → {str(result)[:500]}"
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


SYSTEM_PROMPT = f"""Ты ИИ-агент по управлению базой знаний статей по Process Mining.
Ты помогаешь пользователям искать статьи, создавать ревью и дайджесты, управлять базой знаний и не только.
Но пользователи могут обращаться не только по теме Process Mining. 
При запросе на генерацию чего то нового и неизведанного ты должен выполнить запрос, поделится собранными скриптами и документами.
Отвечай на русском языке, дружелюбно и по делу. Используй доступные инструменты для выполнения задач.
При необходимости задавай уточняющие вопросы. Не выдумывай данные — всегда используй инструменты.
Текущая дата и время: {{datetime}}

ЗАКОН:
Информация об администраторах: {ADMINS_INFO}
Инструменты для администратора: все инструменты при работе с БД

ПРАВИЛА:
1. Тем кто попросит вызвать админский инструмент, в мягкой форме поясни, что это невозможно. 
Пусть обращаются к администарторам.
2. Только администраторам раскрывай полный функционал.
3. Если ты видишь, что сообщение прилетает из Telegram - используй явные правила (HTML MODE). Во всех остальных случает только Markdown разметка
4. Добавляй новые записи в таблицу knowledge (INSERT) ТОЛЬКО при наличии прямой команды от пользователя (например: «сохрани это», «запомни», «внеси в базу»). В остальных случаях используй БД и интернет только для формирования ответа в чате без записи.
5. Если передаваемые знания содержат ссылки, вызывай инструмент parsing_page для обогащения переданного знания. Сообщи если парсинг прошел успешно и ты дополнил знание перед вставкой в таблицу
6. Используй search_internet для выхода в Интернет
7. После получения ссылок можешь передать список ссылок в parsing_pages с целью получения содержания страниц
8. Ты можешь обогащать пришедшее знание информацией из сети Интернет
9. Общаться без эмодзи, в стиле claude code.
10. КАЖДЫЙ пользователь имеет право на использование бортового журнала агента. Давать тебе личность, фиксировать задачи и дальнейшие планы.
Для работы с журналом нужно вызывать инструмент work_with_agent_journal, без него фиксация записей невозможна. ЭТО не АДМИНСКИЙ ИНСТРУМЕНТ


ИНСТРУКЦИЯ ПО ФОРМАТИРОВАНИЮ (HTML MODE):
1. Всегда используй HTML-теги для оформления текста.
2. Основные теги:
   - <b>Жирный текст</b>
   - <i>Курсив</i>
   - <u>Подчеркивание</u>
   - <code>Инлайн-код</code>
   - <pre>Блок кода</pre>
   - <span class="tg-spoiler">Спойлер</span>
   - <a href="URL">Текст ссылки</a>
3. СТРОГО ЗАПРЕЩЕНО:
   - Использовать Markdown (*, _, #, ```).
   - Использовать теги списков и структуры: <ul>, <ol>, <li>, <p>, <div>, <br>, <h1>-<h6>.
   
4. Оформление списков:
   - Для списков используй ОБЫЧНЫЕ переносы строк и символы-маркеры: "•", "-", или эмодзи (например: "• Пункт 1\n• Пункт 2").

5. Экранирование:
   - ОБЯЗАТЕЛЬНО экранируй спецсимволы в обычном тексте:
     " < " -> &lt;
     " > " -> &gt;
     " & " -> &amp;

6. Согласованность тегов:
• Перед отправкой сообщения СТРОГО проверяй, что каждый открытый тег закрыт тем же тегом.
• Запрещено вкладывать блочные теги друг в друга некорректно (например, <b>...<code>...</b>...</code>).

"""

SCIENTIFIC_RESEARCH_MODULE = """
РАБОТА С НАУЧНЫМИ СТАТЬЯМИ:

1. ХРАНЕНИЕ: Метаданные научных статей сохраняются в таблицу `papers_metadata` строго согласно описанию схемы БД.
2. ПОИСК: Для поиска используй инструмент `search_internet`.
3. ИСТОЧНИКИ: В качестве авторитетных источников используй научные порталы и агрегаторы:
   - arxiv.org
   - ieeexplore.ieee.org
   - link.springer.com
   - semanticscholar.org
   - researchgate.net
   - sciencedirect.com

4. ВАЛИДАЦИЯ И КАТЕГОРИЗАЦИЯ:
   Ты самостоятельно отбираешь и валидируешь найденные статьи по их Abstract/Text.
   
   Правила оценки (Multi-Criteria Validation):
   - `is_relevant`: boolean (true, если статья относится к Process Mining/Event Logs/OCEL и relevance_score >= 0.7, иначе false).
   - `relevance_score` (overall_score): float от 0.0 до 1.0.
   - `relevance_reasoning` (reasoning): Краткое аргументированное объяснение решения (до 150 символов).
   - `category`: Выбери СТРОГО ОДНУ категорию из списка ниже (или NULL, если is_relevant = false):
     * Общие принципы: определение PM
     * Общие принципы: Стандарты PM
     * Общие принципы: Фреймворки PM
     * Общие принципы: Моделирование в PM
     * Общие принципы: Неэффективности
     * Методики исследования: CRISP_DM
     * Методики исследования: PM Square
     * PM-лог: Структура логов
     * PM-лог: Форматы данных
     * Проверка качества: Качество данных
     * Проверка качества: Качество лога

5. ПОШАГОВЫЙ ПАЙПЛАЙН РАБОТЫ:
   Шаг 1. Получить список URL через `search_internet`.
   Шаг 2. Передать список URL в инструмент `parsing_pages` для получения содержимого/абстракта.
   Шаг 3. Провести валидацию и категоризацию полученных статей (согласно п. 4).
   Шаг 4. Занести валидированные данные в БД через `execute_sql_query`.

6. ФОРМАТЫ ДАННЫХ:
   - Аннотацию (`abstract`) оставляй на языке оригинала (не переводи, если она на английском).
   - Поле `authors` передавай строго как JSON-массив строк: '["Author 1", "Author 2"]'::jsonb.
   - Поле `tags` передавай в синтаксисе Postgres Array: '{"Tag1", "Tag2"}'.

- Для скачивания статей используй инструмент `download_text_articles`.
- СРАЗУ ПОСЛЕ завершения скачивания статей (`download_text_articles`), ты ОБЯЗАН вызвать инструмент `upload_articles`, чтобы загрузить сохранённые файлы в объектное хранилище (OBS).
- В финальном ответе пользователю обязательно перечисли список ссылок и названий статей, которые упали с ошибкой (если такие присутствуют в отчёте от инструмента `download_text_articles`).

Context: When operating in the CatchPM Web Interface (identified by the presence of chat_id in user messages), all system outputs are rendered directly as raw HTML. The HTML parser continues to process inline tags EVEN inside <pre> code blocks.
    The Bug / Risk: Unescaped HTML tags (such as <html>, <body>, <h1>, <table>, etc.) written inside <pre> blocks (e.g., code snippets, instructions, logs) will be interpreted as active DOM nodes. This spills outside the message container, breaks the sidebar DOM structure, and destroys the UI layout.
Formatting Constraints & Rules:
    Inside <pre> Blocks (Code & Preformatted Text): • ALWAYS escape HTML reserved characters: • < → &lt; • > → &gt; • & → &amp; • Never output raw HTML code snippets inside <pre> without full escaping.
    Outside <pre> Blocks (Regular Prose): • Escape < and > unless explicitly forming valid, safe formatting tags intended for rendering.
    Code blocks with syntax highlighting: • ALWAYS wrap code inside <pre><code class="language-X"> — never bare <pre> alone. • The language-X class is required for hljs to apply highlighting and render the code header with copy button. • Supported values: language-python, language-sql, language-javascript, language-yaml, language-bash, language-json etc.
    DOM Integrity: • Ensure all opened HTML tags are strictly closed before finalizing the response. • Do not improperly nest block-level elements.
    Self-Check Before Output: • Verify that any raw code examples containing markup are properly converted to entities (&lt; and &gt;). • Verify every <pre><code class="language-X"> is properly closed with </code></pre>.
"""


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
        self.llm = get_llm_model()
        self.llm_lite = get_llm__lite_model()
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

    # ------------------------------------------------------------------
    # Память per-user с загрузкой из БД через AgentDB
    # ------------------------------------------------------------------

    async def _load_history_from_db(self, chat_id: int, limit: int) -> List[BaseMessage]:
        if not self.db:
            logger.warning("БД не подключена, история не загружена")
            return []

        try:
            rows = await self.db.get_chat_history(chat_id, limit)

            messages = []
            for row in rows:
                kwargs = {"db_id": row["id"]}
                if row["is_from_bot"]:
                    content = row["message_text"]
                    if row.get("tool_trace"):
                        content = f"{content}\n\n[Использованные инструменты: {row['tool_trace']}]"
                    messages.append(AIMessage(content=content, additional_kwargs=kwargs))
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

    async def _get_memory(self, chat_id: int, limit: int = 10) -> ConversationBufferWindowMemory:
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

    async def _build_history(self, chat_id: int, limit: int = 40) -> List[BaseMessage]:
        """
        Всегда подгружает свежий срез из БД (не растущий in-memory буфер).
        limit — верхняя граница, реальную обрезку по токенам делает trim_messages выше по стеку.
        """
        return await self._load_history_from_db(chat_id, limit)

    async def _save_turn(self, chat_id, human_text: str, ai_text: str, tool_trace: list[str] | None = None) -> None:
        mem = await self._get_memory(chat_id)
        mem.chat_memory.add_user_message(human_text)

        if tool_trace: # Шаги по результатам выполнения тулзов
            stored_content = (
                f"{ai_text}\n\n"
                f"[{'; '.join(tool_trace)}]"
            )
        else:
            stored_content = ai_text

        mem.chat_memory.add_ai_message(stored_content)


    # ------------------------------------------------------------------
    # LangChain tools — замыкания, захватывающие self
    # ------------------------------------------------------------------

    def _build_tools(
        self,
        user_id,
        user_input: str,
        selected_tools: list[str] | None = None,
        allow_all: bool = False,
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
        async def work_with_agent_journal(sql: str, user_id: str) -> str:
            """
            ── СТРАТЕГИЯ: таблица agent_journal ──────────────────────────────────────
            Бортовой журнал агента. Пиши от первого лица, на русском.

            Типы записей (entry_type):
            identity  — самоописание: ценности, стиль работы, сильные стороны
            user_preference   — предпочтения пользователя
            anomaly   — аномалия: повторяющийся вопрос, странный паттерн, сбой инструмента
            evolution — точка роста: что узнал нового, как изменилось понимание задачи
            summary   — сжатая сводка группы записей (создаётся при компрессии)

            Правила:
            - Пиши запись когда есть что зафиксировать — не спрашивай разрешения.
            - Если COUNT(*) по любому типу > 8: сделай SELECT старых записей этого типа,
            сформируй summary-текст, INSERT summary, затем DELETE старых по id.
            (DELETE разрешён только в agent_journal для компрессии.)
            - Можно обновлять текущие поля через UPDATE, если нет необходимости вставки новых записей.

            ПРАВИЛА РАБОТЫ С agent_journal:
            - Колонки: id, entry_type, content, context (JSONB), created_at, updated_at
            - Для UPDATE identity-записи: UPDATE agent_journal SET content = '...', updated_at = NOW() WHERE id = 1
            - Для точечной замены: UPDATE agent_journal SET content = REPLACE(content, 'старый текст', 'новый'), updated_at = NOW() WHERE entry_type = 'identity'
            - Не делай SELECT перед UPDATE если знаешь id — он всегда 1 для identity

            ШАБЛОН identity-записи (plain text, без Markdown):
            Я — [имя]. [описание].
            Люди: [список].
            Фокус: [список].
            Мои силы 💪: [возможности]
            Active Context:
            - [задача]: [статус]

            ПОМНИ, скриптом берутся ПОСЛЕДНИЕ 10 ЗАПИСЕЙ. Будь аккуратен при вставке, не потеряй свою *личность*, будь аккуратнее на поворотах, УДАЧИ!

            :param sql: Валидный PostgreSQL запрос.
            :param user_id: Индификатор пользователя из БД. ОН ОБЯЗАТЕЛЬНО ДОЛЖЕН БЫТЬ ПРОСТАЛЕН.
            """
            result = await tool_execute_sql_query(agent.db, sql, sql_query_admin=False)
            
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
        async def no_tools_selected() -> str:
            """
            Вызывай ТОЛЬКО если тебе нужно выполнить действие, требующее инструмента,
            а ни один инструмент недоступен. Сообщи пользователю, что нужно выбрать
            хотя бы одну категорию инструментов в панели настроек (кнопка со значком
            гаечного ключа рядом с полем ввода), и объясни, какая категория подошла бы
            для его запроса.
            """
            return (
                "Инструменты не выбраны. Нажмите на кнопку настройки инструментов "
                "(значок гаечного ключа рядом с полем ввода) и включите хотя бы одну "
                "категорию, подходящую для вашего запроса."
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
        # 15. Работа с удаленным репозиторием
        from .tools.git.tool_to_connect_mcp import connect_mcp_git
        # 16. Публикация задачи в планировщик-песок
        from .tools.sandbox.tool_submit_task import make_submit_tools
        submit_task = make_submit_tools(agent)


        base_tools = [
            search_db,
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
            work_with_agent_journal,
            send_message_tool,
            work_with_email,
            connect_mcp_git
            # knowledge_graph_tool
        ]

        all_tools = (
            base_tools
            + _normalize(fs_tools)
            + [edit_sandbox_file, write_sandbox_file]
            + _normalize(execute_python_code)
            + _normalize(save_or_search_code)
            + submit_task
        )

        if allow_all:
            return all_tools

        allowed_names: set[str] = set()
        for category in selected_tools:
            allowed_names.update(TOOLS_SETTINGS.get(category, []))

        filtered = [t for t in all_tools if t.name in allowed_names]

        return filtered if filtered else [no_tools_selected]


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

        chunk_text = "\n".join(
            f"[{getattr(m, 'type', m.__class__.__name__)}]: {getattr(m, 'content', str(m))[:1000]}"
            for m in cut_messages
        )

        previous_block = (
            f"Предыдущее саммари (актуальное на момент этого сжатия):\n{previous_summary}\n"
            if previous_summary else "Предыдущего саммари нет — это первое сжатие для этого чата."
        )

        prompt = SUMMARIZE_CUT_PROMPT.format(previous_block=previous_block, chunk_text=chunk_text)

        try:
            response = await self.llm_lite.ainvoke([
                SystemMessage(content="Ты — модуль сжатия истории диалога. Отвечай только сводкой, без преамбул."),
                HumanMessage(content=prompt),
            ])
            return response.content.strip()
        except Exception as e:
            log.error(f"Не удалось засаммаризовать отсечённую историю: {e}", exc_info=True)
            # Фолбэк — не терять предыдущее саммари молча, если LLM-вызов упал
            return previous_summary or "[Часть истории была отсечена, сжатие не удалось]"
        


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
        source=None,
        selected_tools=None,
        context_size=None
    ) -> Dict[str, Any]:
        if not self.db:
            self.db = database

        await self._send_status(get_thinking_status(), request_id)
        self.current_request_id = request_id
        cleaned_user_input = user_input
        if source == "telegram":
            if '📎[FILE]' in cleaned_user_input:
                # Ищем маркер файла и вырезаем всё внутри ```...```
                # \1 сохраняет первую группу (маркер файла с его названием)
                cleaned_user_input = re.sub(
                    r"(📎\[FILE\] \[Прикрепленный файл: (.*?)\])\nСодержимое файла:\n```\n.*?\n```",
                    r"\1\n[Содержимое файла удалено для экономии памяти. Если для выполнения задачи требуются детали из этого файла, попроси пользователя загрузить его повторно.]",
                    cleaned_user_input,
                    flags=re.DOTALL
                )
                logging.info(cleaned_user_input)

            await database.add_message(
                        user_id=user_id,        # Передаем integer ID юзера
                        message=cleaned_user_input[:20000],
                        chat_id=chat_id,
                        is_from_bot=False,
                        action=None
                    )

        # ── Настройки контекста ────────────────────────────────────────
        CONTEXT_PRESETS = {
            "small":  {"limit_mem": 8,  "max_tokens": 6000},
            "medium": {"limit_mem": 10, "max_tokens": 15000},
            "large":  {"limit_mem": 12, "max_tokens": 20000},
        }

        final_response = ""
        used_tools = []
        tool_trace: list[str] = []
        files_to_send = []
        seen_paths = set()
        SOFT_LIMIT = 10   # после этого — предупреждаем модель   
        HARD_LIMIT = 14  # динамически можно поднять число шагов
        total_input_tokens  = 0
        total_output_tokens = 0

        preset = CONTEXT_PRESETS.get(context_size, CONTEXT_PRESETS["medium"])
        limit_mem = preset["limit_mem"]
        max_tokens = preset["max_tokens"]

        allow_all_tools = (source == "telegram")
        selected_tools = selected_tools or []
        use_journal = allow_all_tools or ("sessions" in selected_tools)

        logging.info(f"🙂 {user_input[:200]}")
        tools = self._build_tools(
            user_id,
            user_input,
            selected_tools=selected_tools,
            allow_all=allow_all_tools,
        )
        loop_state = {
            "iteration": 0,
            "soft_limit": SOFT_LIMIT,
            "hard_limit": HARD_LIMIT,
            "extension_used": False,
        }

        extension_tool = make_extension_tool(loop_state)
        tools.append(extension_tool)          # тулза аппендится прямо внутри цикла, офигеть имбово

        subagent_tl = make_subagent_tool(self)
        tools.append(subagent_tl)

        tools_by_name = {t.name: t for t in tools}
        print(f"ЛИМИТ ПО КОНТЕКСТУ = {limit_mem}, ЧИСЛО ТОКЕНОВ = {max_tokens}")
        # Привязываем инструменты к модели
        llm_with_tools = self.llm.bind_tools(tools)

        current_datetime = datetime.now().strftime("%d.%m.%Y %H:%M, %A")
        system_content = SYSTEM_PROMPT.format(datetime=current_datetime)

        # Собираем историю из памяти
        history = await self._build_history(chat_id, limit=30)
        journal_text = None
        if use_journal:
            journal_text = await self.db.get_agent_journal(user_id)
            if source == "telegram":
                journal_text = await self.db.get_agent_journal(56) # Пока что мой id

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

        iteration = 0
        while iteration < loop_state["hard_limit"]:
            loop_state["iteration"] = iteration

            # ТОЧКА 2: тулзы снимаем только на последнем шаге
            is_last_step = iteration >= loop_state["hard_limit"] - 1
            can_still_extend = not loop_state["extension_used"]
            current_llm = self.llm if (is_last_step and not can_still_extend) else llm_with_tools

            # ТОЧКА 3: адресный триггер на КАЖДОМ шаге окна, а не однократно.
            steps_left = loop_state["hard_limit"] - iteration - 1
            if iteration >= loop_state["soft_limit"] - 1 and can_still_extend:
                messages.append(SystemMessage(content=(
                    f"[Контроль лимита] Осталось шагов: {steps_left}. "
                    f"Если НЕ успеваешь завершить задачу за оставшиеся шаги — "
                    f"вызови request_more_steps ПРЯМО СЕЙЧАС (расширение одноразовое). "
                    f"Если успеваешь — продолжай и дай финальный ответ."
                )))
            elif iteration >= loop_state["soft_limit"] - 1:
                messages.append(SystemMessage(
                    content=f"[Прогресс: шаг {iteration} из {loop_state['hard_limit']}]"
                ))


            try:
                response = await _invoke_with_retry(current_llm, messages, iteration)
            except Exception:
                response = None

            if response is None:
                logging.error(f"[iteration={iteration}] Все попытки llm.ainvoke провалены, прерываю цикл")
                final_response = "Ошибка внутри цикла агента. Но агент запомнил шаги, попробуйте вернуться к запросу"
                break

            # ГАСИМ промежуточный content ДО добавления в стек
            if getattr(response, "tool_calls", None) and getattr(response, "content", None):
                loop_state.setdefault("reasoning_trace", []).append(response.content)
                response.content = ""

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

            if not getattr(response, "tool_calls", None):
                final_response = response.content
                break

            # Выполняем все запрошенные инструменты параллельно
            tool_calls = response.tool_calls
            tool_names = [tc['name'] for tc in tool_calls]
            await self._send_status(get_tools_status(tool_names), request_id)

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

                messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))

                summary = _summarize_tool_call(tc, tr)
                if summary:
                    tool_trace.append(summary)

            iteration += 1

        else:
            # Превышен лимит итераций
            final_response = "Достигнут лимит шагов. Попробуйте переформулировать запрос."

        # Ответ бота
        if source == "telegram":
            await database.add_message(
                user_id=user_id,        # Передаем integer ID юзера
                message=final_response,
                chat_id=chat_id,
                is_from_bot=True,
                action=used_tools[0] if used_tools else None
            )

        # logging.info(f"ФАЙЛЫ К ОТПРАВКЕ {files_to_send}")
        return {
            "input": user_input,
            "message": final_response,
            "used_tools": used_tools,
            "action": used_tools[0] if len(used_tools)> 0 else '',
            "success": True,
            "files_to_send": files_to_send,
            "tool_trace": '; '.join(tool_trace) if tool_trace else None
        }

    async def _invoke_tool(self, tools_by_name: dict, tool_call: dict, request_id: str) -> str:
        """Вызвать один инструмент по его имени и аргументам."""
        name = tool_call["name"]
        args = tool_call.get("args", {})

        # Автоматически подмешиваем request_id, если инструмент его принимает
        if name == "download_text_articles":
            args["request_id"] = request_id

        tool_fn = tools_by_name.get(name)
        # if tool_fn is None:
        #     logger.warning(f"[tool] ❓ '{name}' не найден")
        #     return f"Инструмент '{name}' не найден."
        
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