import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import importlib.util
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage
from agent.core.token_usage import cache_read_known, normalize_usage

@pytest.mark.parametrize('raw,known', [
    ({'prompt_tokens': 100}, False),
    ({'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 0}}, True),
    ({'input_tokens': 40, 'cache_read_input_tokens': 60}, True),
    ({'input_tokens': 100}, False),
    ({}, False),
])
def test_cache_presence(raw, known):
    assert cache_read_known(raw) is known

@pytest.mark.parametrize('details,known', [({}, False), ({'cache_read': 0}, True), ({'cache_read': 30}, True)])
def test_metadata_cache(details, known):
    msg = AIMessage(content='ok', usage_metadata={'input_tokens': 100, 'output_tokens': 1, 'total_tokens': 101, 'input_token_details': details})
    assert cache_read_known(msg) is known
    assert normalize_usage(msg)['input_tokens'] == 100

def test_actual_compaction_threshold():
    from agent.common.context_limits import TOKEN_SOFT_LIMIT
    assert TOKEN_SOFT_LIMIT == 200_000
    root = Path(__file__).parents[1] / 'app'
    assert 'from .common.context_limits import TOKEN_SOFT_LIMIT' in (root / 'agent/context_compactor.py').read_text()
    assert '"max_tokens": TOKEN_SOFT_LIMIT' in (root / 'api/memory_api.py').read_text()
