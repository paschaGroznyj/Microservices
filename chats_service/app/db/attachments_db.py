"""Вложения чата для правой панели «Медиа / Файлы / Ссылки».

Файлы и ссылки собираются по всей цепочке форков (chat_chain): из родителя видны
только сообщения до точки форка включительно (id <= upto), как в истории чата.
Видимость сообщений та же, что у /chat/{id}/messages: свои и ответы агента (user_id = -1).

Пагинация курсором по (chats.id, chat_files.id) от новых к старым: SERIAL id растут
монотонно, поэтому страницы не теряют и не дублируют записи, даже если в чат
приходят новые файлы, пока панель открыта.
"""
import re

MEDIA_EXTS = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif", "heic",
              "mp4", "mov", "webm", "mkv", "avi", "m4v")
VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v"}
TABS = ("media", "files", "links")
AUTHORS = ("all", "me", "agent")

# Одна и та же ссылка в Python и в PostgreSQL (ARE): http(s)://, до пробела или закрывающей скобки/кавычки.
_URL_PG = r"""https?://[^][[:space:]<>"'`()]+"""
_URL_PY = re.compile(r"""https?://[^\]\[\s<>"'`()]+""")
_MD_LINK = re.compile(r"""\[([^\]\n]{1,200})\]\((https?://[^\s)]+)\)""")
_TRAIL = re.compile(r"[.,;:!?]+$")
_TRAIL_PG = r"[.,;:!?]+$"
MAX_LINKS_PER_MSG = 20

_IS_MEDIA = f"""(lower(COALESCE(f.file_kind, '')) IN ('image', 'video')
    OR lower(COALESCE(f.file_ext, '')) IN ({", ".join(f"'{e}'" for e in MEDIA_EXTS)}))"""

# Файлы, видимые в чате $1 пользователю $2. LATERAL по message_id: в chats он не уникален.
_FILES_SRC = """
    FROM chat_chain($1) ch
    JOIN chat_files f ON f.chat_id = ch.chat_id
    JOIN LATERAL (
        SELECT x.id, x.user_id, x.is_from_bot, x.created_at FROM chats x
        WHERE x.chat_id = f.chat_id AND x.message_id = f.message_id
        ORDER BY x.id LIMIT 1
    ) m ON TRUE
    WHERE (ch.upto IS NULL OR m.id <= ch.upto)
      AND (m.user_id = $2 OR m.user_id = -1)
"""

# Сообщения со ссылками, видимые в чате $1 пользователю $2.
_LINK_MSGS_SRC = """
    FROM chat_chain($1) ch
    JOIN chats m ON m.chat_id = ch.chat_id AND (ch.upto IS NULL OR m.id <= ch.upto)
    WHERE (m.user_id = $2 OR m.user_id = -1)
      AND m.message_text ~ 'https?://'
"""


def _author_sql(author: str, files: bool) -> str:
    if author == "me":
        return " AND f.source = 'user'" if files else " AND NOT COALESCE(m.is_from_bot, FALSE)"
    if author == "agent":
        return " AND f.source = 'agent'" if files else " AND COALESCE(m.is_from_bot, FALSE)"
    return ""


def parse_cursor(cursor: str | None, tab: str) -> tuple[int, int] | None:
    """Курсор файлов «mid.fid», ссылок «mid». Битый курсор — ValueError."""
    if not cursor:
        return None
    if tab == "links":
        if not re.fullmatch(r"\d{1,12}", cursor):
            raise ValueError("bad cursor")
        return int(cursor), 0
    m = re.fullmatch(r"(\d{1,12})\.(\d{1,12})", cursor)
    if not m:
        raise ValueError("bad cursor")
    return int(m.group(1)), int(m.group(2))


def extract_links(text: str) -> list[dict]:
    """Ссылки сообщения без повторов. У markdown-ссылки [текст](url) текст становится заголовком."""
    if not text:
        return []
    titles = {}
    for t, u in _MD_LINK.findall(text):
        titles.setdefault(_TRAIL.sub("", u), t.strip())
    out, seen = [], set()
    for raw in _URL_PY.findall(text):
        url = _TRAIL.sub("", raw)
        if len(url) <= len("https://") or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": titles.get(url) or None})
        if len(out) >= MAX_LINKS_PER_MSG:
            break
    return out


def _kind(row) -> str:
    ext = (row["file_ext"] or "").lower()
    kind = (row["file_kind"] or "").lower()
    if kind == "video" or ext in VIDEO_EXTS:
        return "video"
    if kind == "image" or ext in MEDIA_EXTS:
        return "image"
    return "file"


