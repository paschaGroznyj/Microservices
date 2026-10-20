import asyncio
import logging
import json
import contextvars
import os
import re
import ast
import httpx
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain.tools import tool
from langchain_core.messages import BaseMessage
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import ToolMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

from .core.agent_provider import (get_llm_lite_model, get_llm_model,
get_llm_model_gpt, get_llm_model_claude, get_llm_model_gemini,
get_llm_lite_model_gpt_mini, get_llm_lite_model_gemini_flash,
get_llm_model_glm)
from .core.botocore_client import OBSWorker
from .core.config import (
    ARTICLES_FOLDER, VECTOR_DB_PATH, STORAGE_PATH,
    METADATA_FILE, PORT_GATEWAY_PORT, PORT_GATEWAY_SIGMA_PORT, ADMINS_INFO,
    PORT_PARSERS_SERVICE, get_role, ADMINS_BY_USER_ID,
    ADMINS_BY_TG_ID, get_role_tg, TG_BOT_PUSH_URL
)
from .core.settings import TOOLS_SETTINGS, UNIVERSAL_TOOLS, ADMIN_ONLY_CATEGORIES
from .prompts.summary_prompt import SUMMARIZE_CUT_PROMPT, TOOL_TRACE_SUMMARY_PROMPT
from .prompts.system_prompt import SYSTEM_PROMPT
from .title_generator import generate_chat_title, is_first_turn

from .tool_status_messages import get_tool_status, get_thinking_status, get_tools_status
from .tools.sessions.tool_request_extension import make_extension_tool
from .tools.subAgents.make_subagent import make_subagent_tool
from .tools.journal.tool_agent_journal import make_journal_tool
from .judge import judge_final_answer
from .supervisor import run_supervisor
from .context_compactor import compact_messages, TOKEN_SOFT_LIMIT


GATEWAY_URL = f"http://api_gateway:{PORT_GATEWAY_PORT}"
GATEWAY_SIGMA_URL = f"http://api_gateway_sigma:{PORT_GATEWAY_SIGMA_PORT}"

# Redis-канал для стриминга чанков на sigma-гейтвей (у sigma нет WS, стрим льётся через pub/sub).
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

logger = logging.getLogger(__name__)


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
# HarnessAgent — LangChain Tools + Window Memory
# ================================================================
MAX_STDOUT_CHARS = 2000  # Настройте максимальную длину (например, 2000 символов)
MAX_TOOL_MSG_CHARS = int(os.getenv("MAX_TOOL_MSG_CHARS", "8000"))
MAX_TRACE_FROM_DB_CHARS = int(os.getenv("MAX_TRACE_FROM_DB_CHARS", "2500"))
TRACE_SUMMARY_THRESHOLD = int(os.getenv("TRACE_SUMMARY_THRESHOLD", "6000"))
MAX_TRACE_HARD_CHARS = int(os.getenv("MAX_TRACE_HARD_CHARS", "12000"))

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
    # "request_more_steps",
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
    if name == "search_internet":
        # результат — многострочный текст с [S1]...[S5], вытащим только заголовки+URL
        lines = str(result).split("\n")
        titles = [l for l in lines if l.startswith("[S")]
        compact = " | ".join(titles)
        return f"search_internet(query='{args.get('query')}') → {compact}"
    if name == "parse_web_page":
        return f"{name} Передаваемые ссылки в парсер ({args}). -> {str(result)[:1000]}"
    if name == "generate_image":
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
    # if name == "request_more_steps":
    #     return f"{name} ШАГ-БУСТ → {str(result)}"
    if name == "execute_shell_commands":
        _cmds = args.get("commands", args)
        _cmd_s = str(_cmds)
        if len(_cmd_s) > 400:
            _cmd_s = _cmd_s[:400] + f"…[+{len(_cmd_s) - 400} симв]"
        if isinstance(result, dict):
            _ok = result.get("ok")
            _out = str(result.get("stdout", ""))
            _err = str(result.get("stderr", ""))
            _rc = result.get("exit_code")
            _res_s = f"ok={_ok} rc={_rc} stdout={_out[:800]}"
            if _err.strip():
                _res_s += f" stderr={_err[:300]}"
        else:
            _res_s = str(result)[:800]
        return f"execute_shell_commands({_cmd_s}) → {_res_s}"
    _args_s = str(args)
    if len(_args_s) > 500:
        _args_s = _args_s[:500] + f"…[+{len(_args_s) - 500} симв]"
    return f"{name}({_args_s} -> {str(result)[:1000]})"


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

