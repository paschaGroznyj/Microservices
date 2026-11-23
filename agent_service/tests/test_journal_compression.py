"""Автосжатие журнала: архивирует, а не удаляет; невалидный ответ модели журнал не трогает.

Парсер проверяется юнит-тестами. Работа с БД — на настоящем PostgreSQL,
если задан JOURNAL_TEST_DSN (иначе skip): схема agent_journal берётся из
postgres/sql/03_agent/01_journal_and_sessions.sql.
"""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.tools.journal import journal_compression as jc
from agent.tools.journal.journal_compression import parse_compression_response

IDS = [11, 12, 13]


def _ok(ids=IDS, summary="## Тема\n- Путь templates/budget/BUDGET_TEMPLATE.md (#11)"):
    return json.dumps({"summary": summary, "archive": {str(i): f"Запись {i} о бюджете." for i in ids}},
                      ensure_ascii=False)


# ── парсер ─────────────────────────────────────────────────────────────────

def test_parse_valid():
    summary, arch = parse_compression_response(_ok(), IDS)
    assert "BUDGET_TEMPLATE.md" in summary
    assert arch == {i: f"Запись {i} о бюджете." for i in IDS}


def test_parse_fenced_and_hash_keys():
    body = json.dumps({"summary": "x" * 50, "archive": {"#11": "a", "#12": "b", "#13": "c"}})
    _, arch = parse_compression_response(f"Вот:\n```json\n{body}\n```", IDS)
    assert set(arch) == set(IDS)


@pytest.mark.parametrize("text", [
    "Я — модуль сжатия бортового журнала. Ожидаю задачи.",
    json.dumps({"summary": "Я — модуль сжатия бортового журнала. Ожидаю задачи от вас.",
                "archive": {str(i): "x" for i in IDS}}, ensure_ascii=False),
    json.dumps({"summary": "коротко", "archive": {str(i): "x" for i in IDS}}),
    json.dumps({"summary": "x" * 50, "archive": {"11": "a", "12": "b"}}),
    json.dumps({"summary": "x" * 50, "archive": {"11": "a", "12": "b", "13": "c", "99": "d"}}),
    json.dumps({"summary": "x" * 50, "archive": {"11": "a", "12": "", "13": "c"}}),
    json.dumps({"summary": "x" * 50, "archive": {"11": "a", "12": "b", "13": "c" * 400}}),
    json.dumps({"summary": "x" * 50, "archive": ["11"]}),
])
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        parse_compression_response(text, IDS)


def test_response_text_anthropic_blocks():
    resp = SimpleNamespace(content=[{"type": "thinking", "thinking": "..."}, {"type": "text", "text": " {} "}])
    assert jc._response_text(resp) == "{}"


# ── реальный PostgreSQL ─────────────────────────────────────────────────────

