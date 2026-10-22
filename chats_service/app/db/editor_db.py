"""
Слой БД для реестра воркспейсов редактора (editor_workspaces).

Переиспользует общий пул asyncpg (передаётся в конструктор).
Таблица создаётся миграцией (migrations/..._editor_workspaces.sql), здесь только CRUD.
Физические файлы живут в песочнице, здесь — реестр «workspace_id → user_id».
"""
import logging

logger = logging.getLogger(__name__)


class EditorDB:
    def __init__(self, pool):
        self.pool = pool

    async def register(self, user_id: int, workspace_id: str,
                       chat_id: str = None, title: str = None) -> dict:
        """INSERT/UPSERT воркспейса за юзером. Вызывается тулом агента."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO editor_workspaces (user_id, workspace_id, chat_id, title)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, workspace_id) DO UPDATE SET
                    chat_id    = COALESCE(EXCLUDED.chat_id, editor_workspaces.chat_id),
                    title      = COALESCE(EXCLUDED.title, editor_workspaces.title),
                    is_active  = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id, user_id, workspace_id, chat_id, title, is_active, created_at
                """,
                user_id, workspace_id, chat_id, title,
            )
            return dict(row) if row else None

    async def list_for_user(self, user_id: int, chat_id: str = None) -> list:
        """Список активных воркспейсов юзера. Если chat_id задан — фильтр по чату."""
        async with self.pool.acquire() as conn:
            if chat_id:
                rows = await conn.fetch(
                    """SELECT id, user_id, workspace_id, chat_id, title, is_active, created_at, updated_at
                       FROM editor_workspaces
                       WHERE user_id = $1 AND is_active = TRUE AND chat_id = $2
                       ORDER BY updated_at DESC""",
                    user_id, chat_id,
                )
            else:
                rows = await conn.fetch(
                    """SELECT id, user_id, workspace_id, chat_id, title, is_active, created_at, updated_at
                       FROM editor_workspaces
                       WHERE user_id = $1 AND is_active = TRUE
                       ORDER BY updated_at DESC""",
                    user_id,
                )
            return [dict(r) for r in rows]

    async def owns(self, user_id: int, workspace_id: str) -> bool:
        """Проверка: принадлежит ли воркспейс юзеру (гейт доступа в редакторе)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT 1 FROM editor_workspaces
                   WHERE user_id = $1 AND workspace_id = $2 AND is_active = TRUE""",
                user_id, workspace_id,
            )
            return row is not None

    async def deactivate(self, user_id: int, workspace_id: str) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """UPDATE editor_workspaces SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
                   WHERE user_id = $1 AND workspace_id = $2""",
                user_id, workspace_id,
            )
            return res.endswith("1")
