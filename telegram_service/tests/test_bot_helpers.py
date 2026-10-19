"""Pure/sync helpers of app.bot that need no Redis or network."""
import pytest


def test_tg_username_prefers_username(bot):
    assert bot._tg_username(123, "alice") == "alice"


def test_tg_username_strips_whitespace(bot):
    assert bot._tg_username(123, "  bob  ") == "bob"


@pytest.mark.parametrize("uname", [None, "", "   "])
def test_tg_username_surrogate_when_blank(bot, uname):
    assert bot._tg_username(777, uname) == "tg_777"


def test_redis_key_builders(bot):
    assert bot._k_allowed() == "tg:allowed_chats"
    assert bot._k_buf(5) == "tg:buf:5"
    assert bot._k_buf_mark(5) == "tg:buf_mark:5"
    assert bot._k_orch_ts(5) == "tg:orch_ts:5"
    assert bot._k_poke(5) == "tg:poke:5"
    assert bot._k_await(5, 9) == "tg:await:5:9"
    assert bot._k_session(5) == "tg:session:5"
    assert bot._k_sessions_reg(5) == "tg:sessions:5"
    assert bot._k_post(5, "m1") == "tg:post:5:m1"
    assert bot._k_fwdgroup(5, "g") == "tg:fwdgroup:5:g"


def test_session_title_default_when_empty(bot):
    assert bot._session_title({"id": 42}) == "Сессия 42"


def test_session_title_collapses_whitespace(bot):
    assert bot._session_title({"title": "  hello   world  "}) == "hello world"


def test_session_title_truncates_long(bot):
    long = "x" * 50
    out = bot._session_title({"title": long})
    assert out.endswith("…")
    assert len(out) == 38


class _Origin:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Msg:
    def __init__(self, origin):
        self.forward_origin = origin


def test_fwd_origin_author_user(bot):
    su = _Origin(full_name="John Doe")
    msg = _Msg(_Origin(type="user", sender_user=su))
    assert bot._fwd_origin_author(msg) == "John Doe"


def test_fwd_origin_author_hidden_user(bot):
    msg = _Msg(_Origin(type="hidden_user", sender_user_name="Anon"))
    assert bot._fwd_origin_author(msg) == "Anon"


def test_fwd_origin_author_channel(bot):
    msg = _Msg(_Origin(type="channel", chat=_Origin(title="My Channel")))
    assert bot._fwd_origin_author(msg) == "My Channel"


def test_fwd_origin_author_fallback_on_error(bot):
    msg = _Msg(_Origin(type="user"))  # no sender_user -> AttributeError -> fallback
    assert bot._fwd_origin_author(msg) == "источник"
