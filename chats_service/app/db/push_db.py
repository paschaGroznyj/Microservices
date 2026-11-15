"""push_subscriptions: Web Push-подписки браузеров (одна строка = один браузер/устройство).

DDL — postgres/sql/02_chats/01_chats_core.sql, для уже созданных баз дублируется в
DDL_STATEMENTS (выполняет DataBaseChats.__init_db, идемпотентно).
"""

DDL_STATEMENTS = (
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS notify_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    """
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id          BIGSERIAL PRIMARY KEY,
        user_id     BIGINT NOT NULL,
        gw          TEXT NOT NULL DEFAULT 'main',
        endpoint    TEXT NOT NULL UNIQUE,
        p256dh      TEXT NOT NULL,
        auth        TEXT NOT NULL,
        user_agent  TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_push_subs_user ON push_subscriptions(user_id, gw)",
)

MAX_ENDPOINT_LEN = 2048
MAX_KEY_LEN = 256


class PushDB:
    def __init__(self, pool):
        self.pool = pool

    async def upsert_subscription(self, user_id: int, gw: str, endpoint: str, p256dh: str, auth: str,
                                  user_agent: str | None = None) -> None:
        """Один endpoint принадлежит одному браузеру: при повторной подписке (другой
        пользователь в том же браузере) строка переходит к новому user_id."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO push_subscriptions (user_id, gw, endpoint, p256dh, auth, user_agent)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (endpoint) DO UPDATE SET
                    user_id = EXCLUDED.user_id, gw = EXCLUDED.gw,
                    p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth,
                    user_agent = EXCLUDED.user_agent, updated_at = CURRENT_TIMESTAMP
                """,
                int(user_id), gw, endpoint, p256dh, auth, (user_agent or "")[:300] or None,
            )

    async def delete_subscription(self, endpoint: str, user_id: int | None = None) -> int:
        async with self.pool.acquire() as conn:
            if user_id is None:
                res = await conn.execute("DELETE FROM push_subscriptions WHERE endpoint = $1", endpoint)
            else:
                res = await conn.execute(
                    "DELETE FROM push_subscriptions WHERE endpoint = $1 AND user_id = $2", endpoint, int(user_id))
        try:
            return int(str(res).split()[-1])
        except Exception:
            return 0

    async def list_subscriptions(self, user_id: int, gw: str) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = $1 AND gw = $2",
                int(user_id), gw,
            )
        return [dict(r) for r in rows]

    async def notify_enabled(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            v = await conn.fetchval("SELECT notify_enabled FROM user_settings WHERE user_id = $1", int(user_id))
        return bool(v)

    async def chat_owner(self, chat_id: str) -> int | None:
        """Ответ агента приходит с user_id=-1, владельца берём по сообщениям пользователя."""
        async with self.pool.acquire() as conn:
            v = await conn.fetchval(
                """
                SELECT user_id FROM chats
                WHERE chat_id = $1 AND user_id > 0 AND is_from_bot IS NOT TRUE
                ORDER BY id DESC LIMIT 1
                """,
                str(chat_id),
            )
        return int(v) if v else None

    async def chat_title(self, chat_id: str, user_id: int) -> str | None:
        async with self.pool.acquire() as conn:
            v = await conn.fetchval(
                """
                SELECT COALESCE(NULLIF(renamed_title, ''), NULLIF(auto_title, ''))
                FROM chat_sidebar_meta WHERE chat_id = $1
                ORDER BY (user_id = $2) DESC, (renamed_title IS NOT NULL) DESC LIMIT 1
                """,
                str(chat_id), int(user_id),
            )
        return v or None
