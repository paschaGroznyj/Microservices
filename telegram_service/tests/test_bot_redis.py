"""Redis-backed async helpers with a mocked aioredis client.

No pytest-asyncio in the sandbox; coroutines are driven via asyncio.run().
"""
import asyncio
import json


def run(coro):
    return asyncio.run(coro)


def test_buffer_push_pipeline(bot, fake_redis):
    run(bot.buffer_push(10, "alice", "hello\nworld"))
    pipe = fake_redis.pipeline.return_value
    pipe.rpush.assert_called_once()
    args = pipe.rpush.call_args[0]
    assert args[0] == "tg:buf:10"
    assert args[1] == "alice: hello world"  # newline collapsed
    pipe.execute.assert_awaited_once()


def test_buffer_lines_returns_list(bot, fake_redis):
    fake_redis.lrange.return_value = ["a: 1", "b: 2"]
    out = run(bot.buffer_lines(10))
    assert out == ["a: 1", "b: 2"]
    # NB: current impl calls lrange twice (debug read + return); assert it ran.
    fake_redis.lrange.assert_awaited()
    assert fake_redis.lrange.call_args[0][0] == "tg:buf:10"


def test_fwd_group_already_seen_first_is_false(bot, fake_redis):
    # SET NX returns truthy on first set -> not seen before
    fake_redis.set.return_value = True
    assert run(bot.fwd_group_already_seen(1, "gid")) is False


def test_fwd_group_already_seen_dup_is_true(bot, fake_redis):
    # SET NX returns None when key exists -> already seen
    fake_redis.set.return_value = None
    assert run(bot.fwd_group_already_seen(1, "gid")) is True


def test_fwd_group_no_group_id_is_false(bot, fake_redis):
    assert run(bot.fwd_group_already_seen(1, None)) is False
    fake_redis.set.assert_not_called()


class _Msg:
    def __init__(self, media_group_id=None, message_id=1):
        self.media_group_id = media_group_id
        self.message_id = message_id


def test_post_already_dispatched_uses_media_group(bot, fake_redis):
    fake_redis.set.return_value = None  # existing -> dup
    assert run(bot.post_already_dispatched(5, _Msg(media_group_id="g7"))) is True
    key = fake_redis.set.call_args[0][0]
    assert key == "tg:post:5:g7"


def test_post_already_dispatched_single_message_key(bot, fake_redis):
    fake_redis.set.return_value = True  # first -> not dup
    assert run(bot.post_already_dispatched(5, _Msg(message_id=99))) is False
    key = fake_redis.set.call_args[0][0]
    assert key == "tg:post:5:m99"


def test_pop_forward_returns_and_deletes(bot, fake_redis):
    payload = {"author": "src", "text": "hi", "file_b64": None, "file_name": None}
    fake_redis.get.return_value = json.dumps(payload)
    out = run(bot.pop_forward(3))
    assert out == payload
    fake_redis.delete.assert_awaited_once_with("tg:lastfwd:3")


def test_pop_forward_none_when_empty(bot, fake_redis):
    fake_redis.get.return_value = None
    assert run(bot.pop_forward(3)) is None
    fake_redis.delete.assert_not_called()


def test_stash_tag_writes_payload(bot, fake_redis):
    run(bot.stash_tag(4, 11, "alice", "гоша?"))
    key, val = fake_redis.set.call_args[0][0], fake_redis.set.call_args[0][1]
    assert key == "tg:lasttag:4"
    data = json.loads(val)
    assert data == {"user_id": 11, "username": "alice", "text": "гоша?"}


def test_pop_tag_returns_and_deletes(bot, fake_redis):
    fake_redis.get.return_value = json.dumps({"user_id": 1, "username": "u", "text": "t"})
    out = run(bot.pop_tag(4))
    assert out["username"] == "u"
    fake_redis.delete.assert_awaited_once_with("tg:lasttag:4")


def test_get_current_session_reads_key(bot, fake_redis):
    fake_redis.get.return_value = "sess-1"
    assert run(bot.get_current_session(55)) == "sess-1"
    fake_redis.get.assert_awaited_once_with("tg:session:55")


def test_list_sessions_sorts_newest_first(bot, fake_redis):
    fake_redis.hgetall.return_value = {
        "s1": json.dumps({"title": "old", "ts": 100}),
        "s2": json.dumps({"title": "new", "ts": 200}),
    }
    out = run(bot.list_sessions(55))
    assert [x["id"] for x in out] == ["s2", "s1"]
