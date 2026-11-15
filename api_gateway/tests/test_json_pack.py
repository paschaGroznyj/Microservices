"""json_pack.py: длинный ```json из текста → вложение через /web/upload_files."""
import asyncio
import json

import json_pack


def run(coro):
    return asyncio.run(coro)


class Resp:
    def __init__(self, status=200, text="{}"):
        self.status_code, self.text = status, text


class FakeHttp:
    def __init__(self, status=200, exc=None):
        self.calls, self.status, self.exc = [], status, exc

    async def post(self, url, data=None, files=None, timeout=None):
        if self.exc:
            raise self.exc
        self.calls.append({"url": url, "data": data, "files": files})
        return Resp(self.status)


def big(kind="image_edit_request", n=60):
    return json.dumps({"kind": kind, "version": 1,
                       "edits": [{"op": "crop", "i": i, "note": "x" * 10} for i in range(n)]},
                      ensure_ascii=False, indent=2)


def msg(body):
    return "Правки к фото «a.jpg»\n1. Кадр 4:3\n\nПолная разметка — в JSON (image_edit_request).\n\n```json\n" + body + "\n```"


def test_extract_cuts_long_json_and_names_by_kind():
    body = big()
    text, items = json_pack.extract(msg(body))
    assert "```" not in text
    assert text.endswith("(image_edit_request).")
    assert [i["name"] for i in items] == ["image_edit_request.json"]
    assert json.loads(items[0]["content"].decode()) == json.loads(body)


def test_extract_keeps_short_and_invalid_json():
    short = '{"kind": "image_edit_request", "edits": []}'
    t1, i1 = json_pack.extract(msg(short))
    assert i1 == [] and t1 == msg(short)
    broken = "{" + "x" * 1500
    t2, i2 = json_pack.extract(msg(broken))
    assert i2 == [] and t2 == msg(broken)


def test_extract_dedups_names_and_falls_back_to_data():
    text = "a\n```json\n" + big() + "\n```\nb\n```json\n" + big() + "\n```\n```json\n" + json.dumps(list(range(400))) + "\n```"
    _, items = json_pack.extract(text)
    assert [i["name"] for i in items] == ["image_edit_request.json", "image_edit_request_2.json", "data.json"]


def test_pack_message_uploads_and_rewrites_data():
    http = FakeHttp()
    data = {"chat_id": "42", "message_id": "msg-1", "message": msg(big("video_edit_request")), "has_files": False, "files": []}
    packed = run(json_pack.pack_message(http, "http://agent", data, 56))
    assert http.calls[0]["url"] == "http://agent/web/upload_files"
    form = http.calls[0]["data"]
    assert form == {"chat_id": "42", "message_id": "msg-1", "user_id": "56", "file_modes": ["file"]}
    name, content, mime = http.calls[0]["files"][0][1]
    assert name == "video_edit_request.json" and mime == "application/json"
    assert data["has_files"] is True and "```" not in data["message"]
    assert data["files"] == [{"name": name, "ext": "json", "size": len(content), "type": "application/json"}]
    assert packed == {"message": data["message"], "files": data["files"]}


def test_pack_message_fail_open():
    for http in (FakeHttp(status=500), FakeHttp(exc=RuntimeError("down"))):
        original = msg(big())
        data = {"chat_id": "42", "message_id": "m", "message": original}
        assert run(json_pack.pack_message(http, "http://agent", data, 1)) is None
        assert data["message"] == original and "has_files" not in data


def test_pack_message_skips_when_front_already_has_files_or_no_ids():
    http = FakeHttp()
    for data in ({"chat_id": "1", "message_id": "m", "message": msg(big()), "has_files": True},
                 {"chat_id": "1", "message": msg(big())},
                 {"chat_id": "1", "message_id": "m", "message": "просто текст"}):
        assert run(json_pack.pack_message(http, "http://agent", data, 1)) is None
    assert http.calls == []
