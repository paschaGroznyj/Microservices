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
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

import json
import logging

import asyncpg

from app.core.config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME

log = logging.getLogger("max_db")

_SETTINGS_FIELDS = {
    "system_prompt", "agent_name", "agent_model", "selected_tools",
    "trigger_mode", "orchestrator_context_n", "proactive_enabled", "timezone_name",
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
                    proactive_enabled      BOOLEAN NOT NULL DEFAULT TRUE,
                    timezone_name          TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    updated_at             TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS max_triggers (
                    id               SERIAL PRIMARY KEY,
                    chat_id          TEXT NOT NULL REFERENCES max_chats(chat_id) ON DELETE CASCADE,
                    added_by_user_id BIGINT,
                    kind             TEXT NOT NULL DEFAULT 'recurring',
                    title            TEXT,
                    time_msk         TEXT NOT NULL,
                    days_of_week     INT[],
                    trigger_date     DATE,
                    prompt           TEXT,
                    preset_key       TEXT,
                    addressed        BOOLEAN NOT NULL DEFAULT FALSE,
                    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
                    last_fired_at    TIMESTAMPTZ,
                    created_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at       TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_max_triggers_scan ON max_triggers(is_active, time_msk);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_max_triggers_chat_preset
                    ON max_triggers(chat_id, preset_key) WHERE preset_key IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS ux_max_triggers_custom_natkey
                    ON max_triggers(chat_id, kind, time_msk, COALESCE(title,''), COALESCE(trigger_date,'0001-01-01'::date))
                    WHERE preset_key IS NULL;
                CREATE TABLE IF NOT EXISTS max_chat_members (
                    chat_id     TEXT NOT NULL,
                    user_id     BIGINT NOT NULL,
                    username    TEXT,
                    first_name  TEXT,
                    is_bot      BOOLEAN NOT NULL DEFAULT FALSE,
                    last_seen   TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (chat_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_max_members_chat
                    ON max_chat_members(chat_id);
            """)
            await conn.execute("ALTER TABLE max_chat_settings ADD COLUMN IF NOT EXISTS timezone_name TEXT NOT NULL DEFAULT 'Europe/Moscow';")

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
        await self.ensure_default_alarm_triggers(chat_id, added_by_user_id)
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
                       s.orchestrator_context_n, s.proactive_enabled, s.timezone_name
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
                "timezone_name": d.pop("timezone_name", None) or "Europe/Moscow",
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

    async def upsert_member(self, chat_id: str, user_id: int,
                            username: str | None, first_name: str | None,
                            is_bot: bool = False) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO max_chat_members
                    (chat_id, user_id, username, first_name, is_bot, last_seen)
                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                ON CONFLICT (chat_id, user_id) DO UPDATE
                    SET username   = COALESCE(EXCLUDED.username, max_chat_members.username),
                        first_name = COALESCE(EXCLUDED.first_name, max_chat_members.first_name),
                        last_seen  = CURRENT_TIMESTAMP;
            """, str(chat_id), int(user_id), username, first_name, bool(is_bot))

    async def get_random_member(self, chat_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id, username, first_name FROM max_chat_members "
                "WHERE chat_id=$1 AND is_bot=FALSE ORDER BY random() LIMIT 1",
                str(chat_id))
            return dict(row) if row else None

    async def list_proactive_chats(self) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT c.chat_id, c.chat_type
                FROM max_chats c
                JOIN max_chat_settings s ON s.chat_id = c.chat_id
                WHERE c.is_active = TRUE AND s.proactive_enabled = TRUE
            """)
        return [dict(r) for r in rows]

    async def list_triggers(self, chat_id: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM max_triggers
                WHERE chat_id = $1
                ORDER BY kind, time_msk
            """, chat_id)
        return [dict(r) for r in rows]

    async def create_trigger(self, chat_id: str, added_by_user_id: int | None,
                             kind: str, time_msk: str, title: str | None = None,
                             days_of_week: list | None = None, trigger_date=None,
                             prompt: str | None = None, preset_key: str | None = None,
                             addressed: bool = False) -> dict:
        if preset_key is not None:
            conflict = "(chat_id, preset_key) WHERE preset_key IS NOT NULL"
        else:
            conflict = ("(chat_id, kind, time_msk, COALESCE(title,''), "
                        "COALESCE(trigger_date,'0001-01-01'::date)) "
                        "WHERE preset_key IS NULL")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO max_triggers
                    (chat_id, added_by_user_id, kind, title, time_msk,
                     days_of_week, trigger_date, prompt, preset_key, addressed)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT {conflict} DO UPDATE SET
                    added_by_user_id = EXCLUDED.added_by_user_id,
                    days_of_week     = EXCLUDED.days_of_week,
                    trigger_date     = EXCLUDED.trigger_date,
                    prompt           = EXCLUDED.prompt,
                    addressed        = EXCLUDED.addressed,
                    updated_at       = CURRENT_TIMESTAMP
                RETURNING id, chat_id, added_by_user_id, kind, title,
                          time_msk, days_of_week, trigger_date, prompt, preset_key,
                          addressed, is_active, last_fired_at, created_at, updated_at
            """, chat_id, added_by_user_id, kind, title, time_msk,
                 days_of_week, trigger_date, prompt, preset_key, addressed)
        return dict(row)

    async def update_trigger(self, trigger_id: int, **fields) -> dict | None:
        allowed = {"kind", "title", "time_msk", "days_of_week",
                   "trigger_date", "prompt", "preset_key", "addressed", "is_active"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                vals.append(v)
                sets.append(f"{k} = ${len(vals)}")
        if not sets:
            return None
        vals.append(trigger_id)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                UPDATE max_triggers
                SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ${len(vals)}
                RETURNING id, chat_id, added_by_user_id, kind, title,
                          time_msk, days_of_week, trigger_date, prompt, preset_key,
                          addressed, is_active, last_fired_at, created_at, updated_at
            """, *vals)
        return dict(row) if row else None

    async def delete_trigger(self, trigger_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM max_triggers WHERE id = $1", trigger_id)
        return res.endswith("1")

    async def scan_due_triggers(self, now_utc: datetime) -> list[dict]:
        GRACE_SEC = 120
        MSK = ZoneInfo("Europe/Moscow")
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.id, t.chat_id, t.kind, t.title, t.time_msk,
                       t.days_of_week, t.trigger_date, t.prompt, t.preset_key,
                       t.addressed, c.chat_type, s.timezone_name, t.last_fired_at
                FROM max_triggers t
                JOIN max_chats c ON c.chat_id = t.chat_id
                JOIN max_chat_settings s ON s.chat_id = t.chat_id
                WHERE t.is_active = TRUE
                  AND c.is_active = TRUE
                  AND s.proactive_enabled = TRUE
            """)
        due = []
        for row in rows:
            d = dict(row)
            hhmm = d.get("time_msk")
            if not hhmm:
                continue
            try:
                hh, mm = [int(x) for x in str(hhmm).split(":")]
            except Exception:
                continue
            tz_name = (d.get("timezone_name") or "Europe/Moscow").strip()
            try:
                chat_tz = ZoneInfo(tz_name)
            except Exception:
                chat_tz = MSK
            now_chat = now_utc.astimezone(chat_tz)
            scheduled = now_chat.replace(hour=hh, minute=mm, second=0, microsecond=0)
            delta = (now_chat - scheduled).total_seconds()
            if delta < 0 or delta >= GRACE_SEC:
                continue
            if d.get("kind") == "recurring":
                days = d.get("days_of_week") or []
                if days and now_chat.isoweekday() not in days:
                    continue
            elif d.get("kind") == "once":
                td = d.get("trigger_date")
                if td != now_chat.date():
                    continue
            else:
                continue
            lf = d.get("last_fired_at")
            if lf is not None:
                try:
                    lf_chat = lf.astimezone(chat_tz)
                    if lf_chat >= scheduled:
                        continue
                except Exception:
                    pass
            d["fire_stamp"] = scheduled.strftime("%Y%m%d%H%M")
            d.pop("timezone_name", None)
            due.append(d)
        return due

    async def mark_trigger_fired(self, trigger_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE max_triggers
                SET last_fired_at = CURRENT_TIMESTAMP,
                    is_active = CASE WHEN kind = 'once' THEN FALSE ELSE is_active END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = $1
            """, trigger_id)

    async def get_trigger(self, trigger_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM max_triggers
                WHERE id = $1
            """, trigger_id)
        return dict(row) if row else None

    async def ensure_default_alarm_triggers(self, chat_id: str, added_by_user_id: int | None = None) -> None:
        defaults = [
            ("breakfast", "Завтрак", "09:00"),
            ("lunch", "Обед", "13:00"),
            ("dinner", "Ужин", "18:00"),
        ]
        async with self.pool.acquire() as conn:
            for preset_key, title, time_msk in defaults:
                await conn.execute("""
                    INSERT INTO max_triggers
                        (chat_id, added_by_user_id, kind, title, time_msk,
                         days_of_week, trigger_date, prompt, preset_key, addressed, is_active)
                    VALUES ($1,$2,'recurring',$3,$4,NULL,NULL,NULL,$5,FALSE,TRUE)
                    ON CONFLICT (chat_id, preset_key) WHERE preset_key IS NOT NULL DO NOTHING
                """, str(chat_id), added_by_user_id, title, time_msk, preset_key)

    async def get_alarm_trigger(self, chat_id: str, preset_key: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM max_triggers
                WHERE chat_id = $1 AND preset_key = $2
            """, str(chat_id), preset_key)
        return dict(row) if row else None
