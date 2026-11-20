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
    assert form == {"chat_id": "42", "message_id": "msg-1", "user_id": "56", "file_modes": ["file"],
                    "persist": "0"}
    name, content, mime = http.calls[0]["files"][0][1]
    assert name == "video_edit_request.json" and mime == "application/json"
    assert data["has_files"] is True and "```" not in data["message"]
    assert data["files"] == [{"name": name, "ext": "json", "size": len(content),
                              "type": "application/json", "kind": "agent_json"}]
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


# ── pasted_files: текст «файлом» из Sigma ────────────────────────────────

def test_pasted_files_upload_as_txt_file_mode():
    http = FakeHttp()
    long_text = "строка\n" * 5000  # 35 000 симв, больше лимита поля Sigma
    data = {"chat_id": "7", "message_id": "m-1", "message": "разбери лог",
            "has_files": False, "files": [],
            "pasted_files": [{"name": "../../etc/passwd", "text": long_text},
                             {"name": "лог.txt", "text": "коротко"}]}
    packed = run(json_pack.pack_message(http, "http://agent", data, 56))
    assert "pasted_files" not in data
    form = http.calls[0]["data"]
    assert form["file_modes"] == ["file", "file"] and form["persist"] == "0"
    (n1, c1, m1), (n2, c2, m2) = [f[1] for f in http.calls[0]["files"]]
    assert n1 == "etc passwd.txt" and "/" not in n1
    assert n2 == "лог.txt" and m1 == m2 == "text/plain"
    assert c1.decode("utf-8") == long_text
    assert data["message"] == "разбери лог" and data["has_files"] is True
    assert [f["kind"] for f in data["files"]] == ["pasted_text", "pasted_text"]
    assert data["files"][0]["size"] == len(c1)
    assert packed == {"message": "разбери лог", "files": data["files"]}


def test_pasted_files_with_json_block_single_upload():
    http = FakeHttp()
    data = {"chat_id": "7", "message_id": "m-2", "message": msg(big()),
            "pasted_files": [{"name": "a", "text": "x" * 50}]}
    run(json_pack.pack_message(http, "http://agent", data, 1))
    assert len(http.calls) == 1
    names = [f[1][0] for f in http.calls[0]["files"]]
    assert names == ["image_edit_request.json", "a.txt"]
    assert [f["kind"] for f in data["files"]] == ["agent_json", "pasted_text"]


def test_pasted_files_dedup_limits_and_junk():
    items = json_pack.take_pasted({"pasted_files": [
        {"name": "t", "text": "1"}, {"name": "t.txt", "text": "2"}, {"name": "", "text": "3"},
        {"name": "x", "text": "   "}, {"name": "y", "text": 5}, "мусор",
        {"name": "t", "text": "4"}, {"name": "t", "text": "5"},
    ]})
    # MAX_PASTED_FILES=5 считается по сырым элементам, мусор отсеивается.
    assert [i["name"] for i in items] == ["t.txt", "t_2.txt", "text.txt"]
    assert json_pack.take_pasted({"pasted_files": "не список"}) == []
    big_text = "я" * (json_pack.PASTED_MAX_CHARS + 10)
    [it] = json_pack.take_pasted({"pasted_files": [{"name": "b", "text": big_text}]})
    assert len(it["text"]) == json_pack.PASTED_MAX_CHARS


def test_pasted_files_fail_open_inlines_text():
    for http in (FakeHttp(status=500), FakeHttp(exc=RuntimeError("down"))):
        data = {"chat_id": "7", "message_id": "m", "message": "вопрос",
                "pasted_files": [{"name": "лог", "text": "СТРОКИ"}]}
        packed = run(json_pack.pack_message(http, "http://agent", data, 1))
        assert data["message"] == "вопрос\n\n[лог.txt]\nСТРОКИ"
        assert "has_files" not in data and "pasted_files" not in data
        assert packed == {"message": data["message"], "files": []}


def test_pasted_files_only_text_empty_message():
    http = FakeHttp()
    data = {"chat_id": "7", "message_id": "m", "message": "",
            "pasted_files": [{"name": "n", "text": "abc"}]}
    packed = run(json_pack.pack_message(http, "http://agent", data, 1))
    assert packed["message"] == "" and packed["files"][0]["name"] == "n.txt"
