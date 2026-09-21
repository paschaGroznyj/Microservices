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
import asyncio
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

        # ── SSE-поддержка (push вместо polling) ──────────────────────────
        # chat_id -> asyncio.Event: взводится в mark_done(), чтобы SSE-стрим
        # проснулся ровно в момент готовности ответа, без опроса store в цикле.
        self._chat_ready: dict[int, asyncio.Event] = {}
        # username -> asyncio.Event: взводится при push_user_event(), чтобы
        # SSE user-events стрим просыпался при появлении новых событий.
        self._user_ready: dict[str, asyncio.Event] = {}
        # event loop гейтвея — нужен, чтобы взводить Event'ы из потока
        # Kafka-consumer (он крутится в отдельном треде). Проставляется
        # из lifespan через bind_loop().
        self._loop: asyncio.AbstractEventLoop | None = None

        # ── ОБЩИЙ ЧАТ (group-chat, SSE-broadcast) ────────────────────
        # Монотонный счётчик событий комнаты + кольцевой буфер.
        # Консьмер кладёт событие через push_group_event(); каждый SSE-
        # стрим держит свой cursor и забирает события с него.
        # room_uid -> list[(seq:int, event:dict)]
        self._group_events: dict[str, list] = {}
        self._group_seq: int = 0
        # room_uid -> asyncio.Event (взводится при новом событии)
        self._group_ready: dict[str, asyncio.Event] = {}

    # ── Привязка event loop и SSE-события ────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Запомнить основной event loop гейтвея. Вызывается из lifespan.
        Нужно, чтобы взводить asyncio.Event из потока Kafka-consumer."""
        self._loop = loop

    def _get_chat_event(self, chat_id: str) -> asyncio.Event:
        with self._lock:
            ev = self._chat_ready.get(str(chat_id))
            if ev is None:
                ev = asyncio.Event()
                self._chat_ready[str(chat_id)] = ev
            return ev

    def _get_user_event(self, username: str) -> asyncio.Event:
        with self._lock:
            ev = self._user_ready.get(username)
            if ev is None:
                ev = asyncio.Event()
                self._user_ready[username] = ev
            return ev

    def _signal(self, ev: asyncio.Event) -> None:
        """Потокобезопасно взвести Event. Вызывается и из async-кода
        гейтвея, и из потока Kafka-consumer (через loop.call_soon_threadsafe)."""
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(ev.set)
        else:
            # loop ещё не привязан (тесты/старт) — ставим напрямую
            try:
                ev.set()
            except RuntimeError:
                pass

    async def wait_chat_ready(self, chat_id: str, timeout: float) -> bool:
        """Спит до тех пор, пока mark_done не взведёт событие чата, либо
        до таймаута. Возвращает True, если событие пришло; False по таймауту.
        Использует SSE-эндпоинт вместо цикла опроса get_chat_state()."""
        ev = self._get_chat_event(chat_id)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            ev.clear()

    async def wait_user_ready(self, username: str, timeout: float) -> bool:
        """Спит до появления нового пользовательского события либо таймаута."""
        ev = self._get_user_event(username)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            ev.clear()

    # ── Состояние ответа бота ────────────────────────────────────────────

    def mark_running(self, chat_id: str, request_id: str | None = None) -> None:
        """Пользователь отправил сообщение — ждём ответ бота."""
        with self._lock:
            self._chats[str(chat_id)] = {
                "status": "running",
                "message": None,
                "request_id": request_id,
                "ts": time.time(),
            }
        logger.info(f"[sigma-store] chat {chat_id} -> running (req={request_id})")

    def mark_done(self, chat_id: str, message: dict) -> None:
        """Kafka-consumer получил финальный bot_message — ответ готов."""
        with self._lock:
            prev = self._chats.get(str(chat_id)) or {}
            self._chats[str(chat_id)] = {
                "status": "done",
                "message": message,
                "request_id": prev.get("request_id"),
                "ts": time.time(),
            }
        # Разбудить SSE-стрим, ждущий этот чат (push вместо polling).
        self._signal(self._get_chat_event(chat_id))
        logger.info(f"[sigma-store] chat {chat_id} -> done")

    def get_chat_request_id(self, chat_id: str) -> str | None:
        """request_id текущего running-хода чата — нужен SSE-стриму,
        чтобы подписаться на Redis-канал stream:{request_id} с чанками."""
        with self._lock:
            st = self._chats.get(str(chat_id))
            return (st or {}).get("request_id")

    def get_chat_state(self, chat_id: str) -> dict:
        """Вернуть текущее состояние для polling. Если done — отдаём один раз
        и сбрасываем в idle, чтобы повторный опрос не дублировал сообщение."""
        self._gc()
        with self._lock:
            st = self._chats.get(str(chat_id))
            if not st:
                return {"status": "idle"}
            if st["status"] == "done":
                # Отдаём финал и очищаем — фронт после done перечитает историю.
                self._chats.pop(str(chat_id), None)
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
        self._signal(self._get_user_event(username))

    def broadcast_user_event(self, event: dict) -> None:
        """Разослать событие всем известным пользователям (напр. новый отзыв)."""
        with self._lock:
            targets = list(self._known_users) or list(self._user_events.keys())
            for username in targets:
                q = self._user_events.setdefault(username, [])
                q.append({"event": event, "ts": time.time()})
                if len(q) > _MAX_USER_EVENTS:
                    del q[: len(q) - _MAX_USER_EVENTS]
        for username in targets:
            self._signal(self._get_user_event(username))

    def pop_user_events(self, username: str) -> list:
        """Забрать и очистить накопленные события пользователя."""
        with self._lock:
            self._known_users.add(username)
            q = self._user_events.pop(username, [])
        return [item["event"] for item in q]

    # ── ОБЩИЙ ЧАТ (group-chat) ────────────────────────────────
    _MAX_GROUP_EVENTS = 300

    def _get_group_event(self, room_uid: str) -> asyncio.Event:
        with self._lock:
            ev = self._group_ready.get(room_uid)
            if ev is None:
                ev = asyncio.Event()
                self._group_ready[room_uid] = ev
            return ev

    def group_cursor(self, room_uid: str) -> int:
        """Текущий seq комнаты — новый SSE-клиент начинает с него,
        чтобы не получать старые события (история тянется через REST)."""
        with self._lock:
            return self._group_seq

    def push_group_event(self, room_uid: str, event: dict) -> None:
        """Консьмер кладёт событие общего чата (new/edit/delete/pin).
        Взводит Event комнаты — все SSE-стримы этой комнаты проснутся."""
        with self._lock:
            self._group_seq += 1
            seq = self._group_seq
            q = self._group_events.setdefault(room_uid, [])
            q.append((seq, event))
            if len(q) > self._MAX_GROUP_EVENTS:
                del q[: len(q) - self._MAX_GROUP_EVENTS]
        self._signal(self._get_group_event(room_uid))

    def pop_group_events(self, room_uid: str, after_seq: int):
        """Вернуть события комнаты с seq > after_seq и новый курсор."""
        with self._lock:
            q = self._group_events.get(room_uid, [])
            fresh = [(seq, ev) for (seq, ev) in q if seq > after_seq]
        if not fresh:
            return [], after_seq
        new_cursor = fresh[-1][0]
        return [ev for (_seq, ev) in fresh], new_cursor

    async def wait_group_ready(self, room_uid: str, timeout: float) -> bool:
        """Спит до появления нового события в комнате либо таймаута."""
        ev = self._get_group_event(room_uid)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            ev.clear()

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
                # осиротевший Event тоже убираем, чтобы словарь не тёк
                self._chat_ready.pop(cid, None)


# Синглтон на процесс
status_store = SigmaStatusStore()
