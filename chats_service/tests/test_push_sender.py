"""Web Push (app/push_sender.py): текст уведомления, когда слать, очередь отправки,
настоящее шифрование pywebpush (aes128gcm + VAPID) с расшифровкой на стороне «браузера»."""
import asyncio
import base64
import json
import os

import pytest

from app import push_sender as ps


def run(c):
    return asyncio.run(c)


class FakeRedis:
    def __init__(self, keys=()):
        self.kv = {k: "1" for k in keys}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        return True

    async def exists(self, k):
        return int(k in self.kv)


class FakePushDB:
    def __init__(self, owner=56, enabled=True, subs=None, title="Web Push"):
        self.owner, self.enabled, self.title = owner, enabled, title
        self.subs = subs if subs is not None else [{"endpoint": "https://fcm.googleapis.com/fcm/send/x", "p256dh": "k", "auth": "a"}]
        self.deleted, self.asked_gw = [], None

    async def chat_owner(self, chat_id):
        return self.owner

    async def notify_enabled(self, uid):
        return self.enabled

    async def list_subscriptions(self, uid, gw):
        self.asked_gw = gw
        return list(self.subs)

    async def chat_title(self, chat_id, uid):
        return self.title

    async def delete_subscription(self, ep, user_id=None):
        self.deleted.append(ep)
        return 1


def bot(**kw):
    p = {"is_from_bot": True, "chat_id": "425826", "message": "## Готово\nИзменил **два** файла: `push.js` и [sw.js](http://x)",
         "gateway_target": "main", "user_id": -1}
    p.update(kw)
    return p


# ---------- текст ----------

def test_plain_preview_strips_markdown_and_code():
    t = ps.plain_preview("## Итог\n- **пункт** один\n```json\n{\"component\":\"Chart\"}\n```\nСм. [доку](http://a) и `cmd`")
    assert t == "Итог пункт один См. доку и cmd"


def test_plain_preview_keeps_snake_case_and_cuts():
    assert ps.plain_preview("push_sender.py готов") == "push_sender.py готов"
    long = ps.plain_preview("а" * 300)
    assert len(long) == ps.BODY_LIMIT and long.endswith("…")


def test_build_payload():
    p = ps.build_payload("c1", "Пуши", "Ответ")
    assert p == {"title": "AuGent ответил: Пуши", "body": "Ответ", "chat_id": "c1", "tag": "chat-c1", "url": "/?push_chat=c1"}
    assert ps.build_payload("c1", None, "```x```")["title"] == "AuGent ответил"
    assert ps.build_payload("c1", None, "```x```")["body"] == "Ответ готов"


@pytest.mark.parametrize("kw,ok", [
    ({}, True),
    ({"gateway_target": "sigma"}, True),
    ({"gateway_target": "telegram"}, False),
    ({"gateway_target": "max"}, False),
    ({"is_from_bot": False}, False),
    ({"action": "stopped"}, False),
    ({"message": "   "}, False),
    ({"chunk": "x"}, False),
])
def test_should_push(kw, ok):
    assert ps.should_push(bot(**kw)) is ok


# ---------- решение и очередь ----------

def make_sender(sent, fail_status=None):
    def send(sub, data, vapid):
        if fail_status:
            class R:
                status_code = fail_status
            e = Exception("push rejected")
            e.response = R()
            raise e
        sent.append((sub["endpoint"], json.loads(data)))
    return ps.PushSender(send_fn=send)


async def vapid_stub():
    return object()


def test_sends_when_tab_not_visible():
    sent = []
    s, db = make_sender(sent), FakePushDB()
    n = run(ps.notify_bot_reply(bot(), db, FakeRedis(), s, vapid_stub))
    s.join()
    assert n == 1 and db.asked_gw == "main"
    ep, payload = sent[0]
    assert payload["title"] == "AuGent ответил: Web Push"
    assert payload["body"] == "Готово Изменил два файла: push.js и sw.js"


