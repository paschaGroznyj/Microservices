"""
api_gateway/json_pack.py
────────────────────────
Длинный JSON из текста сообщения → файл-вложение.

Pro-разметка (media-pro.js) шлёт правки обычным текстом: описание + блок
```json ...```. Блок на несколько тысяч символов не нужен ни в Kafka, ни в
контексте агента. Gateway вырезает такой блок из текста ДО отправки в
chats_service и отдаёт его в agent_service тем же путём, что и обычные
вложения (/web/upload_files, режим "file"):

  * agent_service кладёт байты только в медиа-хранилище чата
    (media_store.put_media → media:chat:{chat_id}:blob:{mid} + :index).
    На диск и в OBS не пишем (persist=0): файл нужен агенту, а не ленте;
  * в промпт агента попадает только маркер 📄[DOCUMENT] с media_id;
  * в Kafka и в БД (chat_files) уходит короткий текст + files=[{name, size,
    type, kind="agent_json"}]. По kind фронт рисует плитку fa-tile без ссылки
    на файл, в том числе после F5.

Пакуем только блоки ```json, которые длиннее PACK_MIN_CHARS и парсятся как
JSON. Если у сообщения уже есть вложения с фронта (has_files), не пакуем:
второй вызов /web/upload_files с тем же message_id перезаписал бы маркеры
первого. Любая ошибка — fail-open: сообщение уходит как было.

Вставленный текст «файлом» (Sigma, кнопка .fa-chip__tofile на чипе вставки).
В Sigma загрузка файлов от пользователя закрыта, а поле ввода ограничено
20 000 символов. Поэтому фронт шлёт такой текст отдельным полем
pasted_files=[{name, text}], а gateway сам заворачивает его в .txt и кладёт
в медиа-хранилище тем же вызовом /web/upload_files, что и JSON (один вызов
на сообщение). Режим берём из file_parsers.classify(force_file=True), так что
текст не попадёт в промпт, агент получит документ по media_id. В ленте —
плитка kind="pasted_text" без ссылки. Если загрузка не удалась, текст
дописывается в сообщение как есть, чтобы он не потерялся.
"""

import json
import logging
import re

import file_parsers

log = logging.getLogger(__name__)

PACK_MIN_CHARS = 1000
MAX_BLOCKS = 5
UPLOAD_TIMEOUT = 30.0
# chat_files.file_kind упакованного JSON: фронт не ходит за ним в /files/.
PACKED_KIND = "agent_json"

# Вставленный текст «файлом» (pasted_files).
PASTED_KIND = "pasted_text"
MAX_PASTED_FILES = 5
PASTED_MAX_CHARS = 2_000_000
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')

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


def _pasted_name(raw, used: set) -> str:
    """Безопасное имя .txt без путей и дублей."""
    base = _BAD_NAME.sub(" ", str(raw or "")).strip()
    base = re.sub(r"\s+", " ", base)
    if base.lower().endswith(".txt"):
        base = base[:-4].strip()
    base = base[:60].strip(" .") or "text"
    name, n = base + ".txt", 2
    while name.lower() in used:
        name = f"{base}_{n}.txt"
        n += 1
    used.add(name.lower())
    return name


def take_pasted(data: dict) -> list[dict]:
    """Забирает data["pasted_files"] → [{name, content: bytes, text}].

    Поле удаляется из data в любом случае: в chats_service и Kafka оно не
    уходит. Пустые и нестроковые элементы пропускаются, длина каждого текста
    режется до PASTED_MAX_CHARS, элементов не больше MAX_PASTED_FILES.
    """
    raw = data.pop("pasted_files", None)
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    used: set = set()
    for el in raw[:MAX_PASTED_FILES]:
        if not isinstance(el, dict):
            continue
        text = el.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        text = text[:PASTED_MAX_CHARS]
        content = text.encode("utf-8")
        if len(content) > file_parsers.MAX_UPLOAD_BYTES:
            continue
        items.append({"name": _pasted_name(el.get("name"), used), "content": content, "text": text})
    return items


def _inline_fallback(text: str, pasted: list[dict]) -> str:
    """Загрузка не удалась — возвращаем вставленный текст в сообщение."""
    parts = [text] if text else []
    for it in pasted:
        parts.append(f"[{it['name']}]\n{it['text']}")
    return "\n\n".join(parts)


async def pack_message(http_client, agent_url: str, data: dict, user_id) -> dict | None:
    """Пакует JSON из data["message"] и data["pasted_files"]. → {"message", "files"} или None.

    data — тело /api/chats/send_message; при успехе в нём меняются message,
    has_files и files. Возвращённый dict gateway отдаёт фронту (packed),
    чтобы тот перерисовал своё оптимистичное сообщение.
    """
    pasted = take_pasted(data)
    text = data.get("message") or ""
    try:
        chat_id, message_id = data.get("chat_id"), data.get("message_id")
        if not chat_id or not message_id or data.get("has_files"):
            if pasted:
                data["message"] = _inline_fallback(text, pasted)
                log.warning("[json_pack] pasted_files без chat_id/message_id или с has_files: "
                            "текст оставлен в сообщении")
                return {"message": data["message"], "files": []}
            return None
        new_text, items = extract(text)
        if not items and not pasted:
            return None
        files_payload, modes, files = [], [], []
        for it in items:
            files_payload.append(("files", (it["name"], it["content"], "application/json")))
            modes.append("file")
            files.append({"name": it["name"], "ext": "json", "size": len(it["content"]),
                          "type": "application/json", "kind": PACKED_KIND})
        for it in pasted:
            mode, _chars = file_parsers.classify(it["name"], it["content"], force_file=True)
            files_payload.append(("files", (it["name"], it["content"], "text/plain")))
            modes.append(mode)
            files.append({"name": it["name"], "ext": "txt", "size": len(it["content"]),
                          "type": "text/plain", "kind": PASTED_KIND})
        form = {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "user_id": str(user_id),
            "file_modes": modes,
            "persist": "0",
        }
        resp = await http_client.post(
            f"{agent_url}/web/upload_files", data=form, files=files_payload,
            timeout=UPLOAD_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"upload_files {resp.status_code}: {resp.text[:200]}")
        data["message"] = new_text
        data["has_files"] = True
        data["files"] = files
        log.info("[json_pack] chat=%s msg=%s: %d JSON + %d вставк(и) → %s, текст %d → %d симв",
                 chat_id, message_id, len(items), len(pasted), [f["name"] for f in files],
                 len(text), len(new_text))
        return {"message": new_text, "files": files}
    except Exception as e:  # fail-open
        log.warning("[json_pack] пропуск упаковки: %s", e)
        if pasted:
            data["message"] = _inline_fallback(text, pasted)
            return {"message": data["message"], "files": []}
        return None
