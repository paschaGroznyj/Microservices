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
import json
import logging
import re
from datetime import datetime, timezone

from app.core.reactions import normalize_emoji, summarize

logger = logging.getLogger(__name__)

_THUMBHASH_RE = re.compile(r"^[A-Za-z0-9+/]{8,64}={0,2}$")


def _pixels(v) -> int | None:
    """width/height: целое 1..65535, иначе None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= 65535 else None


def _thumbhash(v) -> str | None:
    return v if isinstance(v, str) and _THUMBHASH_RE.match(v) else None

GLOBAL_ROOM_UID = "global"


class GroupChatDB:
    def __init__(self, pool):
        # пул asyncpg передаётся извне (общий с DataBaseChats)
        self.pool = pool

    async def init_schema(self):
        """Таблицы группового чата создаются в postgres/sql/02_chats/02_group_chats.sql.
        Гарантируем наличие дефолтной комнаты 'global'."""
        async with self.pool.acquire() as conn:
            # реакции {"user_id": "emoji"}
            await conn.execute(
                "ALTER TABLE group_messages ADD COLUMN IF NOT EXISTS reactions JSONB NOT NULL DEFAULT '{}'::jsonb")
            # метаданные медиа для лоадера: размеры кадра и ThumbHash (base64)
            for col, typ in (("width", "INTEGER"), ("height", "INTEGER"), ("thumbhash", "TEXT")):
                await conn.execute(
                    f"ALTER TABLE group_message_files ADD COLUMN IF NOT EXISTS {col} {typ}")
            await conn.execute("""
                INSERT INTO group_rooms (room_uid, room_type, title, created_by)
                VALUES ($1, 'global', 'Общий чат', NULL)
                ON CONFLICT (room_uid) DO NOTHING
            """, GLOBAL_ROOM_UID)
        logger.info("[group-chat] общая комната проверена")

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
            if client_msg_id:
                # повторная доставка из Kafka (рестарт консюмера, ретрай) не
                # плодит дубль. Консюмер один на группу, гонки нет.
                dup = await conn.fetchrow("""
                    SELECT id, room_id, user_id, username, message_text, client_msg_id,
                           is_deleted, is_pinned, edited_at, created_at, reactions
                    FROM group_messages
                    WHERE room_id = $1 AND client_msg_id = $2
                    ORDER BY id LIMIT 1
                """, room["id"], client_msg_id)
                if dup:
                    logger.info(f"[group-chat] дубль client_msg_id={client_msg_id} -> id={dup['id']}")
                    return None if dup["is_deleted"] else self._serialize(dict(dup), room_uid)
            row = await conn.fetchrow("""
                INSERT INTO group_messages
                    (room_id, user_id, username, message_text, client_msg_id, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, room_id, user_id, username, message_text, client_msg_id,
                          is_deleted, is_pinned, edited_at, created_at, reactions
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
                           is_deleted, is_pinned, edited_at, created_at, reactions
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
                           is_deleted, is_pinned, edited_at, created_at, reactions
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
                       is_deleted, is_pinned, edited_at, created_at, reactions
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
                          is_deleted, is_pinned, edited_at, created_at, reactions
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
                          is_deleted, is_pinned, edited_at, created_at, reactions
            """, message_id, is_pinned)
        if not row:
            return None
        room_uid = await self._room_uid_by_id(row["room_id"])
        msg = self._serialize(dict(row), room_uid)
        files_map = await self.get_files_for_messages([msg["id"]])
        msg["files"] = files_map.get(msg["id"], [])
        return msg

    # ── Реакции ────────────────────────────────────────────

    async def apply_reactions(self, ops: list) -> list:
        """ops: [{message_id, user_id, emoji}], emoji '' снимает реакцию.
        Одна транзакция на пачку. Возвращает [{room_uid, message_id, reactions}]
        по изменённым сообщениям (удалённые пропускаются)."""
        # последняя операция пользователя по сообщению побеждает
        last: dict = {}
        for op in ops or []:
            try:
                mid, uid = int(op["message_id"]), int(op["user_id"])
            except (KeyError, TypeError, ValueError):
                continue
            emoji = normalize_emoji(op.get("emoji"))
            if emoji is None or mid <= 0:
                continue
            last[(mid, uid)] = emoji
        if not last:
            return []
        touched = sorted({mid for mid, _ in last})
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for (mid, uid), emoji in last.items():
                    if emoji:
                        await conn.execute("""
                            UPDATE group_messages
                            SET reactions = COALESCE(reactions, '{}'::jsonb) || $2::jsonb
                            WHERE id = $1 AND is_deleted = FALSE
                        """, mid, json.dumps({str(uid): emoji}))
                    else:
                        await conn.execute("""
                            UPDATE group_messages
                            SET reactions = COALESCE(reactions, '{}'::jsonb) - $2::text
                            WHERE id = $1 AND is_deleted = FALSE
                        """, mid, str(uid))
                rows = await conn.fetch("""
                    SELECT m.id, r.room_uid, m.reactions
                    FROM group_messages m JOIN group_rooms r ON r.id = m.room_id
                    WHERE m.id = ANY($1::bigint[]) AND m.is_deleted = FALSE
                """, touched)
        return [{"room_uid": r["room_uid"], "message_id": r["id"],
                 "reactions": summarize(r["reactions"])} for r in rows]

    # ── Файлы (вложения) ───────────────────────────────────────

    async def add_file(self, room_uid: str, client_msg_id: str, obs_key: str,
                       file_name: str, mime_type: str, size_bytes: int,
                       is_image: bool, width: int | None = None,
                       height: int | None = None, thumbhash: str | None = None):
        """Регистрирует загруженный файл. message_id пока NULL — доклеим
        по client_msg_id, когда консюмер вставит сообщение."""
        room = await self.get_room_by_uid(room_uid)
        if not room:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO group_message_files
                    (message_id, room_id, client_msg_id, obs_key, file_name,
                     mime_type, size_bytes, is_image, width, height, thumbhash)
                VALUES (NULL, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, obs_key, file_name, mime_type, size_bytes, is_image,
                          width, height, thumbhash
            """, room["id"], client_msg_id, obs_key, file_name,
                 mime_type, size_bytes, is_image,
                 _pixels(width), _pixels(height), _thumbhash(thumbhash))
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
                SELECT id, obs_key, file_name, mime_type, size_bytes, is_image,
                       width, height, thumbhash
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
                       size_bytes, is_image, width, height, thumbhash
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
                SELECT id, obs_key, file_name, mime_type, size_bytes, is_image,
                       width, height, thumbhash
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
            "width": _pixels(row.get("width")),
            "height": _pixels(row.get("height")),
            "thumbhash": _thumbhash(row.get("thumbhash")),
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
            "reactions": summarize(row.get("reactions")),
        }
