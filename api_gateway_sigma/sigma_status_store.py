# sigma_status_store.py
"""
SIGMA store — потокобезопасное in-memory хранилище состояния для GET-polling,
заменяющего WebSocket-канал старого gateway.

Держит две вещи:
  1. Состояние ответа бота по чатам (chat_id -> running/done + готовый message).
     Заполняется: mark_running() из send_message и mark_done() из Kafka-consumer.
  2. Очередь пользовательских событий (username -> список событий): релевантные
     статьи, ошибки парсера, новые отзывы. Забираются и очищаются на polling.

Данные живут только в памяти процесса. Для одного инстанса sigma этого
достаточно (chats_service всё равно пишет историю в БД — при F5 фронт
перечитает /api/chats/{id}/messages). TTL чистит протухшие записи, чтобы
память не текла.
"""
import threading
import time
import logging

logger = logging.getLogger(__name__)

# Сколько держим завершённый ответ в store, прежде чем считать его протухшим.
_DONE_TTL_SEC = 300
# Сколько держим "running" без ответа (страховка от зависших запросов).
_RUNNING_TTL_SEC = 900
# Максимум событий в очереди одного пользователя.
_MAX_USER_EVENTS = 200


class SigmaStatusStore:
    def __init__(self):
        self._lock = threading.Lock()
        # chat_id -> {"status": "running"|"done", "message": dict|None,
        #             "request_id": str|None, "ts": float}
        self._chats: dict[int, dict] = {}
        # username -> list[dict]
        self._user_events: dict[str, list] = {}
        # известные онлайн-пользователи (для broadcast); пополняется при pop
        self._known_users: set[str] = set()

    # ── Состояние ответа бота ────────────────────────────────────────────

    def mark_running(self, chat_id: int, request_id: str | None = None) -> None:
        """Пользователь отправил сообщение — ждём ответ бота."""
        with self._lock:
            self._chats[int(chat_id)] = {
                "status": "running",
                "message": None,
                "request_id": request_id,
                "ts": time.time(),
            }
        logger.info(f"[sigma-store] chat {chat_id} -> running (req={request_id})")

    def mark_done(self, chat_id: int, message: dict) -> None:
        """Kafka-consumer получил финальный bot_message — ответ готов."""
        with self._lock:
            prev = self._chats.get(int(chat_id)) or {}
            self._chats[int(chat_id)] = {
                "status": "done",
                "message": message,
                "request_id": prev.get("request_id"),
                "ts": time.time(),
            }
        logger.info(f"[sigma-store] chat {chat_id} -> done")

    def get_chat_state(self, chat_id: int) -> dict:
        """Вернуть текущее состояние для polling. Если done — отдаём один раз
        и сбрасываем в idle, чтобы повторный опрос не дублировал сообщение."""
        self._gc()
        with self._lock:
            st = self._chats.get(int(chat_id))
            if not st:
                return {"status": "idle"}
            if st["status"] == "done":
                # Отдаём финал и очищаем — фронт после done перечитает историю.
                self._chats.pop(int(chat_id), None)
                return {"status": "done", "message": st["message"]}
            return {"status": st["status"]}

    # ── Пользовательские события (релевантные статьи, ошибки, отзывы) ─────

    def push_user_event(self, username: str, event: dict) -> None:
        with self._lock:
            self._known_users.add(username)
            q = self._user_events.setdefault(username, [])
            q.append({"event": event, "ts": time.time()})
            if len(q) > _MAX_USER_EVENTS:
                del q[: len(q) - _MAX_USER_EVENTS]

    def broadcast_user_event(self, event: dict) -> None:
        """Разослать событие всем известным пользователям (напр. новый отзыв)."""
        with self._lock:
            targets = list(self._known_users) or list(self._user_events.keys())
            for username in targets:
                q = self._user_events.setdefault(username, [])
                q.append({"event": event, "ts": time.time()})
                if len(q) > _MAX_USER_EVENTS:
                    del q[: len(q) - _MAX_USER_EVENTS]

    def pop_user_events(self, username: str) -> list:
        """Забрать и очистить накопленные события пользователя."""
        with self._lock:
            self._known_users.add(username)
            q = self._user_events.pop(username, [])
        return [item["event"] for item in q]

    # ── Уборка ───────────────────────────────────────────────────────────

    def _gc(self) -> None:
        now = time.time()
        with self._lock:
            dead = []
            for cid, st in self._chats.items():
                age = now - st["ts"]
                if st["status"] == "done" and age > _DONE_TTL_SEC:
                    dead.append(cid)
                elif st["status"] == "running" and age > _RUNNING_TTL_SEC:
                    dead.append(cid)
            for cid in dead:
                self._chats.pop(cid, None)


# Синглтон на процесс
status_store = SigmaStatusStore()