def _merge_tool_chunks(chunk, calls: dict, idx_map: dict, state: dict) -> None:
    """Копит tool_call_chunks по id (а не по index). Имя пишется один раз."""
    for tcc in getattr(chunk, "tool_call_chunks", None) or []:
        cid, idx = tcc.get("id"), tcc.get("index")
        if cid:                                   # пришёл id: новый или уже известный вызов
            if cid not in calls:
                calls[cid] = {"id": cid, "name": "", "args": ""}
            key = cid
            if idx is not None:
                idx_map[idx] = key                # последний id на этом index выигрывает
            state["cur"] = key
        else:                                     # продолжение: ищем по index, иначе текущий
            key = idx_map.get(idx, state.get("cur"))
            if key is None:                       # id вообще не было — заводим синтетический
                key = f"call_{len(calls)}"
                calls[key] = {"id": key, "name": "", "args": ""}
                state["cur"] = key
                if idx is not None:
                    idx_map[idx] = key
        e = calls[key]
        if tcc.get("name") and not e["name"]:     # имя — ОДИН раз, не конкатенируем
            e["name"] = tcc["name"]
        e["args"] += tcc.get("args") or ""


def _build_final(aggregate, calls: dict) -> AIMessage:
    tool_calls, invalid = [], []
    for e in calls.values():
        raw = e["args"].strip() or "{}"
        try:
            args = json.loads(raw)
            tool_calls.append({"name": e["name"], "args": args, "id": e["id"], "type": "tool_call"})
        except json.JSONDecodeError:
            logging.warning(f"[stream] битый JSON args у {e['name']}: {raw[:200]!r}")
            invalid.append({"name": e["name"], "args": raw, "id": e["id"],
                            "error": "invalid json", "type": "invalid_tool_call"})
    ak = dict(aggregate.additional_kwargs or {})
    ak.pop("tool_calls", None)                    # сырой дубль провайдера не нужен
    return AIMessage(
        content=aggregate.content,
        additional_kwargs=ak,
        response_metadata=aggregate.response_metadata,
        tool_calls=tool_calls,
        invalid_tool_calls=invalid,
        usage_metadata=aggregate.usage_metadata,
        id=aggregate.id,
    )


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
                # return _build_final(aggregate, calls)
                return aggregate
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

# ── Стриминг аргументов тулов как псевдо-reasoning ───────────────────────
# Модели coder (gpt) и gemini не отдают reasoning в стрим — UI подвисает в
# тишине на время tool-вызова. Для них показываем сами аргументы тулзы
# (обрезанные), чтобы интерфейс оставался отзывчивым. Переиспользуем штатный
# live-стрим: фронт рисует это приглушённым reasoning-стилем.
STREAM_TOOL_ARGS_ENABLED = os.getenv("STREAM_TOOL_ARGS", "1") not in ("0", "false", "False", "")
STREAM_ARGS_MODELS = set(
    m.strip() for m in os.getenv("STREAM_TOOL_ARGS_MODELS", "coder,gemini").split(",") if m.strip()
)
STREAM_ARGS_MAX_LEN = int(os.getenv("STREAM_TOOL_ARGS_MAX_LEN", "1000"))