DSN = os.environ.get("JOURNAL_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="JOURNAL_TEST_DSN не задан")
SCHEMA = Path(__file__).resolve().parents[2] / "postgres/sql/03_agent/01_journal_and_sessions.sql"
CHAT = "t-journal-compress"


class _LLM:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("provider down")
        payload = json.loads(messages[1]["content"].split("Записи (JSON):\n", 1)[1])
        ids = [e["id"] for e in payload]
        text = self.reply(ids) if callable(self.reply) else self.reply
        return SimpleNamespace(content=text if text is not None else _ok(ids))


@pytest.fixture
def db():
    asyncpg = pytest.importorskip("asyncpg")

    async def setup():
        pool = await asyncpg.create_pool(DSN, min_size=1, max_size=4)
        async with pool.acquire() as c:
            exists = await c.fetchval("SELECT to_regclass('agent_journal') IS NOT NULL")
            if not exists:
                sql = SCHEMA.read_text()
                await c.execute(sql[: sql.index("-- 2.")])  # только таблица журнала и индексы
            await c.execute("DELETE FROM agent_journal WHERE chat_id = $1", CHAT)
        return pool

    loop = asyncio.new_event_loop()
    pool = loop.run_until_complete(setup())
    yield SimpleNamespace(pool=pool, loop=loop)
    loop.run_until_complete(pool.execute("DELETE FROM agent_journal WHERE chat_id = $1", CHAT))
    loop.run_until_complete(pool.close())
    loop.close()


def _seed(db, n, entry_type="evolution", archived=()):
    async def go():
        ids = []
        for i in range(n):
            ids.append(await db.pool.fetchval("""
                INSERT INTO agent_journal (chat_id, entry_type, content, is_archived, archive_summary,
                                           created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, NOW() - make_interval(mins => $6), NOW())
                RETURNING id
            """, CHAT, entry_type, f"запись {i}", i in archived, "ручной архив" if i in archived else None,
                n - i))
        return ids
    return db.loop.run_until_complete(go())


def _rows(db):
    return db.loop.run_until_complete(db.pool.fetch(
        "SELECT id, entry_type, is_archived, archive_summary, content, context "
        "FROM agent_journal WHERE chat_id = $1 ORDER BY id", CHAT))


def _run(db, llm, entry_type="evolution"):
    return db.loop.run_until_complete(jc.maybe_compress_journal(db, llm, CHAT, entry_type))


@pg
def test_compress_archives_instead_of_delete(db):
    ids = _seed(db, 9)
    res = _run(db, _LLM())
    assert res["compressed"] is True and res["archived_ids"] == ids[:6]
    rows = {r["id"]: r for r in _rows(db)}
    assert len(rows) == 10  # 9 исходных + summary, ничего не удалено
    for i in ids[:6]:
        assert rows[i]["is_archived"] and rows[i]["archive_summary"] == f"Запись {i} о бюджете."
        assert rows[i]["content"].startswith("запись ")  # исходный текст сохранён
    for i in ids[6:]:
        assert not rows[i]["is_archived"]
    summary = rows[res["summary_id"]]
    assert summary["entry_type"] == "summary" and not summary["is_archived"]
    assert json.loads(summary["context"])["compressed_ids"] == ids[:6]


@pg
def test_below_threshold_counts_only_active(db):
    # 12 записей, из них 5 уже в ручном архиве: активных 7 <= 8 — сжатия нет.
    _seed(db, 12, archived={0, 1, 2, 3, 4})
    llm = _LLM()
    assert _run(db, llm) is None and llm.calls == []


@pg
def test_manual_archive_not_touched(db):
    ids = _seed(db, 11, archived={0, 1})  # 9 активных
    res = _run(db, _LLM())
    assert res["archived_ids"] == ids[2:8]
    rows = {r["id"]: r for r in _rows(db)}
    assert rows[ids[0]]["archive_summary"] == "ручной архив"


@pytest.mark.parametrize("reply,fail", [
    ("Я — модуль сжатия бортового журнала. Ожидаю задачи.", False),
    (lambda ids: _ok(ids[:-1]), False),
    (None, True),
])
@pg
def test_bad_response_leaves_journal_untouched(db, reply, fail):
    _seed(db, 9)
    before = [tuple(r) for r in _rows(db)]
    res = _run(db, _LLM(reply=reply, fail=fail))
    assert res["compressed"] is False
    assert [tuple(r) for r in _rows(db)] == before


@pg
def test_identity_and_summary_skipped(db):
    _seed(db, 12, entry_type="identity")
    llm = _LLM()
    assert _run(db, llm, "identity") is None and llm.calls == []


@pg
def test_race_rolls_back(db, monkeypatch):
    ids = _seed(db, 9)

    llm = _LLM()
    orig = jc._apply

    async def racing_apply(*a, **kw):
        # Пока модель отвечала, одну из записей вручную убрали в архив.
        await db.pool.execute("UPDATE agent_journal SET is_archived = TRUE WHERE id = $1", ids[0])
        return await orig(*a, **kw)

    monkeypatch.setattr(jc, "_apply", racing_apply)
    res = _run(db, llm)
    assert res["compressed"] is False
    rows = {r["id"]: r for r in _rows(db)}
    assert len(rows) == 9  # summary не вставлена
    assert sum(r["is_archived"] for r in rows.values()) == 1  # только ручная правка


# ── контекст журнала ────────────────────────────────────────────────────────

@pg
def test_context_all_identity_and_hidden_counters(db, monkeypatch):
    from agent.core import db as core_db

    ident = _seed(db, 3, entry_type="identity")
    _seed(db, 12, entry_type="evolution")
    _seed(db, 4, entry_type="anomaly", archived={0, 1, 2, 3})
    monkeypatch.setattr(core_db, "MAX_JOURNAL_ARCHIVE_IN_CONTEXT", 2)
    agent_db = core_db.AgentDB()
    agent_db.pool = db.pool
    text = db.loop.run_until_complete(agent_db.get_agent_journal(CHAT))
    for i in ident:
        assert f"ID: {i} | Тип: [identity]" in text
    assert "ещё 2 активных записей старше не показаны" in text
    assert "ещё 2 более старых архивных записей не показаны" in text
    assert "Активных записей в журнале 15, порог 9" in text
