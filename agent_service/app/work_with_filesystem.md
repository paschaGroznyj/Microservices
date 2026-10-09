appuser@...:/app$ python # Внутри agent, exit() на выход

import sys
sys.path.insert(0, "/app")
from tools.filesystem.tool_read_codebase import _tree_view, _read_file, _grep_search, _find_files, ROOT

# 1. Дерево — проверяем что мусорные папки вырезаны
print(_tree_view(ROOT, 3)["tree"])

/app
├── Dockerfile
├── __init__.py
├── app
│   ├── __init__.py
│   ├── agent
│   │   ├── __init__.py
│   │   ├── analyze_article.py
│   │   ├── core
│   │   │   ├── __init__.py
│   │   │   ├── agent_provider.py
│   │   │   ├── botocore_client.py
│   │   │   ├── config.py
│   │   │   ├── db.py
│   │   │   ├── file_parsers.py
│   │   │   └── sql_scripts
│   │   │       ├── agent_journal.sql
│   │   │       ├── audit_agent_journal.sql
│   │   │       ├── knowledge.sql
│   │   │       ├── papers_tables.sql
│   │   │       ├── services_talking.sql
│   │   │       └── summary_session.sql
│   │   ├── digest_email_handler.py
│   │   ├── download_pdf_and_make.py
│   │   ├── email_sender.py
│   │   ├── embeddings.py
│   │   ├── favorites_digest.py
│   │   ├── langchain_agent.py
│   │   ├── metadata.json
│   │   ├── migrate_reviews.py
│   │   ├── monitor.py
│   │   ├── scheduler_digest.py
│   │   ├── search_bot
│   │   │   ├── __init__.py
│   │   │   └── searcher.py
│   │   ├── summary.py
│   │   ├── summary_period.py
│   │   ├── tools
│   │   │   ├── filesystem
│   │   │   │   ├── __init__.py
│   │   │   │   └── tool_read_codebase.py
│   │   │   ├── parsers
│   │   │   │   ├── __init__.py
│   │   │   │   ├── tool_parse_page.py
│   │   │   │   └── web_search.py
│   │   │   ├── reviews
│   │   │   │   ├── __init__.py
│   │   │   │   ├── glossary_pm_cloud.md
│   │   │   │   └── tool_generate_reviews.py
│   │   │   ├── send_messages
│   │   │   │   ├── __init__.py
│   │   │   │   └── tool_send_email.py
│   │   │   └── sessions
│   │   │       ├── __init__.py
│   │   │       └── tool_session_point.py
│   │   ├── translate_relevant.py
│   │   ├── update_metadata.py
│   │   └── vector_db.py
│   ├── app.py
│   ├── main_agent.py
│   ├── md_rendering_example.html
│   └── searcher.py
├── config
│   └── recipients.json
├── readme.md
├── requirements.txt
└── Бизнес_требования.md

13 directories, 54 files


# 2. Чтение файла
res = _read_file("/app/app/agent/langchain_agent.py")
print(res["size"], res["truncated"])
print(res["content"][:500])

76111 True
>>> print(res["content"][:500])
import asyncio
import logging
import json
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

import httpx
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain.tools import tool
from langchain_core.messages import BaseMessage
from langchain.memory import ConversationBufferWindowMemory
from langchain_core.messages import ToolMessage

from .core.agent_provider import get_model, get_llm_model
from .core.botocore_client import O


# 3. Чтение большого файла — проверь truncation
# найди что-то реально большое, например faiss index не тронешь (бинарник),
# возьми любой md подлиннее или искусственно протестируй лимит:
res = _read_file("/app/app/agent/tokens.json")  # или другой файл побольше 50KB
print(res["truncated"]) # truncated = True, если превышен лимит по памяти



# 4. grep — ищем все def
res = _grep_search("def ", ROOT, exts=[".py"])
print(res["matches_count"], res["truncated"])
for m in res["matches"][:10]:
    print(m)

200 True
>>> for m in res["matches"][:10]:
...     print(m)
...
/app/app/app.py:39:async def get_producer() -> AIOKafkaProducer:
/app/app/app.py:50:async def handle_ouroboros_message(payload: dict, prod: AIOKafkaProducer):
/app/app/app.py:66:    async def send_status(status_text: str):
/app/app/app.py:143:async def _listen_ouroboros_with_progress(send_status) -> str | None:
/app/app/app.py:190:async def handle_message(payload: dict, prod: AIOKafkaProducer):
/app/app/app.py:248:async def _process_service_message(message_id: int, enriched_text: str):
/app/app/app.py:275:async def kafka_consumer():
/app/app/app.py:315:async def shutdown():
/app/app/app.py:320:async def kafka_moment():
/app/app/app.py:328:async def lifespan(app: FastAPI):

# 5. Ищем как LIKE
import importlib
import tools.filesystem.tool_read_codebase as m
importlib.reload(m)

res = m._find_files(m.ROOT, "*.py", 3)
print(res["count"])
for f in res["files"][:10]:
    print(f)

