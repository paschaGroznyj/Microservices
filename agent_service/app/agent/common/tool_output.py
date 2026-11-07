"""Единая обрезка вывода инструментов для контекста модели.

Все места, где stdout/stderr/результат тула урезается перед ToolMessage,
трейсом или компактором, пользуются этим модулем:

- clip_text    — текст: схлопывает повторы, оставляет начало и конец
                 (перевес к концу, там traceback и итоговые print), режет
                 по границам строк, в метке пишет, сколько пропущено;
- fit_result   — dict: урезает самые длинные поля, пока сериализованный
                 результат не уложится в лимит. Строку после str() не режем,
                 поэтому результат всегда остаётся разбираемым;
- coerce_tool_result — обратный разбор str(dict)/json в dict.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Callable

DEFAULT_HEAD_RATIO = 0.3
MIN_REPEAT = 3  # с какого числа одинаковых строк подряд схлопываем

_CLIP_MARK_RE = re.compile(r"\n?… \[пропущено \d+ строк, \d+ симв\] …\n?")


def _resolve_carriage_returns(line: str) -> str:
    # Прогресс-бары (tqdm, pip, curl) перерисовывают строку через \r —
    # в терминале видна только последняя версия, её и оставляем.
    if "\r" not in line:
        return line
    parts = [p for p in line.split("\r") if p]
    return parts[-1] if parts else ""


def collapse_repeats(text: str, min_repeat: int = MIN_REPEAT) -> str:
    """Схлопывает одинаковые строки подряд в «строка  [×N]».

    Пустые строки не схлопываются. Сравнение точное: строки таблиц,
    отличающиеся хотя бы цифрой, остаются как есть.
    """
    if not isinstance(text, str) or not text:
        return text
    trailing_nl = text.endswith("\n")
    lines = [_resolve_carriage_returns(l) for l in text.split("\n")]
    if trailing_nl:
        lines = lines[:-1]
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        j = i + 1
        while j < n and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run >= min_repeat and lines[i].strip():
            out.append(f"{lines[i]}  [×{run}]")
        else:
            out.extend(lines[i:j])
        i = j
    res = "\n".join(out)
    return res + "\n" if trailing_nl else res


def clip_text(
    text: Any,
    max_chars: int,
    *,
    head_ratio: float = DEFAULT_HEAD_RATIO,
    collapse: bool = True,
) -> Any:
    """Обрезает текст до max_chars, сохраняя начало и конец.

    head_ratio — доля бюджета под начало (0 — только конец, как для stderr).
    Срез сдвигается к ближайшей границе строки, если она недалеко, чтобы
    не рвать строки посередине. Нестроки возвращаются без изменений.
    """
    if not isinstance(text, str):
        return text
    if collapse:
        text = collapse_repeats(text)
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    mark_len = len("\n… [пропущено 000000 строк, 00000000 симв] …\n")
    budget = max(max_chars - mark_len, 0)
    head_len = int(budget * max(0.0, min(head_ratio, 1.0)))
    tail_len = budget - head_len
    slack = max(budget // 10, 1)  # насколько можно уйти назад ради границы строки

    head = text[:head_len]
    if head_len:
        cut = head.rfind("\n")
        if cut != -1 and head_len - cut <= slack:
            head = head[: cut + 1]

    tail = text[len(text) - tail_len:] if tail_len else ""
    if tail_len:
        cut = tail.find("\n")
        if cut != -1 and cut < slack:
            tail = tail[cut + 1:]

    middle = text[len(head): len(text) - len(tail)]
    skipped_lines = middle.count("\n")
    mark = f"… [пропущено {skipped_lines} строк, {len(middle)} симв] …"
    if head and not head.endswith("\n"):
        mark = "\n" + mark
    if tail:
        mark += "\n"
    return f"{head}{mark}{tail}"


def coerce_tool_result(result: Any) -> dict | None:
    """Приводит результат тула к dict.

    _invoke_tool отдаёт результат строкой (str(dict)), компактор передаёт
    content ToolMessage. Возвращает None, если разобрать нельзя.
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, BaseException):
        return {"ok": False, "error": f"{type(result).__name__}: {result}"}
    if isinstance(result, str):
        s = result.strip()
        if not s.startswith("{"):
            return None
        try:
            v = ast.literal_eval(s)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            try:
                v = json.loads(s)
            except (json.JSONDecodeError, TypeError):
                return None
        return v if isinstance(v, dict) else None
    return None


def _clip_list(value: list, keep: int) -> list:
    if len(value) <= keep:
        return value
    head = max(keep // 3, 1)
    tail = max(keep - head, 1)
    return value[:head] + [f"… [пропущено {len(value) - head - tail} элементов] …"] + value[-tail:]


def fit_result(
    result: dict,
    max_chars: int,
    *,
    field_limits: dict[str, int] | None = None,
    tail_fields: tuple[str, ...] = ("stderr", "error", "traceback"),
    serialize: Callable[[Any], str] = str,
) -> str:
    """Сериализует dict так, чтобы он уложился в max_chars и остался разбираемым.

    1. field_limits — жёсткие лимиты отдельных полей (stdout 3000 и т.п.).
    2. Пока результат длиннее max_chars, урезаем самое длинное поле:
       строку через clip_text, список — по элементам.
    3. Если урезать уже нечего (вложенные структуры), обрезаем итоговую
       строку как текст — крайний случай.
    tail_fields обрезаются только с конца: там traceback.
    """
    data = dict(result)
    field_limits = field_limits or {}

    def _clip_field(key: str, limit: int) -> None:
        val = data[key]
        ratio = 0.0 if key in tail_fields else DEFAULT_HEAD_RATIO
        data[key] = clip_text(val, limit, head_ratio=ratio)

    for key, limit in field_limits.items():
        if isinstance(data.get(key), str):
            _clip_field(key, limit)

    s = serialize(data)
    for _ in range(32):
        if len(s) <= max_chars:
            return s
        over = len(s) - max_chars
        # Размер поля внутри сериализованного dict: для str() строка идёт
        # через repr (\n -> \\n), поэтому считаем вклад поля в контейнере.
        empty = {k: ("" if isinstance(v, str) else []) for k, v in data.items()
                 if isinstance(v, (str, list))}
        sizes = {}
        for k in empty:
            sz = len(serialize({k: data[k]})) - len(serialize({k: empty[k]}))
            if sz > 200:
                sizes[k] = sz
        if not sizes:
            break
        key = max(sizes, key=sizes.get)
        val = data[key]
        if isinstance(val, list):
            keep = max(int(len(val) * (1 - over / sizes[key])) - 1, 2)
            if keep >= len(val):
                break
            data[key] = _clip_list(val, keep)
        else:
            # serialize() экранирует \n и кавычки — длина в repr больше,
            # чем в исходной строке. Пересчитываем лимит пропорционально.
            ratio = len(val) / max(sizes[key], 1)
            target = max(int((sizes[key] - over - 80) * ratio), 100)
            if target >= len(val):
                break
            _clip_field(key, target)
        s = serialize(data)
    if len(s) <= max_chars:
        return s
    return clip_text(s, max_chars, collapse=False)


def tool_content(result: Any, max_chars: int, **kw) -> str:
    """Текст ToolMessage из любого результата тула."""
    if isinstance(result, BaseException):
        return clip_text(f"Ошибка инструмента: {result}", max_chars)
    rd = result if isinstance(result, dict) else coerce_tool_result(result)
    if rd is not None:
        return fit_result(rd, max_chars, **kw)
    return clip_text(str(result), max_chars)