@pytest.mark.parametrize("db,redis", [
    (FakePushDB(enabled=False), FakeRedis()),
    (FakePushDB(owner=None), FakeRedis()),
    (FakePushDB(subs=[]), FakeRedis()),
    (FakePushDB(), FakeRedis(keys=["viewing:56:425826"])),
])
def test_skips(db, redis):
    sent = []
    s = make_sender(sent)
    assert run(ps.notify_bot_reply(bot(), db, redis, s, vapid_stub)) == 0
    s.join()
    assert sent == []


def test_viewing_other_chat_still_sends():
    sent = []
    s = make_sender(sent)
    assert run(ps.notify_bot_reply(bot(), FakePushDB(), FakeRedis(keys=["viewing:56:other"]), s, vapid_stub)) == 1


def test_sigma_asks_sigma_subscriptions():
    db = FakePushDB()
    run(ps.notify_bot_reply(bot(gateway_target="sigma"), db, FakeRedis(), make_sender([]), vapid_stub))
    assert db.asked_gw == "sigma"


@pytest.mark.parametrize("status,dead", [(410, True), (404, True), (403, True), (500, False)])
def test_rejected_subscription_removed_on_next_call(status, dead):
    s, db = make_sender([], fail_status=status), FakePushDB()
    run(ps.notify_bot_reply(bot(), db, FakeRedis(), s, vapid_stub))
    s.join()
    run(ps.notify_bot_reply(bot(is_from_bot=False), db, FakeRedis(), s, vapid_stub))
    assert db.deleted == (["https://fcm.googleapis.com/fcm/send/x"] if dead else [])


def test_no_vapid_no_send():
    async def none():
        return None
    assert run(ps.notify_bot_reply(bot(), FakePushDB(), FakeRedis(), make_sender([]), none)) == 0


# ---------- VAPID ----------

def test_load_vapid_generates_once_in_redis(monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    r = FakeRedis()
    k1 = ps.public_key_b64(run(ps.load_vapid(r)))
    k2 = ps.public_key_b64(run(ps.load_vapid(r)))
    assert k1 == k2 and len(base64.urlsafe_b64decode(k1 + "=")) == 65
    assert "BEGIN" in json.loads(r.kv[ps.VAPID_REDIS_KEY])["private_pem"]


def test_load_vapid_from_env_pem(monkeypatch):
    from py_vapid import Vapid
    v = Vapid()
    v.generate_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", v.private_pem().decode().replace("\n", "\\n"))
    assert ps.public_key_b64(run(ps.load_vapid(None))) == ps.public_key_b64(v)


# ---------- настоящее шифрование ----------

def test_real_webpush_roundtrip(monkeypatch):
    """pywebpush шифрует под ключи «браузера» и подписывает VAPID; расшифровываем как браузер."""
    import http_ece
    import requests
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import Vapid

    ua_priv = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "p256dh": b64(ua_pub), "auth": b64(auth)}

    got = {}

    class Resp:
        status_code = 201
        text = ""
        headers = {}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        got.update(url=url, data=data, headers=headers)
        return Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    vapid = Vapid()
    vapid.generate_keys()
    payload = ps.build_payload("c1", "Тест", "Привет, **Павел**")
    s = ps.PushSender()
    s.enqueue([sub], payload, vapid)
    s.join()
    assert s.sent == 1, s.failed
    h = {k.lower(): v for k, v in got["headers"].items()}
    assert got["url"] == sub["endpoint"]
    assert h["content-encoding"] == "aes128gcm" and h["urgency"] == "high" and h["ttl"] == str(ps.PUSH_TTL)
    assert h["authorization"].startswith("vapid t=") and ("k=" + ps.public_key_b64(vapid)) in h["authorization"]
    plain = http_ece.decrypt(got["data"], private_key=ua_priv, auth_secret=auth, version="aes128gcm")
    assert json.loads(plain) == payload
