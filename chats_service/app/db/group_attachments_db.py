"""Вложения общего / группового чата для правой панели «Медиа / Файлы / Ссылки».

Те же вкладки и курсоры, что у attachments_db (чаты агента), но без цепочки форков:
файлы берутся из group_message_files, ссылки из group_messages.message_text.
Удалённые сообщения не показываются. Фильтр автора: all | me | others (относительно viewer_id).

Пагинация курсором по (group_messages.id, group_message_files.id) от новых к старым,
у ссылок по group_messages.id. Формат курсора тот же: «mid.fid» и «mid».
"""
from app.db.attachments_db import MEDIA_EXTS, VIDEO_EXTS, _TRAIL_PG, _URL_PG, extract_links, parse_cursor

TABS = ("media", "files", "links")
AUTHORS = ("all", "me", "others")
_EXT = "lower(substring(COALESCE(f.file_name, '') from '\\.([A-Za-z0-9]{1,8})$'))"
_IS_MEDIA = f"""(f.is_image OR lower(COALESCE(f.mime_type, '')) LIKE 'video/%'
    OR {_EXT} IN ({", ".join(f"'{e}'" for e in MEDIA_EXTS)}))"""

_FILES_SRC = """
    FROM group_message_files f
    JOIN group_messages m ON m.id = f.message_id AND NOT m.is_deleted
    WHERE f.room_id = $1 AND $2::bigint IS NOT NULL
"""
# $2 (viewer_id) упомянут всегда: без фильтра автора asyncpg иначе не выведет тип параметра.
_LINK_MSGS_SRC = """
    FROM group_messages m
    WHERE m.room_id = $1 AND $2::bigint IS NOT NULL AND NOT m.is_deleted AND m.message_text ~ 'https?://'
"""


def _author_sql(author: str) -> str:
    if author == "me":
        return " AND m.user_id = $2"
    if author == "others":
        return " AND m.user_id <> $2"
    return ""


def _ext(name: str | None) -> str:
    n = name or ""
    i = n.rfind(".")
    return n[i + 1:].lower() if i > 0 else ""


def _kind(row) -> str:
    ext = _ext(row["file_name"])
    mime = (row["mime_type"] or "").lower()
    if mime.startswith("video/") or ext in VIDEO_EXTS:
        return "video"
    if row["is_image"] or mime.startswith("image/") or ext in MEDIA_EXTS:
        return "image"
    return "file"


class GroupAttachmentsDB:
    def __init__(self, pool=None):
        self.pool = pool

    async def room(self, conn, room_uid: str, viewer_id: int) -> dict | None:
        """Комната видна: общая (global) всем, остальные только участникам."""
        row = await conn.fetchrow("""
            SELECT r.id, r.room_type, r.title FROM group_rooms r
            WHERE r.room_uid = $1 AND NOT r.is_deleted
              AND (r.room_type = 'global' OR EXISTS (
                   SELECT 1 FROM group_room_members gm WHERE gm.room_id = r.id AND gm.user_id = $2))
        """, room_uid, viewer_id)
        return dict(row) if row else None

    async def counts(self, conn, room_id: int, viewer_id: int, author: str) -> dict:
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE {_IS_MEDIA}) AS media,
                   count(*) FILTER (WHERE NOT {_IS_MEDIA}) AS files
            {_FILES_SRC}{_author_sql(author)}
        """, room_id, viewer_id)
        links = await conn.fetchval(f"""
            SELECT count(*) FROM (
                SELECT DISTINCT q.id, q.u FROM (
                    SELECT m.id, regexp_replace((regexp_matches(m.message_text, $3, 'g'))[1], $4, '') AS u
                    {_LINK_MSGS_SRC}{_author_sql(author)}
                ) q WHERE length(q.u) > 8
            ) s
        """, room_id, viewer_id, f"({_URL_PG})", _TRAIL_PG)
        return {"media": row["media"], "files": row["files"], "links": links}

    async def files_page(self, conn, room_id: int, viewer_id: int, tab: str, author: str,
                         cur: tuple[int, int] | None, limit: int) -> tuple[list, str | None]:
        args = [room_id, viewer_id]
        where = (" AND " if tab == "media" else " AND NOT ") + _IS_MEDIA + _author_sql(author)
        if cur:
            args += [cur[0], cur[1]]
            where += " AND (m.id, f.id) < ($3, $4)"
        args.append(limit + 1)
        rows = await conn.fetch(f"""
            SELECT f.id, f.file_name, f.mime_type, f.size_bytes, f.is_image, f.width, f.height, f.thumbhash,
                   m.id AS msg_id, m.user_id, m.username, m.created_at
            {_FILES_SRC}{where}
            ORDER BY m.id DESC, f.id DESC
            LIMIT ${len(args)}
        """, *args)
        more = len(rows) > limit
        rows = rows[:limit]
        items = [{
            "id": r["id"], "msg_id": r["msg_id"], "name": r["file_name"] or "file", "ext": _ext(r["file_name"]),
            "kind": _kind(r), "mime": r["mime_type"], "size": r["size_bytes"],
            "width": r["width"], "height": r["height"], "thumbhash": r["thumbhash"],
            "author": "user" if r["user_id"] == viewer_id else "other",
            "user_id": r["user_id"], "username": r["username"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in rows]
        nxt = f"{rows[-1]['msg_id']}.{rows[-1]['id']}" if more and rows else None
        return items, nxt

    async def links_page(self, conn, room_id: int, viewer_id: int, author: str,
                         cur: tuple[int, int] | None, limit: int) -> tuple[list, str | None]:
        args = [room_id, viewer_id]
        where = _author_sql(author)
        if cur:
            args.append(cur[0])
            where += " AND m.id < $3"
        args.append(limit + 1)
        rows = await conn.fetch(f"""
            SELECT m.id, m.user_id, m.username, m.created_at, m.message_text
            {_LINK_MSGS_SRC}{where}
            ORDER BY m.id DESC
            LIMIT ${len(args)}
        """, *args)
        more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for r in rows:
            for k, ln in enumerate(extract_links(r["message_text"])):
                items.append({
                    "id": f"{r['id']}.{k}", "msg_id": r["id"],
                    "author": "user" if r["user_id"] == viewer_id else "other",
                    "user_id": r["user_id"], "username": r["username"],
                    "url": ln["url"], "title": ln["title"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
        nxt = str(rows[-1]["id"]) if more and rows else None
        return items, nxt

    async def list(self, room_uid: str, viewer_id: int, *, tab: str = "media", author: str = "all",
                   cursor: str | None = None, limit: int = 30) -> dict | None:
        """None — комнаты нет или она недоступна. ValueError — битый курсор."""
        cur = parse_cursor(cursor, tab)
        async with self.pool.acquire() as conn:
            room = await self.room(conn, room_uid, viewer_id)
            if room is None:
                return None
            if tab == "links":
                items, nxt = await self.links_page(conn, room["id"], viewer_id, author, cur, limit)
            else:
                items, nxt = await self.files_page(conn, room["id"], viewer_id, tab, author, cur, limit)
            out = {"room_uid": room_uid, "tab": tab, "author": author, "items": items, "next_cursor": nxt}
            if not cursor:
                out["counts"] = await self.counts(conn, room["id"], viewer_id, author)
            return out
