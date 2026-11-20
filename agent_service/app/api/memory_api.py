"""
Memory API: статистика журнала, саммари и контекста для UI.

Эндпоинты:
- GET /web/memory/{chat_id}/stats — общие показники
- GET /web/memory/{chat_id}/journal — список записей журнала с пагинацией
- GET /web/memory/{chat_id}/summary — текст rolling summary

Авторизация: user_id передаётся гейтвеем в query-параметре.
Владение чатом проверяется через chats, chats_forks или chat_sidebar_meta.
"""
from app.agent.common.context_limits import TOKEN_SOFT_LIMIT, context_payload
import json
import logging
from typing import Any
from fastapi import APIRouter, HTTPException, Query

memory_router = APIRouter()

_db = None  # AgentDB instance, устанавливается через set_db

def set_db(db):
    global _db
    _db = db


async def _check_ownership(chat_id: str, user_id: int) -> bool:
    """Проверка владения чатом через chats/chats_forks/chat_sidebar_meta."""
    if not _db or not _db.pool:
        return False
    
    async with _db.pool.acquire() as conn:
        # Через chats (владелец или участник)
        owner = await conn.fetchval(
            "SELECT 1 FROM chats WHERE chat_id = $1 AND user_id = $2 AND user_id > 0 LIMIT 1",
            chat_id, user_id
        )
        if owner:
            return True
        
        # Через chats_forks (создатель ветки)
        fork_owner = await conn.fetchval(
            "SELECT 1 FROM chats_forks WHERE child_chat_id = $1 AND user_id = $2 LIMIT 1",
            chat_id, user_id
        )
        if fork_owner:
            return True
        
        # Через chat_sidebar_meta
        sidebar = await conn.fetchval(
            "SELECT 1 FROM chat_sidebar_meta WHERE chat_id = $1 AND user_id = $2 LIMIT 1",
            chat_id, user_id
        )
        return bool(sidebar)


@memory_router.get("/web/memory/{chat_id}/stats")
async def get_memory_stats(
    chat_id: str,
    user_id: int = Query(..., description="User ID from gateway")
) -> dict[str, Any]:
    """Статистика журнала, саммари и контекста для UI-панели."""
    if not _db or not _db.pool:
        raise HTTPException(status_code=503, detail="DB not available")
    
    # Проверка владения
    has_access = await _check_ownership(chat_id, user_id)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    
    async with _db.pool.acquire() as conn:
        # Журнал: активные записи
        journal_stats = await conn.fetchrow("""
            SELECT 
                COUNT(*) FILTER (WHERE NOT is_archived) as active_count,
                COALESCE(SUM(LENGTH(content)) FILTER (WHERE NOT is_archived), 0) as total_chars,
                COUNT(*) FILTER (WHERE is_archived) as archived_count
            FROM agent_journal
            WHERE chat_id = $1
        """, chat_id)
        
        # Типы записей (только активные)
        journal_types = await conn.fetch("""
            SELECT entry_type, COUNT(*) as count
            FROM agent_journal
            WHERE chat_id = $1 AND NOT is_archived
            GROUP BY entry_type
            ORDER BY count DESC
        """, chat_id)
        
        # Rolling summary
        summary_row = await conn.fetchrow("""
            SELECT summary_text, covered_up_to_id, updated_at
            FROM chat_rolling_summary
            WHERE chat_id = $1
        """, chat_id)
        
        # Покрытие: сколько сообщений в цепочке до covered_up_to_id
        covered_count = 0
        total_messages = 0
        upto_id = 0
        
        if summary_row and summary_row["covered_up_to_id"]:
            upto_id = summary_row["covered_up_to_id"]
            # Считаем покрытие через chat_chain
            covered_count = await conn.fetchval("""
                SELECT COUNT(*)
                FROM (
                    SELECT c.chat_id, c.upto
                    FROM chat_chain($1) c
                ) chain
                JOIN chats m ON m.chat_id = chain.chat_id
                WHERE (chain.upto IS NULL OR m.id <= chain.upto)
                  AND m.id <= $2
            """, chat_id, upto_id) or 0
        
        # Общее количество сообщений в цепочке
        total_messages = await conn.fetchval("""
            SELECT COUNT(*)
            FROM (
                SELECT c.chat_id, c.upto
                FROM chat_chain($1) c
            ) chain
            JOIN chats m ON m.chat_id = chain.chat_id
            WHERE chain.upto IS NULL OR m.id <= chain.upto
        """, chat_id) or 0
        
        # Последний tokens_usage (для контекста)
        last_usage_row = await conn.fetchrow("""
            SELECT message_id, tokens_usage, agent_steps,
                   COALESCE(jsonb_array_length(tool_calls), 0) AS n_tool_calls
            FROM chats
            WHERE chat_id = $1 AND is_from_bot = TRUE AND tokens_usage IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
        """, chat_id)
        
        # Парсинг tokens_usage (asyncpg возвращает строку JSON); формат context
        # общий с финальным payload ответа (context_payload).
        ctx_info = None
        if last_usage_row and last_usage_row["tokens_usage"]:
            try:
                usage = last_usage_row["tokens_usage"]
                if isinstance(usage, str):
                    usage = json.loads(usage)
                ctx_info = context_payload(usage)
            except (json.JSONDecodeError, TypeError, AttributeError, ValueError):
                logging.warning(f"[memory_api] Failed to parse tokens_usage for chat {chat_id}")
        ctx_info = ctx_info or {
            "tokens": None, "max_tokens": TOKEN_SOFT_LIMIT, "limit_kind": "compaction_threshold",
            "model_window_tokens": None, "cache_pct": None,
            "context_breakdown": None, "context_cache_read_tokens": None,
        }

        # Формируем ответ
        summary_info = {
            "text": summary_row["summary_text"] if summary_row else "",
            "covered": covered_count,
            "upto_id": upto_id,
            "total_messages": total_messages,
            "chars": len(summary_row["summary_text"]) if summary_row and summary_row["summary_text"] else 0,
            "updated_at": summary_row["updated_at"].isoformat() if summary_row and summary_row["updated_at"] else None,
        }
        
        return {
            "chat_id": chat_id,
            "journal": {
                "active": journal_stats["active_count"] or 0,
                "archived": journal_stats["archived_count"] or 0,
                "total_chars": journal_stats["total_chars"] or 0,
                "types": [{"type": r["entry_type"], "count": r["count"]} for r in journal_types],
            },
            "summary": summary_info,
            "context": {
                "message_id": str(last_usage_row["message_id"]) if last_usage_row and last_usage_row["message_id"] is not None else None,
                **ctx_info,
                # шаги агента и вызовы тулов последнего ответа (кнопка «Шаги» в строке памяти)
                "agent": (
                    {"steps": last_usage_row["agent_steps"], "tool_calls": last_usage_row["n_tool_calls"]}
                    if last_usage_row and last_usage_row["agent_steps"] is not None else None
                ),
            },
        }


