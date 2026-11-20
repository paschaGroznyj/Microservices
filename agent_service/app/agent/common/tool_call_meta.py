"""Метаданные вызова тула для chats.tool_calls.

Запись: {step, name, ok, i, par, t0, ms, hint?, err?}
- i    позиция вызова внутри шага (0..par-1), par — сколько вызовов шага шли параллельно
- t0   старт вызова от начала хода, мс; ms — длительность, мс
- hint начало главного аргумента (команда, запрос, путь), до HINT_MAX символов, секреты скрыты
- err  начало текста ошибки, до ERR_MAX символов, только при ok=False
Старые записи содержат только {step, name, ok}: все новые поля необязательны.
"""
import re
from typing import Any

HINT_MAX = 80
ERR_MAX = 160

# Какой аргумент показывать. Для остальных тулов — первый непустой строковый аргумент.
_HINT_KEYS = (
    "commands", "command", "code", "sql", "query", "url", "urls", "path", "filepath", "file_path",
    "local_path", "name_pattern", "pattern", "subject", "theme", "title", "prompt", "task", "action",
)

_SECRET = [
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|authorization)(\s*[=:]\s*)((?:bearer|basic|token)\s+\S+|\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2***"),
    (re.compile(r"(?i)\b(bearer|token|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)(://[^/\s:@]+:)[^@\s/]+@"), r"\1***@"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"), "***"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "***"),
]


def redact(text: str) -> str:
    for rx, rep in _SECRET:
        text = rx.sub(rep, text)
    return text


def _short(value: Any, limit: int) -> str:
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v is not None)
    text = redact(" ".join(str(value).split()))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _join_split_fields(args: dict) -> dict:
    """__fg_<field>_NNN -> <field>: аргументы, порезанные на куски, собираем обратно."""
    parts: dict[str, list[tuple[str, Any]]] = {}
    out: dict = {}
    for k, v in args.items():
        m = re.fullmatch(r"__fg_(.+)_(\d{3})", str(k))
        if m:
            parts.setdefault(m.group(1), []).append((m.group(2), v))
        else:
            out[k] = v
    for field, chunks in parts.items():
        out.setdefault(field, "".join(str(v) for _, v in sorted(chunks)))
    return out


def tool_call_hint(args: Any) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    args = _join_split_fields(args)
    for key in _HINT_KEYS:
        v = args.get(key)
        if v not in (None, "", [], ()):
            return _short(v, HINT_MAX)
    for v in args.values():
        if isinstance(v, str) and v.strip():
            return _short(v, HINT_MAX)
    return ""


def tool_failed(name: str, result: Any, result_dict: Any) -> bool:
    """Ошибка вызова: исключение, {"ok": False} или строка-ошибка из _invoke_tool
    (исключение тула и неизвестный тул _invoke_tool перехватывает и возвращает текстом)."""
    if isinstance(result, BaseException):
        return True
    if isinstance(result_dict, dict) and result_dict.get("ok") is False:
        return True
    return isinstance(result, str) and result.startswith((f"Ошибка при выполнении {name}:", f"Инструмент '{name}' не найден"))


def tool_call_error(result: Any, result_dict: Any) -> str:
    if isinstance(result, BaseException):
        text = f"{type(result).__name__}: {result}" if str(result) else type(result).__name__
        return _short(text, ERR_MAX)
    if isinstance(result_dict, dict):
        for key in ("error", "detail", "stderr", "message", "raw"):
            v = result_dict.get(key)
            if v:
                return _short(v, ERR_MAX)
    return _short(result, ERR_MAX) if isinstance(result, str) else ""


def tool_call_record(*, step: int, name: str, ok: bool, i: int, par: int,
                     timing: tuple[int, int] | None, args: Any, result: Any, result_dict: Any) -> dict:
    rec: dict[str, Any] = {"step": step, "name": name, "ok": ok, "i": i, "par": par}
    if timing:
        rec["t0"], rec["ms"] = timing
    hint = tool_call_hint(args)
    if hint:
        rec["hint"] = hint
    if not ok:
        err = tool_call_error(result, result_dict)
        if err:
            rec["err"] = err
    return rec
