"""
Слой БД для реестра воркспейсов редактора (editor_workspaces).

Переиспользует общий пул asyncpg (передаётся в конструктор).
Физические файлы живут в песочнице, здесь — реестр «workspace_id → user_id» с учётом TTL.
"""
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

DEFAULT_TTL_HOURS = 10


def _format_ttl_label(remaining_seconds: int | None, is_permanent: bool) -> str:
    if is_permanent or remaining_seconds is None:
        return "бессрочно"
    if remaining_seconds <= 0:
        return "истекла"
    h = remaining_seconds // 3600
    m = (remaining_seconds % 3600) // 60
    if h > 0:
        return f"{h}ч {m}м" if m > 0 else f"{h}ч"
    if m > 0:
        return f"{m}м"
    return f"{remaining_seconds}с"


def _serialize_workspace_row(row) -> dict:
    d = dict(row)
    now = datetime.now(timezone.utc)
    is_perm = bool(d.get("is_permanent")) or (
        d.get("ttl_hours") is not None and int(d.get("ttl_hours")) <= 0
    ) or (d.get("ttl_hours") is None and d.get("expires_at") is None and bool(d.get("is_permanent")))

    exp = d.get("expires_at")
    if is_perm:
        rem_sec = None
        ttl_hours = None
        exp_iso = None
    else:
        ttl_hours = int(d.get("ttl_hours") or DEFAULT_TTL_HOURS)
        if isinstance(exp, datetime):
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            rem_sec = max(0, int((exp - now).total_seconds()))
            exp_iso = exp.isoformat()
        else:
            rem_sec = ttl_hours * 3600
            exp_iso = None

    for k in ("created_at", "updated_at"):
        v = d.get(k)
        if isinstance(v, datetime):
            d[k] = v.isoformat()

    d["is_permanent"] = is_perm
    d["immortal"] = is_perm
    d["ttl_hours"] = ttl_hours
    d["expires_at"] = exp_iso
    d["remaining_seconds"] = rem_sec
    d["ttl_text"] = _format_ttl_label(rem_sec, is_perm)
    return d