def _fmt_tool_calls_preview(tool_calls: list) -> str:
    """Человекочитаемая сводка вызовов тулов для псевдо-reasoning стрима.
    Формат: "• имя_тула(args…)" построчно. Общая длина режется до лимита."""
    lines = []
    for tc in tool_calls:
        name = tc.get("name", "?")
        args = tc.get("args", {}) or {}
        try:
            rendered = _fmt_args(name, args)
        except Exception:
            rendered = str(args)
        lines.append(f"• {name}({rendered})")
    text = "\n".join(lines)
    if len(text) > STREAM_ARGS_MAX_LEN:
        text = text[:STREAM_ARGS_MAX_LEN].rstrip() + "…"
    return text


def _fmt_args(name: str, args: dict) -> str:
    """Форматирует аргументы для лога — код сокращает."""
    parts = []
    for k, v in args.items():
        s = str(v)
        # Если тулза кодовая и аргумент похож на код — показываем РАЗМЕР + короткий preview,
        # а НЕ усечённый хвост "…[+N]" (он провоцировал ложный вывод «код обрезался при передаче»).
        is_code = name in CODE_TOOLS or k in ("code", "content", "new_str", "old_str")
        if is_code and len(s) > MAX_CODE_LEN:
            preview = s[:160].replace("\n", "\\n").rstrip()
            parts.append(f"{k}=<{len(s)} bytes, preview={preview!r}>")
            continue
        limit = MAX_CODE_LEN if is_code else MAX_ARG_LEN
        if len(s) > limit:
            s = s[:limit].rstrip() + f"…[+{len(s)-limit}]"
        parts.append(f"{k}={s!r}")
    return ", ".join(parts)

def _normalize(t):
    """Превращает tool или list[tool] в list."""
    return t if isinstance(t, list) else [t]


# SYSTEM_PROMPT и SCIENTIFIC_RESEARCH_MODULE вынесены в prompts/system_prompt.py