@memory_router.get("/web/memory/{chat_id}/journal")
async def get_journal_entries(
    chat_id: str,
    user_id: int = Query(..., description="User ID from gateway"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Список записей журнала с пагинацией (только активные)."""
    if not _db or not _db.pool:
        raise HTTPException(status_code=503, detail="DB not available")
    
    has_access = await _check_ownership(chat_id, user_id)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    
    async with _db.pool.acquire() as conn:
        entries = await conn.fetch("""
            SELECT id, entry_type, content, created_at, updated_at,
                   LENGTH(content) as chars
            FROM agent_journal
            WHERE chat_id = $1 AND NOT is_archived
            ORDER BY updated_at DESC
            LIMIT $2 OFFSET $3
        """, chat_id, limit, offset)
        
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM agent_journal WHERE chat_id = $1 AND NOT is_archived",
            chat_id
        ) or 0
        
        return {
            "entries": [
                {
                    "id": e["id"],
                    "type": e["entry_type"],
                    "content": e["content"],
                    "created_at": e["created_at"].isoformat(),
                    "updated_at": e["updated_at"].isoformat(),
                    "chars": e["chars"],
                }
                for e in entries
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@memory_router.get("/web/memory/{chat_id}/summary")
async def get_summary_text(
    chat_id: str,
    user_id: int = Query(..., description="User ID from gateway"),
) -> dict[str, Any]:
    """Текст rolling summary."""
    if not _db or not _db.pool:
        raise HTTPException(status_code=503, detail="DB not available")
    
    has_access = await _check_ownership(chat_id, user_id)
    if not has_access:
        raise HTTPException(status_code=403, detail="Access denied")
    
    async with _db.pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT summary_text, covered_up_to_id, updated_at
            FROM chat_rolling_summary
            WHERE chat_id = $1
        """, chat_id)
        
        if not row:
            return {
                "text": "",
                "covered": 0,
                "upto_id": 0,
                "updated_at": None,
            }
        
        # Покрытие через chat_chain
        covered = 0
        upto_id = row["covered_up_to_id"] or 0
        if upto_id:
            covered = await conn.fetchval("""
                SELECT COUNT(*)
                FROM (
                    SELECT c.chat_id, c.upto
                    FROM chat_chain($1) c
                ) chain
                JOIN chats m ON m.chat_id = chain.chat_id
                WHERE (chain.upto IS NULL OR m.id <= chain.upto)
                  AND m.id <= $2
            """, chat_id, upto_id) or 0
        
        return {
            "text": row["summary_text"] or "",
            "covered": covered,
            "upto_id": upto_id,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
