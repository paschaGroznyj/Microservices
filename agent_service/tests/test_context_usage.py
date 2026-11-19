import os
os.environ['OPENBLAS_NUM_THREADS'] = '1'
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from agent.common.context_usage import prompt_weights, scale_breakdown
from agent.common.trace_budget import CONTENT_MARK


def test_final_prompt_components():
    messages = [SystemMessage(content='base'), SystemMessage(content='БОРТОВОЙ ЖУРНАЛ: journal'),
                SystemMessage(content='СЖАТАЯ ИСТОРИЯ: summary'), HumanMessage(content='question'),
                AIMessage(content='answer'+CONTENT_MARK+'trace'), ToolMessage(content='result', tool_call_id='1')]
    weights = prompt_weights(messages)
    assert all(weights[k] > 0 for k in weights if k != 'tools')
    assert weights['tools'] == 0
    for total in [1, 9, 100, 123456]:
        b = scale_breakdown(weights, total)
        assert b['estimated'] is True
        assert sum(b['tokens'].values()) == total
    assert scale_breakdown(weights, 0) is None


def test_snapshot_and_no_tools():
    messages = [HumanMessage(content='hello')]
    before = prompt_weights(messages)
    messages.append(ToolMessage(content='x'*1000, tool_call_id='1'))
    after = prompt_weights(messages)
    assert before['tool_trace'] == 0 < after['tool_trace']
    assert before['tools'] == after['tools'] == 0