class EditorDB:
    def __init__(self, pool):
        self.pool = pool
        self._pending_permanent: set[str] = set()

    async def register(
        self,
        user_id: int,
        workspace_id: str,
        chat_id: str = None,
        title: str = None,
        ttl_hours: int | None = DEFAULT_TTL_HOURS,
        is_permanent: bool = False,
    ) -> dict:
        """INSERT/UPSERT воркспейса за юзером с учётом TTL (по дефолту 10ч или бессрочно)."""
        perm = bool(is_permanent) or ttl_hours is None or int(ttl_hours) <= 0
        eff_ttl = None if perm else int(ttl_hours or DEFAULT_TTL_HOURS)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO editor_workspaces (
                    user_id, workspace_id, chat_id, title,
                    ttl_hours, is_permanent, expires_at, is_active, updated_at
                )
                VALUES (
                    $1, $2, $3, $4,
                    $5::INTEGER,
                    $6::BOOLEAN,
                    CASE
                        WHEN $6::BOOLEAN OR $5::INTEGER IS NULL OR $5::INTEGER <= 0 THEN NULL
                        ELSE CURRENT_TIMESTAMP + make_interval(hours => $5::INTEGER)
                    END,
                    TRUE,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (user_id, workspace_id) DO UPDATE SET
                    chat_id      = COALESCE(EXCLUDED.chat_id, editor_workspaces.chat_id),
                    title        = COALESCE(EXCLUDED.title, editor_workspaces.title),
                    is_permanent = (COALESCE(editor_workspaces.is_permanent, FALSE) OR EXCLUDED.is_permanent),
                    ttl_hours    = CASE
                        WHEN COALESCE(editor_workspaces.is_permanent, FALSE) OR EXCLUDED.is_permanent THEN NULL
                        ELSE EXCLUDED.ttl_hours
                    END,
                    expires_at   = CASE
                        WHEN COALESCE(editor_workspaces.is_permanent, FALSE) OR EXCLUDED.is_permanent THEN NULL
                        ELSE CURRENT_TIMESTAMP + make_interval(hours => COALESCE(EXCLUDED.ttl_hours, 10))
                    END,
                    is_active    = TRUE,
                    updated_at   = CURRENT_TIMESTAMP
                RETURNING id, user_id, workspace_id, chat_id, title,
                          ttl_hours, is_permanent, expires_at, is_active, created_at, updated_at
                """,
                int(user_id),
                str(workspace_id),
                chat_id,
                title,
                eff_ttl,
                perm,
            )
            return _serialize_workspace_row(row) if row else None

    async def list_for_user(self, user_id: int, chat_id: str = None) -> list:
        """Список только активных и живых по TTL воркспейсов юзера."""
        async with self.pool.acquire() as conn:
            if self._pending_permanent:
                pending_list = list(self._pending_permanent)
                await conn.execute(
                    """
                    UPDATE editor_workspaces
                    SET is_permanent = TRUE,
                        ttl_hours = NULL,
                        expires_at = NULL,
                        is_active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = ANY($1::text[])
                    """,
                    pending_list,
                )
                rows_done = await conn.fetch(
                    "SELECT workspace_id FROM editor_workspaces WHERE workspace_id = ANY($1::text[]) AND is_permanent = TRUE",
                    pending_list,
                )
                for r_done in rows_done:
                    self._pending_permanent.discard(r_done["workspace_id"])
            await conn.execute(
                """
                UPDATE editor_workspaces
                SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = $1
                  AND is_active = TRUE
                  AND COALESCE(is_permanent, FALSE) = FALSE
                  AND COALESCE(ttl_hours, 10) > 0
                  AND COALESCE(
                        expires_at,
                        COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) + make_interval(hours => COALESCE(ttl_hours, 10))
                      ) <= CURRENT_TIMESTAMP
                """,
                int(user_id),
            )

            base_query = """
                SELECT id, user_id, workspace_id, chat_id, title, is_active,
                       ttl_hours,
                       COALESCE(is_permanent, FALSE) AS is_permanent,
                       COALESCE(
                           expires_at,
                           CASE
                               WHEN COALESCE(is_permanent, FALSE) OR ttl_hours IS NULL OR ttl_hours <= 0 THEN NULL
                               ELSE COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) + make_interval(hours => COALESCE(ttl_hours, 10))
                           END
                       ) AS expires_at,
                       created_at, updated_at
                FROM editor_workspaces
                WHERE user_id = $1
                  AND is_active = TRUE
                  AND (
                      COALESCE(is_permanent, FALSE) = TRUE
                      OR ttl_hours IS NULL
                      OR ttl_hours <= 0
                      OR COALESCE(
                            expires_at,
                            COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) + make_interval(hours => COALESCE(ttl_hours, 10))
                         ) > CURRENT_TIMESTAMP
                  )
            """
            if chat_id:
                rows = await conn.fetch(
                    base_query + " AND chat_id = $2 ORDER BY updated_at DESC",
                    int(user_id), str(chat_id),
                )
            else:
                rows = await conn.fetch(
                    base_query + " ORDER BY updated_at DESC",
                    int(user_id),
                )
            return [_serialize_workspace_row(r) for r in rows]

    async def owns(self, user_id: int, workspace_id: str) -> bool:
        """Проверка: принадлежит ли активный и живой по TTL воркспейс юзеру."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM editor_workspaces
                WHERE user_id = $1
                  AND workspace_id = $2
                  AND is_active = TRUE
                  AND (
                      COALESCE(is_permanent, FALSE) = TRUE
                      OR ttl_hours IS NULL
                      OR ttl_hours <= 0
                      OR COALESCE(
                            expires_at,
                            COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) + make_interval(hours => COALESCE(ttl_hours, 10))
                         ) > CURRENT_TIMESTAMP
                  )
                """,
                int(user_id), str(workspace_id),
            )
            return row is not None

    async def deactivate(self, user_id: int, workspace_id: str) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """UPDATE editor_workspaces SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = $1 AND workspace_id = $2""",
                int(user_id), str(workspace_id),
            )
            return res.endswith("1")

    async def update_ttl_status(
        self,
        workspace_id: str,
        is_permanent: bool | None = None,
        ttl_hours: int | None = DEFAULT_TTL_HOURS,
        is_active: bool | None = None,
    ) -> int:
        """Обновить статус хранения воркспейса по запросу от agent_sandbox."""
        wid = str(workspace_id)
        async with self.pool.acquire() as conn:
            if is_active is False:
                self._pending_permanent.discard(wid)
                res = await conn.execute(
                    """
                    UPDATE editor_workspaces
                    SET is_active = FALSE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = $1
                    """,
                    wid,
                )
                try:
                    return int(res.split()[-1])
                except Exception:
                    return 0

            perm = bool(is_permanent) or ttl_hours is None or (isinstance(ttl_hours, int) and ttl_hours <= 0)
            if perm:
                res = await conn.execute(
                    """
                    UPDATE editor_workspaces
                    SET is_permanent = TRUE,
                        ttl_hours = NULL,
                        expires_at = NULL,
                        is_active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = $1
                    """,
                    wid,
                )
                try:
                    cnt = int(res.split()[-1])
                except Exception:
                    cnt = 0
                if cnt == 0:
                    self._pending_permanent.add(wid)
                else:
                    self._pending_permanent.discard(wid)
                return cnt
            else:
                self._pending_permanent.discard(wid)
                eff_ttl = int(ttl_hours or DEFAULT_TTL_HOURS)
                res = await conn.execute(
                    """
                    UPDATE editor_workspaces
                    SET is_permanent = FALSE,
                        ttl_hours = $2::INTEGER,
                        expires_at = CURRENT_TIMESTAMP + make_interval(hours => $2::INTEGER),
                        is_active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE workspace_id = $1
                    """,
                    wid,
                    eff_ttl,
                )
                try:
                    return int(res.split()[-1])
                except Exception:
                    return 0
