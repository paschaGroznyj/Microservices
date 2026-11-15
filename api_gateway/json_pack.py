"""
api_gateway/json_pack.py
────────────────────────
Длинный JSON из текста сообщения → файл-вложение.

Pro-разметка (media-pro.js) шлёт правки обычным текстом: описание + блок
```json ...```. Блок на несколько тысяч символов не нужен ни в Kafka, ни в
контексте агента. Gateway вырезает такой блок из текста ДО отправки в
chats_service и отдаёт его в agent_service тем же путём, что и обычные
вложения (/web/upload_files, режим "file"):

  * agent_service кладёт байты в медиа-хранилище чата
    (media_store.put_media → media:chat:{chat_id}:blob:{mid} + :index)
    и на диск/OBS (/app/generated_files/{message_id}/{name}), откуда плитку
    отдаёт /api/chats/{chat}/files/{message_id}/{name};
  * в промпт агента попадает только маркер 📄[DOCUMENT] с media_id;
  * в Kafka и в БД уходит короткий текст + files=[{name, size, type}],
    в ленте это обычная плитка вложения (fa-tile).

Пакуем только блоки ```json, которые длиннее PACK_MIN_CHARS и парсятся как
JSON. Если у сообщения уже есть вложения с фронта (has_files), не пакуем:
второй вызов /web/upload_files с тем же message_id перезаписал бы маркеры
первого. Любая ошибка — fail-open: сообщение уходит как было.
"""

import json
import logging
import re

log = logging.getLogger(__name__)

PACK_MIN_CHARS = 1000
MAX_BLOCKS = 5
UPLOAD_TIMEOUT = 30.0

_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n?[ \t]*```", re.S | re.I)
_SAFE_KIND = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")


def _file_name(obj, used: set) -> str:
    """image_edit_request.json по полю kind, иначе data.json; без дублей."""
    base = "data"
    if isinstance(obj, dict):
        kind = obj.get("kind") or obj.get("type")
        if isinstance(kind, str) and _SAFE_KIND.match(kind):
            base = kind
    name, n = base + ".json", 2
    while name in used:
        name = f"{base}_{n}.json"
        n += 1
    used.add(name)
    return name


def extract(text: str) -> tuple[str, list[dict]]:
    """Вырезает длинные ```json блоки. → (новый текст, [{name, content: bytes}])."""
    if not text or "```" not in text:
        return text, []
    items: list[dict] = []
    used: set = set()

    def _cut(m: re.Match) -> str:
        body = m.group(1)
        if len(items) >= MAX_BLOCKS or len(body) <= PACK_MIN_CHARS:
            return m.group(0)
        try:
            obj = json.loads(body)
        except ValueError:
            return m.group(0)
        items.append({"name": _file_name(obj, used), "content": body.encode("utf-8")})
        return ""

    new_text = _FENCE.sub(_cut, text)
    if not items:
        return text, []
    new_text = re.sub(r"\n{3,}", "\n\n", new_text).strip()
    return new_text, items


async def pack_message(http_client, agent_url: str, data: dict, user_id) -> dict | None:
    """Пакует JSON из data["message"] на месте. → {"message", "files"} или None.

    data — тело /api/chats/send_message; при успехе в нём меняются message,
    has_files и files. Возвращённый dict gateway отдаёт фронту (packed),
    чтобы тот перерисовал своё оптимистичное сообщение.
    """
    try:
        text = data.get("message") or ""
        chat_id, message_id = data.get("chat_id"), data.get("message_id")
        if not text or not chat_id or not message_id or data.get("has_files"):
            return None
        new_text, items = extract(text)
        if not items:
            return None
        files_payload = [
            ("files", (it["name"], it["content"], "application/json")) for it in items
        ]
        form = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "user_id": str(user_id),
            "file_modes": ["file"] * len(items),
        }
        resp = await http_client.post(
            f"{agent_url}/web/upload_files", data=form, files=files_payload,
            timeout=UPLOAD_TIMEOUT,
        )
        if resp.status_code != 200:
            log.warning("[json_pack] upload_files %s chat=%s msg=%s: %s",
                        resp.status_code, chat_id, message_id, resp.text[:200])
            return None
        files = [
            {"name": it["name"], "ext": "json", "size": len(it["content"]),
             "type": "application/json"}
            for it in items
        ]
        data["message"] = new_text
        data["has_files"] = True
        data["files"] = files
        log.info("[json_pack] chat=%s msg=%s: %d блок(ов) JSON → %s, текст %d → %d симв",
                 chat_id, message_id, len(items), [f["name"] for f in files],
                 len(text), len(new_text))
        return {"message": new_text, "files": files}
    except Exception as e:  # fail-open
        log.warning("[json_pack] пропуск упаковки: %s", e)
        return None
