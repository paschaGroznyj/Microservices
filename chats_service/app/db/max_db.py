"""
chats_service/app/db/max_db.py
──────────────────────────────
Слой БД для интеграции мессенджера MAX (аналог telegram_db, ядро).

Таблицы (отдельные от telegram_* — полная изоляция от живого TG):
  max_chats          — allow-list MAX-чатов
  max_chat_settings  — per-chat настройки (модель, тулы, кастомный промпт)

chat_id MAX храним как TEXT (совпадает с chats.chat_id).
"""
import asyncio
import json
import logging

import asyncpg

from app.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME

log = logging.getLogger("max_db")

_SETTINGS_FIELDS = {
    "system_prompt", "agent_name", "agent_model", "selected_tools",
    "trigger_mode", "orchestrator_context_n", "proactive_enabled",
}


class MaxDB:
    def __init__(self, pool: asyncpg.Pool | None = None):
        self.pool = pool

    async def init_db_pool(self):
        if self.pool is not None:
            await self._ensure_schema()
            return
        for attempt in range(1, 6):
            try:
                self.pool = await asyncpg.create_pool(
                    user=DB_USER, password=DB_PASSWORD, host=DB_HOST,
                    port=DB_PORT, database=DB_NAME, min_size=2, max_size=10,
                    command_timeout=60, timeout=60,
                )
                await self._ensure_schema()
                return
            except Exception:
                if attempt < 5:
                    await asyncio.sleep(2.0 * attempt)
                else:
                    raise

    async def _ensure_schema(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS max_chats (
                    chat_id          TEXT PRIMARY KEY,
                    chat_type        TEXT NOT NULL DEFAULT 'group',
                    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                    added_by_user_id BIGINT,
                    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS max_chat_settings (
                    chat_id                TEXT PRIMARY KEY
                        REFERENCES max_chats(chat_id) ON DELETE CASCADE,
                    system_prompt          TEXT,
                    agent_name             TEXT,
                    agent_model            TEXT,
                    selected_tools         JSONB,
                    trigger_mode           TEXT DEFAULT 'mention',
                    orchestrator_context_n INTEGER DEFAULT 8,
                    proactive_enabled      BOOLEAN DEFAULT FALSE,
                    updated_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """)

    async def upsert_chat(self, chat_id: str, added_by_user_id: int | None) -> dict:
        chat_id = str(chat_id).strip()
        try:
            chat_type = "group" if int(chat_id) < 0 else "private"
        except ValueError:
            chat_type = "group"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO max_chats (chat_id, chat_type, is_active, added_by_user_id)
                    VALUES ($1, $2, TRUE, $3)
                    ON CONFLICT (chat_id) DO UPDATE
                        SET is_active = TRUE,
                            chat_type = EXCLUDED.chat_type,
                            updated_at = CURRENT_TIMESTAMP
                    RETURNING chat_id, chat_type, is_active, added_by_user_id;
                """, chat_id, chat_type, added_by_user_id)
                await conn.execute("""
                    INSERT INTO max_chat_settings (chat_id)
                    VALUES ($1) ON CONFLICT (chat_id) DO NOTHING;
                """, chat_id)
        return dict(row)

    async def deactivate_chat(self, chat_id: str) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE max_chats SET is_active=FALSE, updated_at=CURRENT_TIMESTAMP WHERE chat_id=$1",
                str(chat_id),
            )
        return res.endswith("1")

    async def list_active_chat_ids(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT chat_id FROM max_chats WHERE is_active=TRUE")
        return [r["chat_id"] for r in rows]

    async def list_chats_with_settings(self, user_id: int | None = None) -> list[dict]:
        async with self.pool.acquire() as conn:
            base = """
                SELECT c.chat_id, c.chat_type, c.is_active, c.added_by_user_id,
                       c.created_at, c.updated_at,
                       s.system_prompt, s.agent_name, s.agent_model,
                       s.selected_tools, s.trigger_mode,
                       s.orchestrator_context_n, s.proactive_enabled
                FROM max_chats c
                LEFT JOIN max_chat_settings s ON s.chat_id = c.chat_id
                WHERE c.is_active = TRUE
            """
            if user_id is not None:
                rows = await conn.fetch(base + " AND c.added_by_user_id=$1 ORDER BY c.created_at DESC", user_id)
            else:
                rows = await conn.fetch(base + " ORDER BY c.created_at DESC")
        out = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("selected_tools"), str):
                try:
                    d["selected_tools"] = json.loads(d["selected_tools"])
                except Exception:
                    d["selected_tools"] = None
            d["settings"] = {
                "system_prompt": d.pop("system_prompt", None),
                "agent_name": d.pop("agent_name", None),
                "agent_model": d.pop("agent_model", None),
                "selected_tools": d.pop("selected_tools", None),
                "trigger_mode": d.pop("trigger_mode", None),
                "orchestrator_context_n": d.pop("orchestrator_context_n", None),
                "proactive_enabled": d.pop("proactive_enabled", None),
            }
            out.append(d)
        return out

    async def is_allowed(self, chat_id: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM max_chats WHERE chat_id=$1 AND is_active=TRUE", str(chat_id)
            )
        return row is not None

    async def get_settings(self, chat_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM max_chat_settings WHERE chat_id=$1", str(chat_id)
            )
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("selected_tools"), str):
            try:
                d["selected_tools"] = json.loads(d["selected_tools"])
            except Exception:
                d["selected_tools"] = None
        return d

    async def update_settings(self, chat_id: str, **fields) -> dict | None:
        sets, args, i = [], [], 1
        for k, v in fields.items():
            if k not in _SETTINGS_FIELDS:
                continue
            if k == "selected_tools" and v is not None:
                v = json.dumps(v)
            sets.append(f"{k}=${i}")
            args.append(v)
            i += 1
        if not sets:
            return await self.get_settings(chat_id)
        sets.append("updated_at=CURRENT_TIMESTAMP")
        args.append(str(chat_id))
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE max_chat_settings SET {', '.join(sets)} WHERE chat_id=${i}", *args
            )
        return await self.get_settings(chat_id)
