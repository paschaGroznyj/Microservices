from datetime import datetime, timezone
import json
import logging
import multiprocessing
import os
import time

from agent.core.event_log import DailyEventHandler


def timestamp(day):
    return datetime(2026, 11, day, tzinfo=timezone.utc).timestamp()


def record(n, text='журнал'):
    return logging.makeLogRecord({'msg': json.dumps({'n': n, 'text': text}, ensure_ascii=False)})


def test_days_by_filename_not_mtime_and_startup_cleanup(tmp_path):
    for day in range(10, 21):
        (tmp_path / f'{day:02}_11_2026.jsonl').write_text('{}\n')
    (tmp_path / 'keep.txt').write_text('not managed')
    h = DailyEventHandler(tmp_path, clock=lambda: timestamp(20), cleanup_interval=0)
    try:
        assert sorted(p.name for p in tmp_path.glob('*.jsonl')) == [f'{d:02}_11_2026.jsonl' for d in range(14, 21)]
        assert (tmp_path / 'keep.txt').exists()
    finally:
        h.close()


def test_midnight_utc(tmp_path):
    now = [timestamp(20) - .1]
    h = DailyEventHandler(tmp_path, clock=lambda: now[0], cleanup_interval=0)
    try:
        h.emit(record(1)); now[0] += .2; h.emit(record(2))
        assert json.loads((tmp_path / '19_11_2026.jsonl').read_text())['n'] == 1
        assert json.loads((tmp_path / '20_11_2026.jsonl').read_text())['n'] == 2
    finally:
        h.close()


def test_budget_priority_and_complete_utf8_records(tmp_path):
    old = tmp_path / '19_11_2026.jsonl'; old.write_text('{}\n' * 30)
    h = DailyEventHandler(tmp_path, clock=lambda: timestamp(20), cleanup_interval=0, max_bytes=210)
    try:
        for n in range(30):
            h.emit(record(n))
            assert sum(p.stat().st_size for p in tmp_path.glob('*.jsonl')) <= 210
        assert not old.exists()
        p = tmp_path / '20_11_2026.jsonl'
        values = [json.loads(line) for line in p.read_text().splitlines()]
        assert values[-1]['n'] == 29
        assert all(v['text'] == 'журнал' for v in values)
        assert p.stat().st_mode & 0o777 == 0o600
        before = p.read_bytes(); h.emit(record(40, 'x' * 1000)); assert p.read_bytes() == before
    finally:
        h.close(); h.close()


def test_legacy_budget_and_age(tmp_path):
    p = tmp_path / 'events.jsonl.1'; p.write_text('{}\n')
    os.utime(p, (timestamp(1), timestamp(1)))
    q = tmp_path / 'events.jsonl'; q.write_text('x' * 200)
    h = DailyEventHandler(tmp_path, clock=lambda: timestamp(20), max_bytes=100, cleanup_interval=0)
    try:
        assert not p.exists() and not q.exists()
    finally:
        h.close()


def test_background_cleanup_even_when_idle(tmp_path):
    now = [timestamp(20)]
    h = DailyEventHandler(tmp_path, clock=lambda: now[0], cleanup_interval=.02)
    try:
        h.emit(record(1)); now[0] = timestamp(28)
        deadline = time.monotonic() + 2
        while list(tmp_path.glob('*.jsonl')) and time.monotonic() < deadline:
            time.sleep(.01)
        assert not list(tmp_path.glob('*.jsonl'))
    finally:
        h.close()
    assert not h._worker.is_alive()


def _writer(path, worker):
    h = DailyEventHandler(path, clock=lambda: timestamp(20), max_bytes=1500, cleanup_interval=0)
    for n in range(60):
        h.emit(record(worker * 100 + n))
    h.close()


def test_workers_share_budget_and_valid_lines(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    workers = [ctx.Process(target=_writer, args=(str(tmp_path), n)) for n in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(15)
        assert w.exitcode == 0
    files = list(tmp_path.glob('*.jsonl'))
    assert sum(p.stat().st_size for p in files) <= 1500
    assert files
    for p in files:
        assert all(json.loads(l)['text'] == 'журнал' for l in p.read_text().splitlines())
    assert not list(tmp_path.glob('.events-trim-*'))


def test_restart_appends(tmp_path):
    for n in range(2):
        h = DailyEventHandler(tmp_path, clock=lambda: timestamp(20), cleanup_interval=0)
        h.emit(record(n)); h.close()
    assert [json.loads(l)['n'] for l in (tmp_path / '20_11_2026.jsonl').read_text().splitlines()] == [0, 1]


def test_symlink_is_not_followed(tmp_path):
    target = tmp_path / 'private.txt'; target.write_text('unchanged')
    (tmp_path / '20_11_2026.jsonl').symlink_to(target)
    h = DailyEventHandler(tmp_path, clock=lambda: timestamp(20), max_bytes=10, cleanup_interval=0)
    try:
        h.handleError = lambda record: None
        h.emit(record(1))
        assert target.read_text() == 'unchanged'
    finally:
        h.close()
