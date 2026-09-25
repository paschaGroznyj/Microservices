"""
chats_service/app/db/telegram_db.py
───────────────────────────────────
Слой БД для телеграм-интеграции агента.

Таблицы:
  telegram_chats          — allow-list телеграм-чатов (встраиваются через CatchPM UI)
  telegram_chat_settings  — per-chat настройки: модель, тулы, триггер, кастомный промпт

chat_id телеграмы храним как TEXT (совпадает с chats.chat_id) — отрицательные
числа для групп, положительные для лички.
"""
import asyncio
import json
import logging

import asyncpg

from app.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME

log = logging.getLogger("telegram_db")


class TelegramDB:
    def __init__(self, pool: asyncpg.Pool | None = None):
        self.pool = pool

    async def init_db_pool(self):
        """Собственный пул (если модуль используется автономно)."""
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
                CREATE TABLE IF NOT EXISTS telegram_chats (
                    chat_id        TEXT PRIMARY KEY,
                    chat_type      TEXT NOT NULL DEFAULT 'group',  -- 'group' | 'private'
                    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
                    added_by_user_id BIGINT,
                    created_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_telegram_chats_active
                    ON telegram_chats(is_active);

                CREATE TABLE IF NOT EXISTS telegram_chat_settings (
                    chat_id        TEXT PRIMARY KEY
                                   REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
                    system_prompt  TEXT,                 -- кастомный промпт поверх дефолтного HTML
                    agent_name     TEXT,                 -- имя агента в чате (NULL = дефолтный ассистент)
                    agent_model    TEXT,                 -- NULL = дефолт
                    selected_tools JSONB,                -- NULL = все тулы по дефолту
                    trigger_mode   TEXT NOT NULL DEFAULT 'orchestrator', -- 'mention' | 'orchestrator' | 'every'
                    orchestrator_context_n INT NOT NULL DEFAULT 6,
                    proactive_enabled BOOLEAN NOT NULL DEFAULT TRUE, -- будильники (завтрак/обед/ужин)
                    updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS telegram_chat_members (
                    chat_id     TEXT NOT NULL
                                REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
                    user_id     BIGINT NOT NULL,
                    username    TEXT,
                    first_name  TEXT,
                    is_bot      BOOLEAN NOT NULL DEFAULT FALSE,
                    last_seen   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    created_at  TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS ix_tg_members_chat
                    ON telegram_chat_members(chat_id);
            """)

            # мягкая миграция для уже существующих БД
            await conn.execute(
                "ALTER TABLE telegram_chat_settings "
                "ADD COLUMN IF NOT EXISTS proactive_enabled BOOLEAN NOT NULL DEFAULT TRUE;"
            )
            await conn.execute(
                "ALTER TABLE telegram_chat_settings "
                "ADD COLUMN IF NOT EXISTS agent_name TEXT;"
            )

    # ── allow-list ───────────────────────────────────────────────────────────
    async def upsert_chat(self, chat_id: str, added_by_user_id: int | None) -> dict:
        """Встраивает чат. Тип определяем по знаку: chat_id < 0 -> группа."""
        chat_id = str(chat_id).strip()
        try:
            chat_type = "group" if int(chat_id) < 0 else "private"
        except ValueError:
            chat_type = "group"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("""
                    INSERT INTO telegram_chats (chat_id, chat_type, is_active, added_by_user_id)
                    VALUES ($1, $2, TRUE, $3)
                    ON CONFLICT (chat_id) DO UPDATE
                        SET is_active = TRUE,
                            chat_type = EXCLUDED.chat_type,
                            updated_at = CURRENT_TIMESTAMP
                    RETURNING chat_id, chat_type, is_active, added_by_user_id;
                """, chat_id, chat_type, added_by_user_id)
                # дефолтная строка настроек (если нет)
                await conn.execute("""
                    INSERT INTO telegram_chat_settings (chat_id)
                    VALUES ($1) ON CONFLICT (chat_id) DO NOTHING;
                """, chat_id)
        return dict(row)

    async def deactivate_chat(self, chat_id: str) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE telegram_chats SET is_active=FALSE, updated_at=CURRENT_TIMESTAMP WHERE chat_id=$1",
                str(chat_id),
            )
        return res.endswith("1")

    async def list_active_chat_ids(self) -> list[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id FROM telegram_chats WHERE is_active=TRUE"
            )
        return [r["chat_id"] for r in rows]

    async def list_chats_with_settings(self) -> list[dict]:
        """Активные встроенные чаты вместе с их настройками — для CatchPM UI."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.chat_id, c.chat_type, c.is_active, c.added_by_user_id,
                       c.created_at, c.updated_at,
                       s.system_prompt, s.agent_name, s.agent_model,
                       s.selected_tools, s.trigger_mode,
                       s.orchestrator_context_n, s.proactive_enabled
                FROM telegram_chats c
                LEFT JOIN telegram_chat_settings s ON s.chat_id = c.chat_id
                WHERE c.is_active = TRUE
                ORDER BY c.created_at DESC
            """)
        out = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("selected_tools"), str):
                try:
                    d["selected_tools"] = json.loads(d["selected_tools"])
                except Exception:
                    d["selected_tools"] = None
            # Собираем настройки во вложенный объект settings (так их ждёт CatchPM UI),
            # оставляя chat_id/chat_type/is_active в корне.
            settings = {
                "system_prompt": d.pop("system_prompt", None),
                "agent_name": d.pop("agent_name", None),
                "agent_model": d.pop("agent_model", None),
                "selected_tools": d.pop("selected_tools", None),
                "trigger_mode": d.pop("trigger_mode", None),
                "orchestrator_context_n": d.pop("orchestrator_context_n", None),
                "proactive_enabled": d.pop("proactive_enabled", None),
            }
            d["settings"] = settings
            out.append(d)
        return out

    async def is_allowed(self, chat_id: str) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM telegram_chats WHERE chat_id=$1 AND is_active=TRUE",
                str(chat_id),
            )
        return row is not None

    # ── settings ─────────────────────────────────────────────────────────────
    async def get_settings(self, chat_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM telegram_chat_settings WHERE chat_id=$1", str(chat_id)
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
        """Точечное обновление настроек. admin-тулы запрещены на уровне колл-сайта."""
        allowed = {"system_prompt", "agent_name", "agent_model", "selected_tools",
                   "trigger_mode", "orchestrator_context_n", "proactive_enabled"}
        sets, args, i = [], [], 1
        for k, v in fields.items():
            if k not in allowed:
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
                f"UPDATE telegram_chat_settings SET {', '.join(sets)} WHERE chat_id=${i}",
                *args,
            )
        return await self.get_settings(chat_id)

    # ── members ───────────────────────────────────────────────────────────────────────────────
    async def upsert_member(self, chat_id: str, user_id: int, username: str | None,
                            first_name: str | None, is_bot: bool = False) -> None:
        """Пассивный сбор: каждый написавший в чат попадает сюда."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO telegram_chat_members
                    (chat_id, user_id, username, first_name, is_bot, last_seen)
                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id, user_id) DO UPDATE
                    SET username   = EXCLUDED.username,
                        first_name = EXCLUDED.first_name,
                        is_bot     = EXCLUDED.is_bot,
                        last_seen  = CURRENT_TIMESTAMP;
            """, str(chat_id), int(user_id), username, first_name, is_bot)

    async def get_random_member(self, chat_id: str, exclude_bots: bool = True) -> dict | None:
        """Рандомный участник чата (для проактивной инициации)."""
        q = ("SELECT user_id, username, first_name FROM telegram_chat_members "
             "WHERE chat_id=$1")
        if exclude_bots:
            q += " AND is_bot=FALSE"
        q += " ORDER BY random() LIMIT 1"
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(q, str(chat_id))
        return dict(row) if row else None

    async def list_proactive_chats(self) -> list[dict]:
        """Активные чаты с включёнными будильниками."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.chat_id, c.chat_type
                FROM telegram_chats c
                JOIN telegram_chat_settings s ON s.chat_id = c.chat_id
                WHERE c.is_active = TRUE AND s.proactive_enabled = TRUE
            """)
        return [dict(r) for r in rows]
