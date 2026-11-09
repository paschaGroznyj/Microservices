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
READ_SCOPE = "group"
MAX_READERS = 200

_CHAT_READS_DDL = """
    CREATE TABLE IF NOT EXISTS chat_reads (
        scope        TEXT        NOT NULL,
        chat_id      BIGINT      NOT NULL,
        user_id      BIGINT      NOT NULL,
        username     TEXT,
        last_read_id BIGINT      NOT NULL,
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (scope, chat_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS ix_chat_reads_chat_last ON chat_reads(scope, chat_id, last_read_id);
"""

# UPSERT пачки: отметка только растёт, RETURNING отдаёт реально сдвинутые
_READS_UPSERT = """
    INSERT INTO chat_reads AS r (scope, chat_id, user_id, username, last_read_id)
    SELECT $1, t.chat_id, t.user_id, NULLIF(t.username, ''), t.last_read_id
    FROM unnest($2::bigint[], $3::bigint[], $4::text[], $5::bigint[])
         AS t(chat_id, user_id, username, last_read_id)
    ON CONFLICT (scope, chat_id, user_id) DO UPDATE
    SET last_read_id = GREATEST(r.last_read_id, EXCLUDED.last_read_id),
        username     = COALESCE(EXCLUDED.username, r.username),
        updated_at   = CURRENT_TIMESTAMP
    WHERE EXCLUDED.last_read_id > r.last_read_id
       OR (EXCLUDED.username IS NOT NULL AND EXCLUDED.username IS DISTINCT FROM r.username)
    RETURNING r.chat_id, r.user_id, r.last_read_id
"""


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
            # отметки прочтения (универсальная таблица, см. 05_chat_reads.sql)
            await conn.execute(_CHAT_READS_DDL)
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

    # ── Прочтение ──────────────────────────────────────────

    async def mark_reads(self, ops: list) -> list:
        """ops: [{room_uid, user_id, username, last_read_id}] от шины reads_bus.
        Отметка клампится к последнему id комнаты (фронт не задерёт её вперёд)
        и только растёт. Возвращает [{room_uid, user_id, last_read_id}] по
        отметкам, которые реально сдвинулись, для события read."""
        best: dict = {}
        for op in ops or []:
            try:
                room_uid = str(op["room_uid"])
                uid, rid = int(op["user_id"]), int(op["last_read_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if rid <= 0 or not room_uid:
                continue
            name = (op.get("username") or "")[:128]
            prev = best.get((room_uid, uid))
            if prev is None or rid > prev[0]:
                best[(room_uid, uid)] = (rid, name or (prev[1] if prev else ""))
        if not best:
            return []
        uids_by_room = {}
        async with self.pool.acquire() as conn:
            rooms = await conn.fetch("""
                SELECT r.id, r.room_uid,
                       (SELECT MAX(m.id) FROM group_messages m WHERE m.room_id = r.id) AS last_id
                FROM group_rooms r
                WHERE r.room_uid = ANY($1::text[]) AND r.is_deleted = FALSE
            """, sorted({k[0] for k in best}))
            room_by_uid = {r["room_uid"]: (r["id"], r["last_id"] or 0) for r in rooms}
            chat_ids, user_ids, names, read_ids = [], [], [], []
            for (room_uid, uid), (rid, name) in best.items():
                room = room_by_uid.get(room_uid)
                if not room or room[1] <= 0:
                    continue
                chat_ids.append(room[0]); user_ids.append(uid)
                names.append(name); read_ids.append(min(rid, room[1]))
                uids_by_room[room[0]] = room_uid
            if not chat_ids:
                return []
            rows = await conn.fetch(_READS_UPSERT, READ_SCOPE, chat_ids, user_ids, names, read_ids)
        return [{"room_uid": uids_by_room[r["chat_id"]], "user_id": r["user_id"],
                 "last_read_id": r["last_read_id"]} for r in rows]

    async def read_state(self, room_uid: str, viewer_id: int) -> dict:
        """Для ответа истории и счётчика непрочитанных:
          read_upto       — MAX отметки других пользователей: мои сообщения с
                            id <= read_upto получают две галочки;
          my_last_read_id — своя отметка; нет записи -> последний id комнаты
                            (первый вход открывается внизу, без 164 непрочитанных);
          has_mark        — есть ли своя запись. Без неё фронт не считает
                            my_last_read_id отправленным и шлёт первую отметку
                            по видимым сообщениям, иначе первый вход не пишет ничего;
          unread          — чужие неудалённые сообщения с id > my_last_read_id;
          first_unread_id — первое из них (там фронт ставит разделитель);
          last_id         — последний id комнаты на момент ответа;
          tail            — сколько неудалённых сообщений (любых) с id >= first_unread_id:
                            по нему история сразу отдаёт страницу, где есть разделитель."""
        empty = {"read_upto": 0, "my_last_read_id": 0, "has_mark": False,
                 "unread": 0, "first_unread_id": None, "last_id": 0, "tail": 0}
        room = await self.get_room_by_uid(room_uid)
        if not room:
            return empty
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                WITH me AS (
                  SELECT last_read_id FROM chat_reads
                   WHERE scope = $1 AND chat_id = $2 AND user_id = $3
                ), last AS (
                  SELECT COALESCE(MAX(id), 0) AS id FROM group_messages WHERE room_id = $2
                ), mark AS (
                  SELECT COALESCE((SELECT last_read_id FROM me), (SELECT id FROM last)) AS id
                ), unread AS (
                  SELECT COUNT(*) AS n, MIN(id) AS first_id FROM group_messages
                   WHERE room_id = $2 AND is_deleted = FALSE AND user_id <> $3
                     AND id > (SELECT id FROM mark)
                )
                SELECT
                  (SELECT COALESCE(MAX(last_read_id), 0) FROM chat_reads
                    WHERE scope = $1 AND chat_id = $2 AND user_id <> $3) AS read_upto,
                  (SELECT id FROM mark) AS my_last_read_id,
                  EXISTS(SELECT 1 FROM me) AS has_mark,
                  (SELECT id FROM last) AS last_id,
                  (SELECT n FROM unread) AS unread,
                  (SELECT first_id FROM unread) AS first_unread_id,
                  (SELECT COUNT(*) FROM group_messages
                    WHERE room_id = $2 AND is_deleted = FALSE
                      AND id >= (SELECT first_id FROM unread)) AS tail
            """, READ_SCOPE, room["id"], int(viewer_id))
        def num(k):
            try:
                return int(row[k] or 0)
            except (KeyError, TypeError, ValueError):
                return 0
        first = None
        try:
            first = row["first_unread_id"]
        except (KeyError, TypeError):
            pass
        return {"read_upto": num("read_upto"),
                "my_last_read_id": num("my_last_read_id"),
                "has_mark": bool(row["has_mark"]),
                "unread": num("unread"),
                "first_unread_id": int(first) if first else None,
                "last_id": num("last_id"),
                "tail": num("tail")}

    async def get_readers(self, message_id: int, viewer_id: int):
        """Кто прочитал сообщение. Только автор сообщения (как в TG).
        None — нет сообщения, False — чужое сообщение."""
        async with self.pool.acquire() as conn:
            msg = await conn.fetchrow("""
                SELECT id, room_id, user_id FROM group_messages
                WHERE id = $1 AND is_deleted = FALSE
            """, int(message_id))
            if not msg:
                return None
            if int(msg["user_id"]) != int(viewer_id):
                return False
            rows = await conn.fetch("""
                SELECT user_id, username, updated_at FROM chat_reads
                WHERE scope = $1 AND chat_id = $2 AND last_read_id >= $3 AND user_id <> $4
                ORDER BY updated_at DESC
                LIMIT $5
            """, READ_SCOPE, msg["room_id"], msg["id"], msg["user_id"], MAX_READERS)
        return [{"user_id": r["user_id"], "username": r["username"]} for r in rows]

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
