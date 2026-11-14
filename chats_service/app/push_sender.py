"""Web Push: уведомление об ответе агента в веб-чат (main / sigma).

Вызывается из endpoint_consumer после сохранения ответа агента в БД.
Push НЕ отправляется, если:
  - у владельца чата выключено user_settings.notify_enabled;
  - вкладка с этим чатом сейчас видима (Redis viewing:{uid}:{chat_id}, TTL ~35 с,
    ставит гейтвей по heartbeat из push.js);
  - у пользователя нет подписок для этого гейтвея (push_subscriptions.gw).

VAPID-ключ: env VAPID_PRIVATE_KEY (PEM или base64url DER) + VAPID_SUBJECT.
Без env пара генерируется один раз и лежит в Redis webpush:vapid (SET NX — chats_service
и chats-consumer получат одну и ту же пару). Если ключ сменится, старые подписки
перестанут приниматься push-сервером (403) и будут удалены при первой отправке.

Отправка (pywebpush, sync requests) идёт в отдельном потоке: Kafka-цикл
consumer'а синхронный, блокировать его HTTP-запросами к FCM/Mozilla/Apple нельзя.
"""
import base64
import json
import logging
import os
import queue
import re
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)

VAPID_REDIS_KEY = "webpush:vapid"
VAPID_SUBJECT = os.getenv("VAPID_SUBJECT", "mailto:admin@augent.local")
PUSH_TTL = int(os.getenv("WEBPUSH_TTL", "3600"))
PUSH_GATEWAYS = ("main", "sigma")
BODY_LIMIT = 100
# 404/410 — подписка умерла; 401/403 — подписана другим VAPID-ключом, жить ей незачем.
DEAD_STATUSES = {401, 403, 404, 410}


def viewing_key(user_id, chat_id) -> str:
    return f"viewing:{int(user_id)}:{chat_id}"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _vapid_from_private(value: str):
    from py_vapid import Vapid
    s = (value or "").strip().replace("\\n", "\n")
    if "BEGIN" in s:
        return Vapid.from_pem(s.encode())
    return Vapid.from_string(s)