21
>>> for f in res["files"][:10]:
...     print(f)
...
{'path': '/app/__init__.py', 'size': 0}
{'path': '/app/app/__init__.py', 'size': 0}
{'path': '/app/app/app.py', 'size': 37954}
{'path': '/app/app/searcher.py', 'size': 12110}
{'path': '/app/app/agent/vector_db.py', 'size': 4123}
{'path': '/app/app/agent/analyze_article.py', 'size': 6646}
{'path': '/app/app/agent/scheduler_digest.py', 'size': 10228}
{'path': '/app/app/agent/update_metadata.py', 'size': 13416}
{'path': '/app/app/agent/favorites_digest.py', 'size': 11635}
{'path': '/app/app/agent/summary_period.py', 'size': 26112}


## НАПИСАНИЕ КОДА И СОХРАНЕНИЕ В Sandbox
sudo chown -R 1002:1002 ./sandbox # Дать роль себе на запись в папку
ls -la ./sandbox

docker exec -it agent_service sh -c "ls -la /sandbox" # Проверить роли

```
"""
Ручные тесты для sandbox write/edit + расширенного read.
Гоняем в python REPL внутри контейнера.
"""

import sys
sys.path.insert(0, "/app")

from tools.filesystem.tool_read_codebase import _safe_join, _read_file, _tree_view, _grep_search
from tools.filesystem.tool_write_sandbox import _write_file, _valid_sandbox_path, SANDBOX_ROOT
from tools.filesystem.tool_edit_sandbox import _edit_file

PASS, FAIL = "✅", "❌"
results = []

def check(name, condition):
    status = PASS if condition else FAIL
    results.append((status, name))
    print(status, name)


# ── write_sandbox_file: базовые кейсы ──────────────────────────
p = _valid_sandbox_path("draft.py")
check("valid path внутри sandbox резолвится", p == "/sandbox/draft.py")

r = _write_file(p, "print('hello')\n", "write")
check("запись нового файла", "error" not in r and r["bytes_written"] > 0)

r2 = _write_file(p, "print('again')\n", "append")
check("append дописывает", "error" not in r2)

with open(p) as f:
    content = f.read()
check("append реально добавил, не перезаписал", content == "print('hello')\nprint('again')\n")

# ── write_sandbox_file: security периметр ──────────────────────
check("../ вне sandbox блокируется", _valid_sandbox_path("../app/app.py") is None)
check("абсолютный путь в /app блокируется", _valid_sandbox_path("/app/app.py") is None)
check("абсолютный путь в /etc блокируется", _valid_sandbox_path("/etc/passwd") is None)
check("path traversal через несколько уровней блокируется", _valid_sandbox_path("../../../../etc/shadow") is None)

# ── write_sandbox_file: лимиты ──────────────────────────────────
big_content = "x" * 300_000
r3 = _write_file("/sandbox/big.txt", big_content, "write")
check("файл больше MAX_CONTENT_SIZE отклоняется", "error" in r3)

# ── edit_sandbox_file: базовые кейсы ────────────────────────────
_write_file("/sandbox/edit_test.py", "def foo():\n    return 1\n", "write")

r4 = _edit_file("/sandbox/edit_test.py", "return 1", "return 42")
check("точечная замена уникального фрагмента", "error" not in r4)

with open("/sandbox/edit_test.py") as f:
    check("замена реально применилась", "return 42" in f.read())

r5 = _edit_file("/sandbox/edit_test.py", "not_existing_string", "x")
check("несуществующий old_str даёт ошибку, не молча падает", "error" in r5)

# неоднозначная замена
_write_file("/sandbox/dup.py", "x = 1\nx = 1\n", "write")
r6 = _edit_file("/sandbox/dup.py", "x = 1", "x = 2")
check("неоднозначный old_str (2+ вхождения) отклоняется", "error" in r6 and "1 раз" not in r6.get("error", ""))

r7 = _edit_file("/sandbox/nonexistent_file.py", "a", "b")
check("edit несуществующего файла даёт понятную ошибку", "error" in r7)

# ── read-тулзы теперь видят /sandbox ────────────────────────────
check("_safe_join резолвит /sandbox напрямую", _safe_join("/sandbox/draft.py") == "/sandbox/draft.py")

res_read = _read_file("/sandbox/draft.py")
check("read_file читает файл из sandbox", "error" not in res_read and "hello" in res_read["content"])

res_tree = _tree_view(SANDBOX_ROOT, 2)
check("list_tree строит дерево sandbox", "error" not in res_tree)

res_grep = _grep_search("def foo", SANDBOX_ROOT, exts=[".py"])
check("grep_search находит по содержимому в sandbox", res_grep["matches_count"] >= 1)

# ── read-тулзы всё ещё НЕ видят посторонние пути ────────────────
check("read всё ещё блокирует /etc", _safe_join("/etc/passwd") is None)
check("read всё ещё блокирует .env", _safe_join("/app/.env") is None)

# ── итог ──────────────────────────────────────────────────────
print("\n" + "=" * 40)
failed = [n for s, n in results if s == FAIL]
if failed:
    print(f"{FAIL} ПРОВАЛЕНО: {len(failed)}")
    for n in failed:
        print("  -", n)
else:
    print(f"{PASS} Все {len(results)} тестов прошли")
```