class HarnessAgent:
    def __init__(self, database=None):
        base_dir = Path(__file__).resolve().parent
        self.db = database
        self.articles_path = base_dir / Path(STORAGE_PATH) / ARTICLES_FOLDER
        self.name_meta_dir = base_dir / Path(STORAGE_PATH)
        self.digest_path = base_dir / "digests"
        # Фоновая rolling-саммаризация: lock+task per chat_id (одна реплика)
        self._summary_locks = {}
        self._summary_tasks = {}
        self.AGENT_MODELS = {'coder': get_llm_model_gpt(),
                            'designer': get_llm_model_claude(),
                            'universal': get_llm_model_glm(),
                            'gemini': get_llm_model_gemini()
                            }
        self.llm_lite = get_llm_lite_model()
        # Цепочка фолбэков для лайт-модели (суммаризация и прочие служебные
        # вызовы, летящие с фронта): по умолчанию haiku, при отказе —
        # gpt-5-mini, затем gemini-3-flash-preview.
        self.llm_lite_fallbacks = [
            get_llm_lite_model_gpt_mini(),
            get_llm_lite_model_gemini_flash(),
            get_llm_model_glm()
        ]
        self.obs_loader = OBSWorker()
        self.services = {"parsers": {"url": f"http://parsers_service:{PORT_PARSERS_SERVICE}",}}
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
                _txt = row.get("message_text")
                if _txt is None or not str(_txt).strip():
                    continue
                kwargs = {"db_id": row["id"]}
                if row["is_from_bot"]:
                    if row.get("tool_trace"):
                        kwargs["tool_trace"] = _truncate_text(
                            str(row["tool_trace"]), max_chars=MAX_TRACE_HARD_CHARS
                        )
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
        async def get_db_schema() -> str:
            """Показать актуальную схему таблиц базы знаний (для составления SQL-запроса)."""
            return await tool_get_schema(agent.db)
        
        @tool
        async def execute_sql_query(sql: str) -> str:
            """
            Execute an SQL query to the database (SELECT, INSERT, UPDATE).
            
            ACCESS AND RIGHTS MAP (ROLE: knowledge_manager):

            LAW WHEN WORKING WITH THE DB:
            If the SQL query returned “no results” when searching for specific articles/facts that you were going to cite, you are OBLIGED to inform the user: “I didn’t find any exact matches in the database for topic X, I can search more broadly / 
            give a general overview without reference to specific IDs." 
            It is FORBIDDEN to substitute plausible-sounding names for an unsuccessful result,
            especially with formulations like 'I checked/clarified via SQL'
            if the actual query result was empty or irrelevant.

            A statement about saving data to the database without actually calling execute_sql_query 
            In the same move, it is the creation of false information and is strictly prohibited.
            
            STRICT RESTRICTIONS:
            - DIRECT DELETE operations are STRICTLY PROHIBITED at the role permission level.
            - To propose deleting a record from 'knowledge', perform an INSERT into 'knowledge_review' 
            specifying 'knowledge_id' and 'reason'.
            - Before generating queries, be sure to call 'get_db_schema' to check the structure.

            THE STRATEGY OF WORKING WITH THE knowledge TABLE is:
            - NEW ENTRY (INSERT): create whenever the source is new (new article, new URL, new author).
            One article = one entry. Don't combine different sources into one line.

            - UPDATE: only if an entry with this source already exists
            and you need to add or correct the text. Check through SELECT before UPDATING.

            - ALWAYS CHECK the time difference between the time the entry was created and the time it was updated. NEVER tell the user that the entry has been updated. If there is no difference, perform the update yourself.

            - MERGING: DO NOT merge entries from different sources, even if the topics are similar.
            Similarity of topics is not a reason to merge. Semantic search will find related entries on its own.

            - In metadata_knowledge, always specify the source (URL or article identifier).
            so that the origin of knowledge can be traced. Without an explicit link, you can leave it blank.

            - POINT TEXT UPDATE (UPDATE + REPLACE):
            If you need to replace only a specific phrase/word, rather than rewrite the entire text:
            1. First, do SELECT text_knowledge WHERE id = N to see the exact wording.
            2. Perform substitution via REPLACE:
                UPDATE knowledge 
                SET text_knowledge = REPLACE(text_knowledge, 'strictly accurate old text', 'new text'),
                    updated_at = NOW()
                WHERE id = N;

            :param sql: Valid PostgreSQL query.
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
        async def no_tools_selected() -> str:
            """
            Call it ONLY if you need to perform an action that requires a tool.,
            and not a single tool is available. Tell the user what to choose.
            at least one category of tools in the settings panel (the button with the icon
            a wrench next to the input field), and explain which category would be suitable.
            for his request. DO NOT invent data and DO NOT try to answer from memory —
            the task cannot be performed correctly without tools.
            """
            return (
                "⚠️ Tools are not selected. To complete this request, you need to "
                "enable at least one category of tools on the main page: "
                "click on the Tools settings button (wrench icon "
                "next to the input field) and select the appropriate category, then "
                "repeat the request. Without the included tools, I cannot collect "
                "data and will not answer from memory, so as not to mislead you."
            )

        # 2. Парсинг одиночной страницы, не сырой текст, очищенный, ссылка отправляет в сервис parsers
        from .tools.parsers.tool_parse_page import make_parse_page_tool
        parse_web_page = make_parse_page_tool(agent)
        # 3. Поиск в интернете с применением поисковика
        from .tools.parsers.web_search import make_web_search
        search_internet = make_web_search()
        # 5. Точка эндпоинта для создания сессии start/end
        from .tools.sessions.tool_session_point import make_session_point_tool
        session_point = make_session_point_tool(agent)
        # 5.1 Планировщик собственных Telegram-триггеров (агент сам ставит
        from .tools.telegram.tool_manage_trigger import make_manage_trigger_tool
        manage_trigger = make_manage_trigger_tool(agent, chat_id, user_id)
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
        analyze_image = make_analyze_image_tool(agent, images, agent_model=agent_model, chat_id=chat_id)

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

        # Admin-only paid media generation via RouterAPI (routerai.ru):
        # text-to-video (async task + polling) and text-to-speech. Gated by
        # the admin-only "media_gen" category in TOOLS_SETTINGS.
        from .tools.content.tool_generate_video import make_generate_video_tool
        from .tools.content.tool_generate_audio import make_generate_audio_tool
        from .tools.content.tool_generate_audio import make_generate_music_tool
        generate_video = make_generate_video_tool(agent, images)
        generate_audio = make_generate_audio_tool(agent)
        generate_music = make_generate_music_tool(agent)

        base_tools = [
            analyze_image,
            generate_image,
            find_generated_image,
            generate_video,
            generate_audio,
            generate_music,
            get_db_schema,
            execute_sql_query,
            parse_web_page,
            search_internet,
            session_point,
            manage_trigger,
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

        # Admin-only categories
        try:
            _admin_index = ADMINS_BY_TG_ID if is_telegram else ADMINS_BY_USER_ID
            _is_admin = int(user_id) in _admin_index
        except (TypeError, ValueError):
            _is_admin = False
        allowed_names: set[str] = set()
        for category in selected_tools:
            if category in ADMIN_ONLY_CATEGORIES and not _is_admin:
                continue
            allowed_names.update(TOOLS_SETTINGS.get(category, []))

        # Универсальные тулы добавляются всегда, вне зависимости от выбранных
        # категорий — они не привязаны к режиму (напр. анализ изображений).
        allowed_names.update(UNIVERSAL_TOOLS)

        filtered = [t for t in all_tools if t.name in allowed_names]

        # Даже если пользователь не выбрал ни одной категории, универсальные
        # тулы должны остаться доступны — не подменяем их заглушкой.
        universal_present = [t for t in all_tools if t.name in UNIVERSAL_TOOLS]

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

    async def _summarize_tool_trace(self, raw_trace: str) -> str:
        """LLM-сжатие сырого tool_trace ОДНОГО запроса в компактную md-таблицу.

        Вызывается на выходе из run_agent, если сырой trace длиннее
        TRACE_SUMMARY_THRESHOLD. Использует основную модель агента
        (_main_ainvoke) и TOOL_TRACE_SUMMARY_PROMPT. Fail-open: при любой
        ошибке / пустом ответе деградируем на _truncate_text, чтобы падение
        LLM никогда не роняло сохранение сообщения.
        """
        try:
            prompt = TOOL_TRACE_SUMMARY_PROMPT.format(raw_trace=raw_trace)
            resp = await self._main_ainvoke([HumanMessage(content=prompt)])
            summary = (getattr(resp, "content", "") or "").strip()
            if summary:
                logging.info(
                    f"[trace-summary] сырой trace {len(raw_trace)} симв → "
                    f"сводка {len(summary)} симв"
                )
                return summary
            logging.warning("[trace-summary] пустой ответ модели, деградирую на обрезку")
        except Exception as e:
            logging.error(f"[trace-summary] ошибка LLM-сжатия trace: {e}, деградирую на обрезку")
        return _truncate_text(raw_trace, max_chars=MAX_TRACE_FROM_DB_CHARS)

    def _spawn_summary_refresh(self, chat_id, new_cut_messages, prev_summary, max_covered_id, total_cut):
        """Фоновая rolling-саммаризация (defer на N+1).

        Агент текущего запроса её НЕ ждёт: стартует на prev_summary, а новый
        summary ляжет в БД к следующему ходу. Одна реплика → asyncio.Lock per chat."""
        prev = self._summary_tasks.get(chat_id)
        if prev is not None and not prev.done():
            logger.info(f"[ROLLING SUMMARY|bg] chat={chat_id}: задача уже в работе, скип спавна")
            return
        task = asyncio.create_task(
            self._summary_refresh_job(chat_id, new_cut_messages, prev_summary, max_covered_id, total_cut)
        )
        self._summary_tasks[chat_id] = task
        # чтобы GC не убил таск и не терялись исключения
        task.add_done_callback(lambda t: self._summary_tasks.pop(chat_id, None))

    async def _summary_refresh_job(self, chat_id, new_cut_messages, prev_summary, max_covered_id, total_cut):
        lock = self._summary_locks.setdefault(chat_id, asyncio.Lock())
        if lock.locked():
            logger.info(f"[ROLLING SUMMARY|bg] chat={chat_id}: lock занят, другой ход уже сжимает, скип")
            return
        async with lock:
            try:
                # перечитываем покрытие: вдруг предыдущая фоновая задача уже сдвинула указатель
                cur = await self.db.get_rolling_summary(chat_id)
                cur_covered = cur["covered_up_to_id"] if cur else 0
                fresh = [m for m in new_cut_messages if m.additional_kwargs.get("db_id", 0) > cur_covered]
                if not fresh:
                    logger.info(f"[ROLLING SUMMARY|bg] chat={chat_id}: всё уже покрыто другим ходом, скип")
                    return
                base_summary = cur["summary_text"] if cur else prev_summary
                text = await self._summarize_cut_messages(fresh, base_summary)
                await self.db.upsert_rolling_summary(chat_id, text, covered_up_to_id=max_covered_id)
                logger.info(
                    f"[ROLLING SUMMARY|bg] chat={chat_id}: сжато {len(fresh)} "
                    f"(из {total_cut} отсечённых), длина саммари={len(text)}, covered_up_to={max_covered_id}"
                )
            except Exception as e:
                logger.error(f"[ROLLING SUMMARY|bg] chat={chat_id} FAILED: {e}")

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
            "gemini": "Gemini"
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

        if target == "telegram":
            # Буфер reasoning — СТРОГО request-local (ContextVar), а не self.*,
            # иначе параллельные чаты пишут в один список и reasoning смешивается.
            ctx = self._request_ctx.get()
            if not isinstance(ctx, dict):
                return
            buf = ctx.setdefault("tg_reasoning_buf", [])
            if kind == "live":
                if chunk:
                    buf.append(chunk)
                return
            if kind == "reasoning_end":
                block = "".join(buf).strip()
                buf.clear()
                if block:
                    # Адрес плашки "Думаю..." — ФИЗИЧЕСКИЙ числовой tg chat id,
                    # а НЕ session_id (после мульти-сессий chat_id = UUID сессии).
                    # Фолбэк на chat_id для обратной совместимости (старые клиенты).
                    rid = ctx.get("tg_chat_id") or ctx.get("chat_id")
                    await self._tg_edit_reasoning(rid, block)
                return
            if kind == "final_commit":
                # финал уедет по Kafka целиком — просто чистим буфер
                buf.clear()
                return
            return

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

    async def _tg_edit_reasoning(self, chat_id, text: str) -> None:
        """Трансляция reasoning-блока в Telegram: редактирует плашку "Думаю..."
        целым блоком (последний блок размышлений, обрезка до 500 символов).
        Дебаунс ~1с на chat_id, чтобы не упираться в rate limit edit_message_text.
        Любые ошибки гасим — трансляция не должна ронять основной ответ."""
        if not chat_id or not text or not TG_BOT_PUSH_URL:
            return
        # Endpoint /tg/reasoning ждёт chat_id:int (это числовой tg chat id).
        # Если прилетел session UUID (строка не из цифр) — трансляция невозможна,
        # тихо выходим (не роняем ответ).
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            return
        try:
            snippet = text.strip()
            if len(snippet) > 500:
                snippet = snippet[:500].rstrip() + "..."
            # Дебаунс на chat_id
            now = asyncio.get_event_loop().time()
            dbg = getattr(self, "_tg_reasoning_last", None)
            if dbg is None:
                dbg = {}
                self._tg_reasoning_last = dbg
            last = dbg.get(chat_id, 0.0)
            if now - last < 1.0:
                return
            dbg[chat_id] = now
            url = TG_BOT_PUSH_URL.replace("/push_message", "/tg/reasoning")
            client = await self._get_http_client()
            await client.post(
                url,
                json={"chat_id": chat_id, "text": snippet},
                timeout=3,
            )
        except Exception as e:
            logger.debug(f"[tg-reasoning] не удалось отправить блок: {e}")


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
        agent_model="coder",
        source=None,
        selected_tools=None,
        context_size=None,
        images=None,
        gateway_target: str = "main",
        tg_chat_id=None,
    ) -> Dict[str, Any]:
        if not self.db:
            self.db = database

        ctx_token = self._request_ctx.set({
            "request_id": request_id,
            "gateway_target": gateway_target or "main",
            "chat_id": chat_id,
            "tg_chat_id": tg_chat_id,
            "user_id": user_id,
            "message_id": None,
            "agent_model": agent_model,
            "pending_generated_images": [],
            # request-local буфер живого reasoning для Telegram (изоляция параллельных чатов)
            "tg_reasoning_buf": [],
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
        # Буфер живого reasoning для Telegram теперь в request-local ctx ("tg_reasoning_buf")
        cleaned_user_input = user_input
        try:
            # ── Настройки контекста ────────────────────────────────────────
            CONTEXT_PRESETS = {
                "small":  {"limit_mem": 8,  "max_tokens": 15_000},
                "medium": {"limit_mem": 10, "max_tokens": 30_000},
                "large":  {"limit_mem": 12, "max_tokens": 60_000},
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
            role_block = get_role_tg(user_id) if is_telegram else get_role(user_id)
            system_content = SYSTEM_PROMPT.format(datetime=current_datetime, role_block=role_block)
            # Медиа-хранилище чата (Redis, пер-chat_id): картинки/файлы из недавних
            # сообщений — чтобы агент видел их даже когда в текущем сообщении вложений нет.
            try:
                from agent.media import media_store as _media_store
                _media_block = await _media_store.build_media_prompt_block(chat_id)
                if _media_block:
                    system_content += _media_block
            except Exception as _e:
                logging.warning(f"[media_store] prompt block fail: {_e}")
            # Telegram: подмешиваем HTML-инструкцию форматирования + per-chat промпт из БД.
            if is_telegram:
                try:
                    from agent.telegram.tg_integration import (
                        get_tg_chat_settings, build_tg_system_suffix,
                    )
                    _tg_settings = await get_tg_chat_settings(self.db, chat_id)
                    _tg_name = _tg_settings.get("agent_name")
                    if _tg_name and _tg_name.strip():
                        system_content += (
                            "\n\nВ этом чате тебя зовут «" + _tg_name.strip() + "». "
                            "Участники обращаются к тебе по этому имени — воспринимай его как своё и при необходимости так и представляйся."
                        )
                    system_content += "\n\n" + _tg_settings.get("system_prompt")
                except Exception as _e:
                    logging.warning(f"[tg] system suffix fail: {_e}")

            journal_text = None
            if use_journal:
                print(f"USER ID: {user_id}")
                tools.append(make_journal_tool(self, chat_id, user_id))
                journal_text = await self.db.get_agent_journal(chat_id)
                print(f"ЖУРНАЛ АГЕНТА: длина = {len(journal_text)   }")

            tools_by_name = {t.name: t for t in tools}
            # Имена реально доступных агенту тулов — для каталога фонового супервизора
            loop_state["tool_names"] = list(tools_by_name.keys())
            llm_with_tools = llm.bind_tools(tools)
            session_summary = await self.db.get_last_session_summary(chat_id)

            # Собираем историю из памяти
            history = await self._build_history(chat_id, limit=30)
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

            if cut_count > 0:
                cut_messages_all = history[:cut_count]
                # берём только те, что ЕЩЁ не входили в прошлый summary
                new_cut_messages = [
                    m for m in cut_messages_all
                    if m.additional_kwargs.get("db_id", 0) > previous_covered_id
                ]

                if new_cut_messages:
                    # DEFER: не ждём LLM-сжатие в текущем запросе — агент стартует
                    # сразу на previous_summary_text, а новый summary считается фоном
                    # и подхватится следующим запросом (N+1).
                    rolling_summary_text = previous_summary_text
                    max_covered_id = max(m.additional_kwargs.get("db_id", 0) for m in cut_messages_all)
                    self._spawn_summary_refresh(
                        chat_id, new_cut_messages, previous_summary_text, max_covered_id,
                        len(cut_messages_all),
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
            RATIO = {"designer": 1.3, "coder": 4.0}   # символов на токен

            import json
            def _tok(s): return len(s) // RATIO.get(agent_model, 2)   # для русского ~2 симв/токен, а не 4

            for m in messages:
                c = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
                logger.info(f"[budget] {m.__class__.__name__:15} chars={len(c):6} ~tok={_tok(c):6} | {c[:60]!r}")

            schemas = json.dumps([t.args_schema.schema() if t.args_schema else {} for t in tools], ensure_ascii=False)
            descr   = sum(len(t.description or "") for t in tools)
            logger.info(f"[budget] tools={len(tools)} schema_chars={len(schemas)} descr_chars={descr} ~tok={_tok(schemas)+_tok('x'*descr)}")

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

                    # ── Живой стриминг текста модели ──────────────────────────
                    # Заранее неизвестно, окажется ли текущий шаг финалом (без
                    # tool_calls) или reasoning'ом перед вызовом тулзы.
                    stream_state = {"sent": False}

                    async def _on_chunk(text: str, kind: str = "answer"):
                        # kind из коллектора: "reasoning" | "answer".
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
                        # response.content = ""
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
                        if stream_state["sent"]:
                            await self._send_chunk(
                                "", request_id, msg_id=str(request_id), kind="final_commit"
                            )
                        break

                    # Выполняем все запрошенные инструменты параллельно
                    tool_calls = response.tool_calls
                    tool_names = [tc['name'] for tc in tool_calls]

                    # Псевдо-reasoning для "немых" моделей (coder/gemini): показываем
                    # аргументы тулов в reasoning-блоке, чтобы UI не висел в тишине.
                    if (STREAM_TOOL_ARGS_ENABLED
                            and agent_model in STREAM_ARGS_MODELS
                            and not stream_state["sent"]):
                        try:
                            _preview = _fmt_tool_calls_preview(tool_calls)
                            if _preview:
                                await self._send_chunk(
                                    _preview, request_id,
                                    msg_id=str(request_id), kind="live",
                                )
                                await self._send_chunk(
                                    "", request_id,
                                    msg_id=str(request_id), kind="reasoning_end",
                                )
                        except Exception as _e:
                            logger.debug(f"[stream-args] не удалось отправить аргументы тулов: {_e}")

                    tool_results = await asyncio.gather(
                        *[
                            self._invoke_tool(tools_by_name, tc, request_id)
                            for tc in tool_calls
                        ],
                        return_exceptions=True,
                    )

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

            final_response, tool_trace = await agent_event_loop(messages=messages)

            tokens_usage = {
                    "input_tokens": total_input_tokens,
                    "output_tokens": total_output_tokens,
                    "total_tokens": total_input_tokens + total_output_tokens,
                }

            # Авто-отправка изображений, сгенерированных тулом generate_image.
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

            # Сжатие tool_trace ЗДЕСЬ И СЕЙЧАС (на выходе из запроса).
            # Сырой trace текущего хода: если длиннее порога — гоним через
            # основную модель в компактную md-таблицу (артефакты сохраняются),
            # иначе кладём как есть. В БД больше НЕ ложатся сырые простыни.
            trace_out = None
            if tool_trace:
                raw_trace = '; '.join(tool_trace)
                if len(raw_trace) > TRACE_SUMMARY_THRESHOLD:
                    trace_out = await self._summarize_tool_trace(raw_trace)
                else:
                    trace_out = raw_trace

            return {
                "input": user_input,
                "message": final_response,
                "used_tools": used_tools,
                "action": used_tools[0] if len(used_tools)> 0 else '',
                "success": True,
                "files_to_send": files_to_send,
                "tool_trace": trace_out,
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
        logging.info(f"[tool call] {name}")
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

 