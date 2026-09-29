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
from datetime import datetime
from zoneinfo import ZoneInfo

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
                    timezone_name  TEXT NOT NULL DEFAULT 'Europe/Moscow', -- локальная TZ чата
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

                -- Индивидуальные проактивные триггеры per-chat:
                --   kind='recurring' -> регулярный (time_msk + days_of_week)
                --   kind='once'      -> разовый по дате (time_msk + trigger_date)
                CREATE TABLE IF NOT EXISTS telegram_triggers (
                    id             SERIAL PRIMARY KEY,
                    chat_id        TEXT NOT NULL
                                   REFERENCES telegram_chats(chat_id) ON DELETE CASCADE,
                    added_by_user_id BIGINT,
                    kind           TEXT NOT NULL DEFAULT 'recurring', -- 'recurring' | 'once'
                    title          TEXT,                 -- ярлык для UI
                    time_msk       TEXT NOT NULL,        -- 'HH:MM' по МСК
                    days_of_week   INT[],                -- recurring: [1..7] пн..вс; NULL/[] = все дни
                    trigger_date   DATE,                 -- once: конкретная дата
                    prompt         TEXT,                 -- индивидуальный промпт триггера (NULL = дефолт)
                    preset_key     TEXT,                 -- системный будильник: breakfast|lunch|dinner
                    addressed      BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE=адресный рандомному, FALSE=broadcast
                    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
                    last_fired_at  TIMESTAMPTZ,          -- защита от повторного выстрела
                    created_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at     TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_tg_triggers_chat
                    ON telegram_triggers(chat_id);
                CREATE INDEX IF NOT EXISTS ix_tg_triggers_scan
                    ON telegram_triggers(is_active, time_msk);
            """)

            await conn.execute(
                "ALTER TABLE telegram_chat_settings "
                "ADD COLUMN IF NOT EXISTS timezone_name TEXT NOT NULL DEFAULT 'Europe/Moscow';"
            )
            await conn.execute(
                "ALTER TABLE telegram_triggers "
                "ADD COLUMN IF NOT EXISTS preset_key TEXT;"
            )
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_triggers_chat_preset "
                "ON telegram_triggers(chat_id, preset_key) WHERE preset_key IS NOT NULL;"
            )
            # анти-дубль для НЕ-preset триггеров (custom recurring/once):
            # один и тот же чат не может иметь две одинаковые записи по
            # (kind, time_msk, title, trigger_date). preset-будильники сюда
            # не попадают — у них свой индекс выше.
            await conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_tg_triggers_custom_natkey "
                "ON telegram_triggers(chat_id, kind, time_msk, "
                "COALESCE(title,''), COALESCE(trigger_date,'0001-01-01'::date)) "
                "WHERE preset_key IS NULL;"
            )

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
        await self.ensure_default_alarm_triggers(chat_id, added_by_user_id)
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

    async def list_chats_with_settings(self, user_id: int | None = None) -> list[dict]:
        """Активные встроенные чаты вместе с их настройками — для CatchPM UI.
        Если user_id задан — только чаты этого пользователя."""
        async with self.pool.acquire() as conn:
            if user_id is not None:
                rows = await conn.fetch("""
                    SELECT c.chat_id, c.chat_type, c.is_active, c.added_by_user_id,
                           c.created_at, c.updated_at,
                           s.system_prompt, s.agent_name, s.agent_model,
                           s.selected_tools, s.trigger_mode,
                           s.orchestrator_context_n, s.proactive_enabled, s.timezone_name
                    FROM telegram_chats c
                    LEFT JOIN telegram_chat_settings s ON s.chat_id = c.chat_id
                    WHERE c.is_active = TRUE AND c.added_by_user_id = $1
                    ORDER BY c.created_at DESC
                """, user_id)
            else:
                rows = await conn.fetch("""
                    SELECT c.chat_id, c.chat_type, c.is_active, c.added_by_user_id,
                           c.created_at, c.updated_at,
                           s.system_prompt, s.agent_name, s.agent_model,
                           s.selected_tools, s.trigger_mode,
                           s.orchestrator_context_n, s.proactive_enabled, s.timezone_name
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
                "timezone_name": d.pop("timezone_name", None),
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
                   "trigger_mode", "orchestrator_context_n", "proactive_enabled", "timezone_name"}
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


    # ─── Индивидуальные триггеры (telegram_triggers) ───

    async def list_triggers(self, chat_id: str) -> list[dict]:
        """Все триггеры чата (активные и нет) — для UI."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM telegram_triggers
                WHERE chat_id = $1
                ORDER BY kind, time_msk
            """, chat_id)
        return [dict(r) for r in rows]

    async def create_trigger(self, chat_id: str, added_by_user_id: int | None,
                             kind: str, time_msk: str,
                             title: str | None = None,
                             days_of_week: list | None = None,
                             trigger_date=None, prompt: str | None = None,
                             preset_key: str | None = None,
                             addressed: bool = False) -> dict:
        """Создать триггер. days_of_week — список 1..7 или None.

        Идемпотентно: при повторе с тем же ключом (preset_key ЛИБО
        natural-key chat_id+kind+time_msk+title+trigger_date для custom)
        не плодит дубль, а обновляет существующую строку (ON CONFLICT).
        """
        # выбираем целевой конфликт-таргет под соответствующий частичный индекс
        if preset_key is not None:
            conflict = "(chat_id, preset_key) WHERE preset_key IS NOT NULL"
        else:
            conflict = ("(chat_id, kind, time_msk, COALESCE(title,''), "
                        "COALESCE(trigger_date,'0001-01-01'::date)) "
                        "WHERE preset_key IS NULL")
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(f"""
                INSERT INTO telegram_triggers
                    (chat_id, added_by_user_id, kind, title, time_msk,
                     days_of_week, trigger_date, prompt, preset_key, addressed)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                ON CONFLICT {conflict} DO UPDATE SET
                    added_by_user_id = EXCLUDED.added_by_user_id,
                    days_of_week     = EXCLUDED.days_of_week,
                    trigger_date     = EXCLUDED.trigger_date,
                    prompt           = EXCLUDED.prompt,
                    addressed        = EXCLUDED.addressed,
                    is_active        = TRUE,
                    updated_at       = CURRENT_TIMESTAMP
                RETURNING id, chat_id, added_by_user_id, kind, title,
                          time_msk, days_of_week, trigger_date, prompt, preset_key,
                          addressed, is_active, last_fired_at, created_at, updated_at
            """, chat_id, added_by_user_id, kind, title, time_msk,
                 days_of_week, trigger_date, prompt, preset_key, addressed)
        return dict(row)

    async def update_trigger(self, trigger_id: int, **fields) -> dict | None:
        """Точечное обновление триггера."""
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
                UPDATE telegram_triggers
                SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP
                WHERE id = ${len(vals)}
                RETURNING id, chat_id, added_by_user_id, kind, title,
                          time_msk, days_of_week, trigger_date, prompt, preset_key,
                          addressed, is_active, last_fired_at, created_at, updated_at
            """, *vals)
        return dict(row) if row else None

    async def delete_trigger(self, trigger_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM telegram_triggers WHERE id = $1", trigger_id)
        return res.endswith("1")

    async def scan_due_triggers(self, now_utc: datetime) -> list[dict]:
        """Тик-сканер: вернуть триггеры, чьё локальное время настало СЕЙЧАС.
        now_utc — текущий UTC timestamp; локальное время считается через timezone_name чата.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT t.id, t.chat_id, t.kind, t.title, t.time_msk,
                       t.days_of_week, t.trigger_date, t.prompt, t.preset_key,
                       t.addressed, c.chat_type, s.timezone_name, t.last_fired_at
                FROM telegram_triggers t
                JOIN telegram_chats c ON c.chat_id = t.chat_id
                JOIN telegram_chat_settings s ON s.chat_id = t.chat_id
                WHERE t.is_active = TRUE
                  AND c.is_active = TRUE
                  AND s.proactive_enabled = TRUE
            """)
        due = []
        for row in rows:
            d = dict(row)
            tz_name = d.get("timezone_name") or "Europe/Moscow"
            try:
                local_now = now_utc.astimezone(ZoneInfo(tz_name))
            except Exception:
                local_now = now_utc.astimezone(ZoneInfo("Europe/Moscow"))
                d["timezone_name"] = "Europe/Moscow"
            if d.get("time_msk") != local_now.strftime("%H:%M"):
                continue
            if d.get("kind") == "recurring":
                days = d.get("days_of_week") or []
                if days and local_now.isoweekday() not in days:
                    continue
            elif d.get("kind") == "once":
                if d.get("trigger_date") != local_now.date():
                    continue
                lf = d.get("last_fired_at")
                if lf is not None:
                    try:
                        if lf.astimezone(local_now.tzinfo).date() == local_now.date():
                            continue
                    except Exception:
                        if lf.date() == local_now.date():
                            continue
            else:
                continue
            due.append(d)
        return due

    async def mark_trigger_fired(self, trigger_id: int) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE telegram_triggers SET last_fired_at = CURRENT_TIMESTAMP WHERE id = $1",
                trigger_id)

    async def get_trigger(self, trigger_id: int) -> dict | None:
        """Один триггер по id (для debug-прогона)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM telegram_triggers
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
                    INSERT INTO telegram_triggers
                        (chat_id, added_by_user_id, kind, title, time_msk,
                         days_of_week, trigger_date, prompt, preset_key, addressed, is_active)
                    VALUES ($1,$2,'recurring',$3,$4,NULL,NULL,NULL,$5,FALSE,TRUE)
                    ON CONFLICT (chat_id, preset_key) DO NOTHING
                """, str(chat_id), added_by_user_id, title, time_msk, preset_key)

    async def get_alarm_trigger(self, chat_id: str, preset_key: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, chat_id, added_by_user_id, kind, title,
                       time_msk, days_of_week, trigger_date, prompt, preset_key,
                       addressed, is_active, last_fired_at, created_at, updated_at
                FROM telegram_triggers
                WHERE chat_id = $1 AND preset_key = $2
            """, str(chat_id), preset_key)
        return dict(row) if row else None

    async def upsert_alarm_trigger(self, chat_id: str, added_by_user_id: int | None,
                                   preset_key: str, title: str, time_msk: str | None = None,
                                   prompt: str | None = None,
                                   is_active: bool | None = None) -> dict:
        base = await self.get_alarm_trigger(chat_id, preset_key)
        if base is None:
            await self.create_trigger(
                chat_id=chat_id,
                added_by_user_id=added_by_user_id,
                kind='recurring',
                time_msk=time_msk or '09:00',
                title=title,
                days_of_week=None,
                trigger_date=None,
                prompt=prompt,
                preset_key=preset_key,
                addressed=False,
            )
            base = await self.get_alarm_trigger(chat_id, preset_key)
        fields = {'kind': 'recurring', 'title': title, 'preset_key': preset_key}
        if time_msk is not None:
            fields['time_msk'] = time_msk
        if prompt is not None:
            fields['prompt'] = prompt
        if is_active is not None:
            fields['is_active'] = is_active
        row = await self.update_trigger(int(base['id']), **fields)
        return row or base