def public_key_b64(vapid) -> str:
    """applicationServerKey для pushManager.subscribe: несжатая точка P-256, base64url."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return _b64u(vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))


async def load_vapid(redis):
    """Vapid из env или из Redis (генерируется при первом обращении). None — ключа нет."""
    env = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    if env:
        return _vapid_from_private(env)
    if redis is None:
        return None
    raw = await redis.get(VAPID_REDIS_KEY)
    if not raw:
        from py_vapid import Vapid
        v = Vapid()
        v.generate_keys()
        doc = {"private_pem": v.private_pem().decode(), "created": int(time.time())}
        await redis.set(VAPID_REDIS_KEY, json.dumps(doc), nx=True)
        raw = await redis.get(VAPID_REDIS_KEY)
        logger.warning("[push] VAPID_PRIVATE_KEY не задан: ключ сгенерирован и сохранён в Redis %s", VAPID_REDIS_KEY)
    if isinstance(raw, bytes):
        raw = raw.decode()
    return _vapid_from_private(json.loads(raw)["private_pem"])


_FENCE = re.compile(r"```[\s\S]*?(?:```|$)")
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CODE = re.compile(r"`([^`]*)`")
_LINE_MARK = re.compile(r"^\s{0,3}(?:#{1,6}|>|[-*+]|\d+\.)\s+", re.M)
_EMPH = re.compile(r"\*\*|__|~~|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)")
_TABLE = re.compile(r"^\s*\|?\s*:?-{3,}.*$", re.M)


def plain_preview(text: str, limit: int = BODY_LIMIT) -> str:
    """Первые ~limit символов ответа без markdown (код-блоки и GenUI JSON выкидываются)."""
    t = _FENCE.sub(" ", str(text or ""))
    t = _IMG.sub(" ", t)
    t = _LINK.sub(r"\1", t)
    t = _CODE.sub(r"\1", t)
    t = _TABLE.sub(" ", t)
    t = _LINE_MARK.sub("", t)
    t = _EMPH.sub("", t)
    t = t.replace("|", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t


def build_payload(chat_id, title, message) -> dict:
    title = (title or "").strip()
    return {
        "title": f"AuGent ответил: {title}" if title else "AuGent ответил",
        "body": plain_preview(message) or "Ответ готов",
        "chat_id": str(chat_id),
        "tag": f"chat-{chat_id}",
        "url": f"/?push_chat={chat_id}",
    }


def should_push(payload: dict) -> bool:
    """Только финальный ответ агента в веб-чат. Стоп по кнопке — пользователь и так у экрана."""
    if not payload or not payload.get("is_from_bot") or payload.get("chunk"):
        return False
    if (payload.get("gateway_target") or "main").lower() not in PUSH_GATEWAYS:
        return False
    if payload.get("action") == "stopped":
        return False
    return bool(str(payload.get("message") or "").strip()) and payload.get("chat_id") is not None


class PushSender:
    """Очередь отправки + фоновый поток. dead — endpoint'ы, которые push-сервер отверг
    (их удаляет из БД async-сторона при следующем вызове, у потока нет asyncpg)."""

    def __init__(self, send_fn=None, maxsize: int = 1000):
        self._send_fn = send_fn
        self._q = queue.Queue(maxsize=maxsize)
        self.dead = deque()
        self._thread = None
        self._lock = threading.Lock()
        self.sent = 0
        self.failed = 0

    def _send(self, sub: dict, data: str, vapid) -> None:
        if self._send_fn is not None:
            return self._send_fn(sub, data, vapid)
        from pywebpush import webpush
        webpush(
            subscription_info={"endpoint": sub["endpoint"],
                               "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
            data=data,
            vapid_private_key=vapid,
            vapid_claims={"sub": VAPID_SUBJECT},  # новый dict на каждый вызов: pywebpush пишет туда aud
            ttl=PUSH_TTL,
            headers={"Urgency": "high"},
            timeout=10,
        )

    def _worker(self) -> None:
        while True:
            sub, data, vapid = self._q.get()
            try:
                self._send(sub, data, vapid)
                self.sent += 1
            except Exception as e:
                self.failed += 1
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status in DEAD_STATUSES:
                    self.dead.append(sub["endpoint"])
                    logger.info("[push] подписка отвергнута (%s), удалим: %s…", status, sub["endpoint"][:60])
                else:
                    logger.warning("[push] отправка не удалась (%s): %s", status, e)
            finally:
                self._q.task_done()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._worker, name="webpush", daemon=True)
                self._thread.start()

    def enqueue(self, subs: list, payload: dict, vapid) -> int:
        self._ensure_thread()
        data = json.dumps(payload, ensure_ascii=False)
        n = 0
        for sub in subs:
            try:
                self._q.put_nowait((sub, data, vapid))
                n += 1
            except queue.Full:
                logger.warning("[push] очередь переполнена, уведомление пропущено")
                break
        return n

    def drain_dead(self) -> list:
        out = []
        while self.dead:
            out.append(self.dead.popleft())
        return out

    def join(self, timeout: float = 5.0) -> None:
        """Для тестов: дождаться, пока поток разберёт очередь."""
        end = time.time() + timeout
        while self._q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)


async def notify_bot_reply(payload: dict, push_db, redis, sender: PushSender, vapid_getter) -> int:
    """Решает, слать ли push по ответу агента, и ставит его в очередь. Возвращает число подписок."""
    for ep in sender.drain_dead():
        try:
            await push_db.delete_subscription(ep)
        except Exception as e:
            logger.debug("[push] delete dead failed: %s", e)
    if not should_push(payload):
        return 0
    chat_id = str(payload["chat_id"])
    gw = (payload.get("gateway_target") or "main").lower()
    uid = await push_db.chat_owner(chat_id)
    if not uid or not await push_db.notify_enabled(uid):
        return 0
    if redis is not None:
        try:
            if await redis.exists(viewing_key(uid, chat_id)):
                return 0
        except Exception as e:
            logger.debug("[push] viewing check failed: %s", e)
    subs = await push_db.list_subscriptions(uid, gw)
    if not subs:
        return 0
    vapid = await vapid_getter()
    if vapid is None:
        logger.warning("[push] нет VAPID-ключа, уведомление не отправлено")
        return 0
    title = await push_db.chat_title(chat_id, uid)
    return sender.enqueue(subs, build_payload(chat_id, title, payload.get("message")), vapid)
