"""
Слой БД для ОБЩЕГО / группового чата.

Отдельный модуль, чтобы не раздувать chats_tables.py. Переиспользует
тот же пул asyncpg, что и DataBaseChats (передаётся в конструктор),
поэтому отдельного подключения не плодим.

Схема заложена на расширение (приватные группы/личка):
  group_rooms        — комнаты (global | group | direct)
  group_messages     — сообщения (soft-delete, edit, pin)
  group_room_members — участники (для global не используется)
Сообщения никогда не удаляются физически — только is_deleted=TRUE (как в chats).
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

GLOBAL_ROOM_UID = "global"


class GroupChatDB:
    def __init__(self, pool):
        # пул asyncpg передаётся извне (общий с DataBaseChats)
        self.pool = pool

    async def init_schema(self):
        """Создаёт таблицы группового чата и предсоздаёт общую комнату."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS group_rooms (
                    id BIGSERIAL PRIMARY KEY,
                    room_uid TEXT NOT NULL UNIQUE,
                    room_type TEXT NOT NULL DEFAULT 'group',  -- global | group | direct
                    title TEXT,
                    created_by BIGINT,
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_group_rooms_room_uid ON group_rooms(room_uid);
                CREATE INDEX IF NOT EXISTS ix_group_rooms_type ON group_rooms(room_type);

                CREATE TABLE IF NOT EXISTS group_messages (
                    id BIGSERIAL PRIMARY KEY,
                    room_id BIGINT NOT NULL REFERENCES group_rooms(id),
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    message_text TEXT,
                    client_msg_id TEXT,
                    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                    is_pinned BOOLEAN NOT NULL DEFAULT FALSE,
                    edited_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_group_messages_room ON group_messages(room_id, id);
                CREATE INDEX IF NOT EXISTS ix_group_messages_pinned ON group_messages(room_id, is_pinned);
                CREATE INDEX IF NOT EXISTS ix_group_messages_client ON group_messages(client_msg_id);

                CREATE TABLE IF NOT EXISTS group_room_members (
                    room_id BIGINT NOT NULL REFERENCES group_rooms(id),
                    user_id BIGINT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',  -- owner | admin | member
                    joined_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (room_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS group_message_files (
                    id BIGSERIAL PRIMARY KEY,
                    message_id BIGINT REFERENCES group_messages(id),
                    room_id BIGINT NOT NULL REFERENCES group_rooms(id),
                    client_msg_id TEXT,
                    obs_key TEXT NOT NULL,
                    file_name TEXT,
                    mime_type TEXT,
                    size_bytes BIGINT,
                    is_image BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_group_files_msg ON group_message_files(message_id);
                CREATE INDEX IF NOT EXISTS ix_group_files_client ON group_message_files(client_msg_id);
                CREATE INDEX IF NOT EXISTS ix_group_files_room ON group_message_files(room_id);
            """)
            # Предсоздание общей комнаты (единственный room_uid='global')
            await conn.execute("""
                INSERT INTO group_rooms (room_uid, room_type, title, created_by)
                VALUES ($1, 'global', 'Общий чат', NULL)
                ON CONFLICT (room_uid) DO NOTHING
            """, GLOBAL_ROOM_UID)
        logger.info("[group-chat] схема инициализирована, общая комната готова")

    # ── Комнаты ────────────────────────────────────────────────

    async def get_room_by_uid(self, room_uid: str):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, room_uid, room_type, title, created_by, is_deleted, created_at
                FROM group_rooms WHERE room_uid = $1 AND is_deleted = FALSE
            """, room_uid)
            return dict(row) if row else None

    async def ensure_global_room_id(self) -> int:
        room = await self.get_room_by_uid(GLOBAL_ROOM_UID)
        if room:
            return room["id"]
        await self.init_schema()
        room = await self.get_room_by_uid(GLOBAL_ROOM_UID)
        return room["id"] if room else None

    # ── Сообщения ──────────────────────────────────────────

    async def add_message(self, room_uid: str, user_id: int, username: str,
                          message_text: str, client_msg_id: str | None = None):
        """Вставляет сообщение в комнату. Возвращает сохранённую строку (dict)."""
        if user_id is None or not room_uid:
            logger.warning("[group-chat] пропущено: нет user_id/room_uid")
            return None
        room = await self.get_room_by_uid(room_uid)
        if not room:
            logger.warning(f"[group-chat] комната не найдена: {room_uid}")
            return None
        now_utc = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO group_messages
                    (room_id, user_id, username, message_text, client_msg_id, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, room_id, user_id, username, message_text, client_msg_id,
                          is_deleted, is_pinned, edited_at, created_at
            """, room["id"], user_id, username, (message_text or "")[:20000],
                 client_msg_id, now_utc)
        return self._serialize(dict(row), room_uid)

    async def get_messages(self, room_uid: str, limit: int = 50, offset: int = 0,
                           before_id: int | None = None):
        """История комнаты, новые с конца.

        before_id — курсор для подгрузки старых сообщений: отдаём limit штук
        с id < before_id. Курсор не сдвигается, когда в комнату пишут новые
        сообщения (offset при этом съезжает и даёт дубли). offset оставлен
        для обратной совместимости и при before_id игнорируется.
        """
        room = await self.get_room_by_uid(room_uid)
        if not room:
            return {"messages": [], "total_count": 0, "has_more": False}
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        async with self.pool.acquire() as conn:
            total = await conn.fetchval("""
                SELECT COUNT(*) FROM group_messages
                WHERE room_id = $1 AND is_deleted = FALSE
            """, room["id"])
            if before_id:
                # +1 строка, чтобы понять, есть ли ещё старее, без второго COUNT
                rows = await conn.fetch("""
                    SELECT id, room_id, user_id, username, message_text, client_msg_id,
                           is_deleted, is_pinned, edited_at, created_at
                    FROM group_messages
                    WHERE room_id = $1 AND is_deleted = FALSE AND id < $2
                    ORDER BY id DESC
                    LIMIT $3
                """, room["id"], int(before_id), limit + 1)
                has_more = len(rows) > limit
                rows = rows[:limit]
            else:
                rows = await conn.fetch("""
                    SELECT id, room_id, user_id, username, message_text, client_msg_id,
                           is_deleted, is_pinned, edited_at, created_at
                    FROM group_messages
                    WHERE room_id = $1 AND is_deleted = FALSE
                    ORDER BY id DESC
                    LIMIT $2 OFFSET $3
                """, room["id"], limit, offset)
                has_more = (offset + len(rows)) < int(total or 0)
        # возвращаем в хронологическом порядке (старые сверху)
        msgs = [self._serialize(dict(r), room_uid) for r in reversed(rows)]
        files_map = await self.get_files_for_messages([m["id"] for m in msgs])
        for m in msgs:
            m["files"] = files_map.get(m["id"], [])
        return {
            "messages": msgs,
            "total_count": int(total or 0),
            "has_more": has_more,
        }

    async def get_pinned(self, room_uid: str):
        room = await self.get_room_by_uid(room_uid)
        if not room:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, room_id, user_id, username, message_text, client_msg_id,
                       is_deleted, is_pinned, edited_at, created_at
                FROM group_messages
                WHERE room_id = $1 AND is_pinned = TRUE AND is_deleted = FALSE
                ORDER BY id DESC
            """, room["id"])
        return [self._serialize(dict(r), room_uid) for r in rows]

    async def soft_delete(self, message_id: int, user_id: int, is_admin: bool = False):
        """Мягкое удаление (как в chats — никогда не удаляем физически).
        Автор удаляет своё; admin — любое."""
        async with self.pool.acquire() as conn:
            if is_admin:
                res = await conn.execute("""
                    UPDATE group_messages SET is_deleted = TRUE WHERE id = $1
                """, message_id)
            else:
                res = await conn.execute("""
                    UPDATE group_messages SET is_deleted = TRUE
                    WHERE id = $1 AND user_id = $2
                """, message_id, user_id)
        return res.endswith("1")

    async def edit_message(self, message_id: int, user_id: int, new_text: str):
        """Редактирование — только автор своё. Возвращает обновлённую строку."""
        new_text = (new_text or "").strip()
        if not new_text:
            return None
        now_utc = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE group_messages
                SET message_text = $3, edited_at = $4
                WHERE id = $1 AND user_id = $2 AND is_deleted = FALSE
                RETURNING id, room_id, user_id, username, message_text, client_msg_id,
                          is_deleted, is_pinned, edited_at, created_at
            """, message_id, user_id, new_text[:20000], now_utc)
        if not row:
            return None
        room_uid = await self._room_uid_by_id(row["room_id"])
        msg = self._serialize(dict(row), room_uid)
        files_map = await self.get_files_for_messages([msg["id"]])
        msg["files"] = files_map.get(msg["id"], [])
        return msg

    async def set_pin(self, message_id: int, is_pinned: bool):
        """Закреп/откреп сообщения. Возвращает обновлённую строку."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE group_messages SET is_pinned = $2
                WHERE id = $1 AND is_deleted = FALSE
                RETURNING id, room_id, user_id, username, message_text, client_msg_id,
                          is_deleted, is_pinned, edited_at, created_at
            """, message_id, is_pinned)
        if not row:
            return None
        room_uid = await self._room_uid_by_id(row["room_id"])
        msg = self._serialize(dict(row), room_uid)
        files_map = await self.get_files_for_messages([msg["id"]])
        msg["files"] = files_map.get(msg["id"], [])
        return msg

    # ── Файлы (вложения) ───────────────────────────────────────

    async def add_file(self, room_uid: str, client_msg_id: str, obs_key: str,
                       file_name: str, mime_type: str, size_bytes: int,
                       is_image: bool):
        """Регистрирует загруженный файл. message_id пока NULL — доклеим
        по client_msg_id, когда консюмер вставит сообщение."""
        room = await self.get_room_by_uid(room_uid)
        if not room:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO group_message_files
                    (message_id, room_id, client_msg_id, obs_key, file_name,
                     mime_type, size_bytes, is_image)
                VALUES (NULL, $1, $2, $3, $4, $5, $6, $7)
                RETURNING id, obs_key, file_name, mime_type, size_bytes, is_image
            """, room["id"], client_msg_id, obs_key, file_name,
                 mime_type, size_bytes, is_image)
        return dict(row) if row else None

    async def bind_files_to_message(self, message_id: int, client_msg_id: str):
        """Привязывает orphan-файлы (message_id IS NULL) к вставленному
        сообщению по client_msg_id. Возвращает список файлов сообщения."""
        if not client_msg_id:
            return []
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE group_message_files
                SET message_id = $1
                WHERE client_msg_id = $2 AND message_id IS NULL
            """, message_id, client_msg_id)
            rows = await conn.fetch("""
                SELECT id, obs_key, file_name, mime_type, size_bytes, is_image
                FROM group_message_files
                WHERE message_id = $1
                ORDER BY id ASC
            """, message_id)
        return [self._serialize_file(dict(r)) for r in rows]

    async def get_files_for_messages(self, message_ids: list) -> dict:
        """Возвращает {message_id: [files...]} для набора сообщений."""
        if not message_ids:
            return {}
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, message_id, obs_key, file_name, mime_type,
                       size_bytes, is_image
                FROM group_message_files
                WHERE message_id = ANY($1::bigint[])
                ORDER BY id ASC
            """, message_ids)
        result: dict = {}
        for r in rows:
            d = dict(r)
            result.setdefault(d["message_id"], []).append(self._serialize_file(d))
        return result

    async def get_file_by_id(self, file_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, obs_key, file_name, mime_type, size_bytes, is_image
                FROM group_message_files WHERE id = $1
            """, file_id)
        return dict(row) if row else None

    @staticmethod
    def _serialize_file(row: dict) -> dict:
        return {
            "id": row["id"],
            "obs_key": row.get("obs_key"),
            "file_name": row.get("file_name"),
            "mime_type": row.get("mime_type"),
            "size_bytes": row.get("size_bytes"),
            "is_image": row.get("is_image", False),
        }

    # ── helpers ────────────────────────────────────────────────

    async def _room_uid_by_id(self, room_id: int):
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT room_uid FROM group_rooms WHERE id = $1", room_id)

    @staticmethod
    def _serialize(row: dict, room_uid: str) -> dict:
        created = row.get("created_at")
        edited = row.get("edited_at")
        return {
            "id": row["id"],
            "room_uid": room_uid,
            "user_id": row["user_id"],
            "username": row.get("username"),
            "message_text": row.get("message_text"),
            "client_msg_id": row.get("client_msg_id"),
            "is_pinned": row.get("is_pinned", False),
            "is_deleted": row.get("is_deleted", False),
            "edited_at": edited.isoformat() if edited else None,
            "created_at": created.isoformat() if created else None,
        }
