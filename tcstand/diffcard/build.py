"""Стенд карточки «Изменённые файлы»: настоящий diff коммита поверх theme.css/chat.css.
python build.py [commit]  ->  stand.html"""
import json, re, subprocess, sys, pathlib
HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMMIT = sys.argv[1] if len(sys.argv) > 1 else "832a97d"
FILES = ["chats_service/app/endpoint_consumer.py", "chats_service/requirements.txt", ".gitignore",
         "chats_service/tests/test_flow_db_saved.py"]
def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=True).stdout
def parse(patch):
    files, cur, h = [], None, None
    for ln in patch.splitlines():
        if ln.startswith("diff --git"):
            cur = {"path": ln.split(" b/", 1)[1], "status": "M", "hunks": [], "add": 0, "del": 0}; files.append(cur); h = None
        elif cur is None: continue
        elif ln.startswith("new file"): cur["status"] = "A"
        elif ln.startswith("deleted file"): cur["status"] = "D"
        elif ln.startswith(("---", "+++", "index ", "\\")): continue
        elif ln.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)", ln)
            h = {"o": int(m[1]), "n": int(m[2]), "ctx": m[3], "head": ln.split(" @@")[0] + " @@", "lines": []}; cur["hunks"].append(h)
        elif h is not None and ln[:1] in "+- ":
            h["lines"].append([ln[0], ln[1:]])
            cur["add"] += ln[0] == "+"; cur["del"] += ln[0] == "-"
    return files
data = parse(git("show", "--format=", "-U3", COMMIT, "--", *FILES))
data.sort(key=lambda f: FILES.index(f["path"]))
# Иллюстрации правил (не из коммита): бинарник, удалённый файл, скрытый секрет
data += [
    {"path": "output/latency_chart.png", "status": "A", "binary": True, "size": [None, 61440], "add": 0, "del": 0, "hunks": []},
    {"path": "chats_service/app/legacy_requests.py", "status": "D", "add": 0, "del": 9, "collapsed": True, "hunks": [
        {"o": 1, "n": 0, "ctx": "", "head": "@@ -1,9 +0,0 @@", "lines": [["-", l] for l in
         ["import requests", "", "", "def post_sync(url, payload):", "    # TODO: таймаут", "    return requests.post(url, json=payload)",
          "", "", "def get_sync(url):"]]}]},
    {"path": "chats_service/.env", "status": "M", "secret": True, "add": 1, "del": 1, "hunks": []},
]
live = [{"path": "api_gateway_sigma/sigma_consumer.py", "status": "D", "add": 0, "del": 118, "hunks": []},
        {"path": "api_gateway_sigma/tests/test_sigma_consumer.py", "status": "D", "add": 0, "del": 64, "hunks": []}]
meta = {"commit": git("rev-parse", "--short", COMMIT).strip(), "ws": "d976e0e002fd", "rid": "7f3a91c2", "base": "21:51:04"}
tpl = (HERE / "shell.html").read_text()
(HERE / "stand.html").write_text(tpl.replace("/*DATA*/", "window.WSD=" + json.dumps({"files": data, "live": live, "meta": meta}, ensure_ascii=False) + ";"))
print("ok", [(f["path"], f["add"], f["del"]) for f in data])
