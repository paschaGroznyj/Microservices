"""Relative serialized prompt weights, not provider block measurements."""
import json
import math

KEYS = ('system', 'journal', 'summary', 'history', 'tool_trace', 'tools')


def prompt_weights(messages, tools=()):
    weights = dict.fromkeys(KEYS, 0)
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, default=str))
    for message in messages:
        role = getattr(message, 'type', '')
        content = getattr(message, 'content', '')
        text = content if isinstance(content, str) else ''
        key = 'history'
        if role == 'system':
            key = ('journal' if 'БОРТОВОЙ ЖУРНАЛ' in text else
                   'summary' if 'СЖАТАЯ ИСТОРИЯ' in text or 'САММАРИ ПРОШЛОЙ' in text else 'system')
        if role == 'tool' or getattr(message, 'tool_calls', None):
            key = 'tool_trace'
        from .trace_budget import CONTENT_MARK
        if key == 'history' and isinstance(content, str) and CONTENT_MARK in content:
            content, trace = content.split(CONTENT_MARK, 1)
            weights['tool_trace'] += size(CONTENT_MARK + trace)
        weights[key] += size(content) + size(role) + 12
        calls = getattr(message, 'tool_calls', None)
        if calls:
            weights['tool_trace'] += size(calls)
    for tool in tools:
        schema = getattr(tool, 'args_schema', None)
        weights['tools'] += size({'name': tool.name, 'description': tool.description,
                                 'parameters': schema.schema() if schema else {}})
    return weights


def scale_breakdown(weights, input_tokens):
    total = max(0, int(input_tokens or 0))
    denominator = sum(weights.values())
    if not total or not denominator:
        return None
    raw = {k: total * weights[k] / denominator for k in KEYS}
    result = {k: math.floor(raw[k]) for k in KEYS}
    for key in sorted(KEYS, key=lambda k: raw[k] - result[k], reverse=True)[:total-sum(result.values())]:
        result[key] += 1
    return {'estimated': True, 'method': 'serialized_prompt_weights_scaled_to_input',
            'tokens': result}
