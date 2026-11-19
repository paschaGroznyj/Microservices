import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.api import memory_api

@pytest.mark.parametrize('cache,expected', [(None, None), (0, 0), (25000, 25)])
@pytest.mark.parametrize('serialized', [False, True])
def test_endpoint_cache(monkeypatch, cache, expected, serialized):
    import json
    usage = {'context_tokens': 100000, 'input_tokens': 900000, 'cache_read_tokens': 800000}
    if cache is not None:
        usage['context_cache_read_tokens'] = cache
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=1)
    conn.fetch = AsyncMock(return_value=[])
    async def fetchrow(sql, *args):
        if 'FROM agent_journal' in sql:
            return {'active_count': 0, 'archived_count': 0, 'total_chars': 0}
        if 'FROM chat_rolling_summary' in sql:
            return None
        if 'tokens_usage' in sql:
            return {'message_id': 'answer-2', 'tokens_usage': json.dumps(usage) if serialized else usage}
        raise AssertionError(sql)
    conn.fetchrow = fetchrow
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(memory_api, '_db', SimpleNamespace(pool=pool))
    app = FastAPI()
    app.include_router(memory_api.memory_router)
    response = TestClient(app).get('/web/memory/test/stats', params={'user_id': 777})
    assert response.status_code == 200
    c = response.json()['context']
    assert c['message_id'] == 'answer-2'
    assert c['cache_pct'] == expected
    assert c['context_cache_read_tokens'] == cache
    assert c['tokens'] == 100000
    assert c['max_tokens'] == 200000
    assert c['limit_kind'] == 'compaction_threshold'
    assert c['model_window_tokens'] is None
