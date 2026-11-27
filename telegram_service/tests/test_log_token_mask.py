"""Токен бота не попадает в логи: httpx логирует URL api.telegram.org/bot<id>:<secret>/..."""
import logging

from app.bot import _MaskBotToken

SECRET = "AAEnso3O2OKDMFL6TBt3OGYPIlOw-eU-7YI"


def _record(msg, *args):
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)


def test_url_in_args_masked():
    rec = _record('HTTP Request: %s %s "%s"', "POST",
                  f"https://api.telegram.org/bot8792282981:{SECRET}/getUpdates", "HTTP/1.1 200 OK")
    assert _MaskBotToken().filter(rec) is True
    out = rec.getMessage()
    assert SECRET not in out
    assert "/bot8792282981:***/getUpdates" in out


def test_plain_message_untouched():
    rec = _record("HTTP Request: GET %s", "http://127.0.0.1:8001/tg/allowed")
    _MaskBotToken().filter(rec)
    assert rec.args == ("http://127.0.0.1:8001/tg/allowed",)


def test_root_handlers_have_filter():
    assert any(isinstance(f, _MaskBotToken) for h in logging.getLogger().handlers for f in h.filters)