class AttachmentsDB:
    def __init__(self, pool=None):
        self.pool = pool

    async def user_id(self, conn, username: str) -> int | None:
        return await conn.fetchval("SELECT id FROM users WHERE username = $1", username)

    async def owns_chat(self, conn, chat_id: str, uid: int) -> bool:
        """Чат свой: есть строка сайдбара, своё сообщение или пользователь создал эту ветку."""
        return bool(await conn.fetchval("""
            SELECT EXISTS (SELECT 1 FROM chat_sidebar_meta
                           WHERE chat_id = $1 AND user_id = $2 AND NOT is_deleted)
                OR EXISTS (SELECT 1 FROM chats WHERE chat_id = $1 AND user_id = $2)
                OR EXISTS (SELECT 1 FROM chats_forks WHERE child_chat_id = $1 AND user_id = $2)
        """, chat_id, uid))

    async def chain(self, conn, chat_id: str, uid: int) -> list[dict]:
        rows = await conn.fetch("""
            SELECT c.chat_id, c.upto, c.depth, p.created_at AS point_at,
                   COALESCE(
                       (SELECT NULLIF(renamed_title, '') FROM chat_sidebar_meta
                        WHERE chat_id = c.chat_id AND user_id = $2),
                       (SELECT NULLIF(auto_title, '') FROM chat_sidebar_meta
                        WHERE chat_id = c.chat_id AND NULLIF(auto_title, '') IS NOT NULL
                        ORDER BY (user_id = $2) DESC, updated_at DESC LIMIT 1)
                   ) AS title
            FROM chat_chain($1) c
            LEFT JOIN chats p ON p.id = c.upto
            ORDER BY c.depth
        """, chat_id, uid)
        return [{"chat_id": r["chat_id"], "depth": r["depth"], "title": r["title"],
                 "point_id": r["upto"], "point_at": r["point_at"].isoformat() if r["point_at"] else None}
                for r in rows]

    async def counts(self, conn, chat_id: str, uid: int, author: str) -> dict:
        row = await conn.fetchrow(f"""
            SELECT count(*) FILTER (WHERE {_IS_MEDIA}) AS media,
                   count(*) FILTER (WHERE NOT {_IS_MEDIA}) AS files
            {_FILES_SRC}{_author_sql(author, True)}
        """, chat_id, uid)
        links = await self._count_links(conn, chat_id, uid, author)
        return {"media": row["media"], "files": row["files"], "links": links}

    async def _count_links(self, conn, chat_id: str, uid: int, author: str) -> int:
        # Как extract_links: каждая ссылка без хвостовой пунктуации, без повторов внутри сообщения.
        # Шаблоны передаются параметрами: в них есть кавычки.
        return await conn.fetchval(f"""
            SELECT count(*) FROM (
                SELECT DISTINCT q.id, q.u FROM (
                    SELECT m.id, regexp_replace((regexp_matches(m.message_text, $3, 'g'))[1], $4, '') AS u
                    {_LINK_MSGS_SRC}{_author_sql(author, False)}
                ) q WHERE length(q.u) > 8
            ) s
        """, chat_id, uid, f"({_URL_PG})", _TRAIL_PG)

    async def files_page(self, conn, chat_id: str, uid: int, tab: str, author: str,
                         cur: tuple[int, int] | None, limit: int) -> tuple[list, str | None]:
        args = [chat_id, uid]
        where = (" AND " if tab == "media" else " AND NOT ") + _IS_MEDIA + _author_sql(author, True)
        if cur:
            args += [cur[0], cur[1]]
            where += " AND (m.id, f.id) < ($3, $4)"
        args.append(limit + 1)
        rows = await conn.fetch(f"""
            SELECT f.id, f.message_id, f.chat_id, f.file_name, f.file_ext, f.source, f.file_kind,
                   f.image_key, f.file_size, f.thumbhash, f.width, f.height,
                   m.id AS msg_id, m.created_at, ch.depth
            {_FILES_SRC}{where}
            ORDER BY m.id DESC, f.id DESC
            LIMIT ${len(args)}
        """, *args)
        more = len(rows) > limit
        rows = rows[:limit]
        items = [{
            "id": r["id"], "message_id": r["message_id"], "msg_id": r["msg_id"], "chat_id": r["chat_id"],
            "inherited": r["depth"] > 0, "name": r["file_name"], "ext": (r["file_ext"] or "").lower(),
            "kind": _kind(r), "source": r["source"] or "user",
            "author": "agent" if r["source"] == "agent" else "user",
            "image_key": r["image_key"], "size": r["file_size"], "thumbhash": r["thumbhash"],
            "width": r["width"], "height": r["height"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in rows]
        nxt = f"{rows[-1]['msg_id']}.{rows[-1]['id']}" if more and rows else None
        return items, nxt

    async def links_page(self, conn, chat_id: str, uid: int, author: str,
                         cur: tuple[int, int] | None, limit: int) -> tuple[list, str | None]:
        args = [chat_id, uid]
        where = _author_sql(author, False)
        if cur:
            args.append(cur[0])
            where += " AND m.id < $3"
        args.append(limit + 1)
        rows = await conn.fetch(f"""
            SELECT m.id, m.message_id, m.chat_id, m.is_from_bot, m.created_at, m.message_text, ch.depth
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
                    "id": f"{r['id']}.{k}", "message_id": r["message_id"], "msg_id": r["id"],
                    "chat_id": r["chat_id"], "inherited": r["depth"] > 0,
                    "author": "agent" if r["is_from_bot"] else "user",
                    "url": ln["url"], "title": ln["title"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })
        nxt = str(rows[-1]["id"]) if more and rows else None
        return items, nxt

    async def list(self, chat_id: str, username: str, *, tab: str = "media", author: str = "all",
                   cursor: str | None = None, limit: int = 30) -> dict | None:
        """None — чата нет или он чужой. ValueError — битый курсор."""
        cur = parse_cursor(cursor, tab)
        async with self.pool.acquire() as conn:
            uid = await self.user_id(conn, username)
            if uid is None or not await self.owns_chat(conn, chat_id, uid):
                return None
            if tab == "links":
                items, nxt = await self.links_page(conn, chat_id, uid, author, cur, limit)
            else:
                items, nxt = await self.files_page(conn, chat_id, uid, tab, author, cur, limit)
            out = {"chat_id": chat_id, "tab": tab, "author": author, "items": items, "next_cursor": nxt}
            if not cursor:
                out["counts"] = await self.counts(conn, chat_id, uid, author)
                out["chain"] = await self.chain(conn, chat_id, uid)
            return out
