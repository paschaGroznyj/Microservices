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


def _seed(db, n, entry_type="evolution", archived=(), priorities=None, expires=None):
    """priorities/expires — {индекс: значение}; expires задаётся в минутах от NOW()."""
    priorities, expires = priorities or {}, expires or {}

    async def go():
        ids = []
        for i in range(n):
            ids.append(await db.pool.fetchval("""
                INSERT INTO agent_journal (chat_id, entry_type, content, is_archived, archive_summary,
                                           priority, expires_at, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $7,
                        CASE WHEN $8::int IS NULL THEN NULL ELSE NOW() + make_interval(mins => $8::int) END,
                        NOW() - make_interval(mins => $6), NOW())
                RETURNING id
            """, CHAT, entry_type, f"запись {i}", i in archived, "ручной архив" if i in archived else None,
                n - i, priorities.get(i, 3), expires.get(i)))
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
    # 12 обычных записей, в контекст идут 8 — 4 скрыты; identity в счётчик не входят
    assert "ещё 4 активных записей с меньшим приоритетом или старше не показаны" in text
    assert "ещё 2 более старых архивных записей не показаны" in text
    assert "Активных записей в журнале 12 (без identity и сводок), порог 9" in text


# ── приоритеты, разовые записи, лимит сводок ────────────────────────────────

@pg
def test_priority_one_survives_compression_even_if_oldest(db):
    ids = _seed(db, 9, priorities={0: 1, 1: 5})  # ids[0] самая старая, но приоритет 1
    res = _run(db, _LLM())
    assert res["compressed"] is True
    rows = {r["id"]: r for r in _rows(db)}
    assert not rows[ids[0]]["is_archived"]          # старая, но важная — осталась
    assert rows[ids[1]]["is_archived"]              # низкий приоритет ушёл в архив
    active = [i for i in ids if not rows[i]["is_archived"]]
    assert active == [ids[0], ids[7], ids[8]]       # priority 1 + две самые свежие


@pg
def test_expired_entries_hidden_and_not_counted(db):
    ids = _seed(db, 9, expires={0: -5, 1: -1})       # две разовые записи уже просрочены
    llm = _LLM()
    assert _run(db, llm) is None and llm.calls == []  # живых 7 <= порога: сжатия нет
    flags = dict(db.loop.run_until_complete(db.pool.fetch(
        "SELECT id, is_expired FROM agent_journal WHERE chat_id = $1", CHAT)))
    assert flags[ids[0]] and flags[ids[1]] and not flags[ids[2]]
    assert len(flags) == 9                           # ничего не удалено


@pg
def test_compression_keeps_at_most_two_summaries(db):
    old = _seed(db, 2, entry_type="summary")
    _seed(db, 9)
    res = _run(db, _LLM())
    assert res["compressed"] is True and res["merged_summary_ids"] == [old[0]]
    live = db.loop.run_until_complete(db.pool.fetch(
        "SELECT id FROM agent_journal WHERE chat_id = $1 AND entry_type = 'summary' "
        "AND is_archived = FALSE ORDER BY id", CHAT))
    assert [r["id"] for r in live] == [old[1], res["summary_id"]]


@pg
def test_context_summaries_separate_and_expired_hidden(db):
    from datetime import timezone, timedelta
    from agent.core import db as core_db

    s = _seed(db, 3, entry_type="summary")
    ev = _seed(db, 8, expires={0: -1, 1: 120})
    agent_db = core_db.AgentDB()
    agent_db.pool = db.pool
    tz = timezone(timedelta(hours=4))
    text = db.loop.run_until_complete(agent_db.get_agent_journal(CHAT, tz=tz))
    assert "── СВОДКИ ЖУРНАЛА ──" in text
    assert f"ID: {s[0]} |" not in text and f"ID: {s[1]} |" in text and f"ID: {s[2]} |" in text
    assert "ещё 1 старых сводок не показаны" in text
    assert f"ID: {ev[0]} |" not in text              # просроченная разовая не в контексте
    for i in ev[1:]:                                  # 7 живых обычных, сводки лимит 8 не едят
        assert f"ID: {i} |" in text
    assert "Приоритет: 3 | Истекает:" in text
    assert "Активных записей в журнале" not in text   # 7 <= порог 9


# ── инструмент: insert/update/expire через handler ──────────────────────────

def _op(db, **kw):
    from agent.tools.journal.journal_handler import handle_journal_operation
    kw.setdefault("entry_type", "evolution")
    kw.setdefault("content", "")
    return db.loop.run_until_complete(handle_journal_operation(db=db, llm=_LLM(), chat_id=CHAT, **kw))


@pg
def test_handler_priority_expiry_local_time(db):
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=4))   # Самара
    res = _op(db, action="insert", content="Напомнить про ревью", priority=1,
              expires_at="2099-11-25 10:00", tz=tz)
    assert res["ok"] and res["priority"] == 1
    row = db.loop.run_until_complete(db.pool.fetchrow(
        "SELECT priority, expires_at FROM agent_journal WHERE id = $1", res["id"]))
    assert row["priority"] == 1
    assert row["expires_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") == "2099-11-25 06:00"

    assert _op(db, action="update", entry_id=res["id"], priority=4)["ok"]
    assert _op(db, action="expire", entry_id=res["id"])["ok"]
    hidden = db.loop.run_until_complete(db.pool.fetchrow(
        "SELECT is_expired, priority FROM agent_journal WHERE id = $1", res["id"]))
    assert hidden["is_expired"] and hidden["priority"] == 4

    bad = _op(db, action="insert", content="x", priority=9, tz=tz)
    assert bad.get("ok") is not True


# ── время прошлых сообщений в истории ───────────────────────────────────────

def test_history_stamp_uses_user_timezone():
    from datetime import datetime, timezone
    from agent.core.timezone_utils import stamp_history_text, user_tzinfo

    utc = datetime(2026, 11, 24, 4, 30, tzinfo=timezone.utc)
    assert stamp_history_text("привет", utc, user_tzinfo("Europe/Samara")) == "[24.11.2026 08:30] привет"
    # пояс истории совпадает с часами «Current date and time» в промпте
    from agent.core.timezone_utils import resolve_tzinfo
    assert user_tzinfo(None, -240) is resolve_tzinfo(None, -240)[0] or \
        user_tzinfo(None, -240).utcoffset(utc) == resolve_tzinfo(None, -240)[0].utcoffset(utc)
    assert stamp_history_text("x", utc, user_tzinfo("UTC+04:00")) == "[24.11.2026 08:30] x"
    assert stamp_history_text("x", None, user_tzinfo("Europe/Samara")) == "x"
