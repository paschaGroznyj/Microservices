"""Сводка вызова тула для tool_trace (память о вызовах между ходами).

Что именно сохранять, задаётся в core/settings.py → TOOL_TRACE_POLICY.
Здесь только механика: общий форматтер по политике и спец-форматтеры для
тулов, у которых результат надо разбирать (python, shell, поиск, картинки).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .tool_output import clip_text, coerce_tool_result
from ..core.settings import (
    RUNTIME_TOOLS,
    TOOL_DESCRIPTIONS,
    TOOL_TRACE_POLICY,
    TOOLS_SETTINGS,
    TRACE_ARG_LIMIT,
    TRACE_DEFAULT_POLICY,
    UNIVERSAL_TOOLS,
)

logger = logging.getLogger(__name__)
_warned: set[str] = set()

# execute_python_code / edit_and_run при успехе: хвост stdout и число путей.
TRACE_PY_STDOUT = 500
TRACE_PY_MAX_FILES = 10


def known_tools() -> set[str]:
    """Все тулы, о которых знает settings.py."""
    names = set(UNIVERSAL_TOOLS) | set(RUNTIME_TOOLS) | set(TOOL_DESCRIPTIONS)
    for lst in TOOLS_SETTINGS.values():
        names.update(lst)
    return names


def missing_policies() -> set[str]:
    return known_tools() - set(TOOL_TRACE_POLICY)


def policy_for(name: str) -> dict:
    pol = TOOL_TRACE_POLICY.get(name)
    if pol is None:
        if name not in _warned:
            _warned.add(name)
            logger.warning(f"[tool_trace] нет политики для {name!r}, пишу по умолчанию")
        return TRACE_DEFAULT_POLICY
    return pol


def _cut(text: Any, limit: int | None) -> str:
    s = str(text)
    if limit is None or len(s) <= limit:
        return s
    return s[:limit] + f"…[+{len(s) - limit} симв]"


def _format_args(args: Any, pol: dict) -> str:
    if not isinstance(args, dict):
        return _cut(args, TRACE_ARG_LIMIT)
    keep = pol.get("args")
    limits = pol.get("arg_limits") or {}
    items = args.items() if keep is None else ((k, args[k]) for k in keep if k in args)
    parts = []
    for k, v in items:
        if v in (None, "", [], {}):
            continue
        parts.append(f"{k}={_cut(repr(v), limits.get(k, TRACE_ARG_LIMIT))}")
    return ", ".join(parts)


def _generic(name: str, args: Any, result: Any, pol: dict) -> str:
    head = f"{name}({_format_args(args, pol)})"
    limit = pol.get("result", 1000)
    if limit == 0:
        return head
    return f"{head} → {_cut(result, limit)}"


# ── спец-форматтеры ─────────────────────────────────────────────────────

def _fmt_search_internet(name, args, result, pol):
    # Результат — многострочный текст с [S1]...[Sn], берём заголовки + URL.
    titles = [l for l in str(result).split("\n") if l.startswith("[S")]
    return f"search_internet(query='{args.get('query')}') → {' | '.join(titles)}"


def _fmt_generate_image(name, args, result, pol):
    return (
        f"generate_image(prompt='{str(args.get('prompt', ''))[:300]}', "
        f"aspect_ratio={args.get('aspect_ratio', '16:9')}, "
        f"n={args.get('n', 1)}) →\n{str(result)[:1200]}"
    )


def _fmt_find_generated_image(name, args, result, pol):
    return (
        f"find_generated_image(query={args.get('query')!r}, "
        f"file_path={args.get('file_path')!r}) →\n{str(result)[:1200]}"
    )


def _fmt_analyze_image(name, args, result, pol):
    return (
        f"analyze_image(prompt='{str(args.get('prompt', ''))[:300]}', "
        f"image_index={args.get('image_index', 0)}) →\n{str(result)[:1200]}"
    )


def _fmt_transcribe_media(name, args, result, pol):
    src = args.get("source_media_id") or args.get("local_path")
    return (
        f"transcribe_media(source={src!r}, include_visual={args.get('include_visual', False)}) "
        f"→\n{str(result)[:1500]}"
    )


def _fmt_python_run(name, args, result, pol):
    """execute_python_code / edit_and_run: успех — сводка, ошибка — код + ошибка."""
    rd = coerce_tool_result(result)
    if rd is None:
        # Результат не разобрать (например, обрезан в ToolMessage) —
        # не помечаем как ошибку, отдаём начало как есть.
        return f"{name}() [результат не разобран] {clip_text(str(result), 500)}"
    lib_note = f", library_id={args['library_id']}" if args.get("library_id") is not None else ""
    ws_script = rd.get("saved_script_in_workspace")
    ws_id = rd.get("workspace_id") or args.get("workspace_id")
    if ws_script and ws_id:
        script_note = f", скрипт: workspace {ws_id} → {ws_script}"
    elif ws_script:
        script_note = f", скрипт в workspace: {ws_script}"
    else:
        script_note = ""

    if rd.get("ok"):
        # Код в трейс не дублируем: он есть в вызове и в output/.scripts.
        # Полные пути: по ним агент потом шлёт файл (send_file_to_user,
        # analyze_image), одного имени не хватает.
        files = [
            f.get("path") or f.get("filename")
            for f in (rd.get("saved_files") or []) if isinstance(f, dict)
        ]
        if len(files) > TRACE_PY_MAX_FILES:
            files = files[:TRACE_PY_MAX_FILES] + [f"… ещё {len(files) - TRACE_PY_MAX_FILES}"]
        files_note = f", файлы: {files}" if files else ""
        # Хвост stdout: там обычно итог (print результата, пути, числа).
        out = str(rd.get("stdout") or "").strip()
        out_note = f"\nstdout: {clip_text(out, TRACE_PY_STDOUT, head_ratio=0)}" if out else ""
        return f"{name}() [успех]{lib_note}{files_note}{script_note}{out_note}"

    # Ошибка — код в трейс, чтобы агент помнил, что написал и почему упало.
    code = rd.get("code") or args.get("code", "")
    stderr = str(rd.get("stderr") or "")
    error = (clip_text(str(rd.get("error") or "").strip(), 300, head_ratio=0)
             or clip_text(stderr.strip(), 300, head_ratio=0))
    if not error:
        rc = rd.get("exit_code")
        error = f"exit_code={rc}" if rc is not None else "причина не передана"
    line_note = f", строка {rd['error_line']}" if rd.get("error_line") else ""
    code_s = str(code)
    err_ctx = str(rd.get("error_context") or "")
    if err_ctx and len(code_s) > 800:
        # Длинный скрипт: начало (импорты/константы) + строки вокруг ошибки.
        code_block = f"{clip_text(code_s, 400, head_ratio=1)}\n…\nвокруг ошибки:\n{clip_text(err_ctx, 1200)}"
    else:
        code_block = clip_text(code_s, 1200)
    return (
        f"{name}() [ОШИБКА]{lib_note}{script_note}{line_note}\n"
        f"код:\n{code_block}\n"
        f"ошибка: {error}"
    )


def _fmt_shell(name, args, result, pol):
    cmd_s = _cut(args.get("commands", args), 400)
    rd = coerce_tool_result(result)
    if rd is not None:
        out = str(rd.get("stdout", ""))
        err = str(rd.get("stderr", ""))
        res_s = f"ok={rd.get('ok')} rc={rd.get('exit_code')} stdout={clip_text(out, 800)}"
        if err.strip():
            res_s += f" stderr={clip_text(err, 300, head_ratio=0)}"
    else:
        res_s = clip_text(str(result), 800)
    return f"execute_shell_commands({cmd_s}) → {res_s}"


FORMATTERS: dict[str, Callable[[str, dict, Any, dict], str]] = {
    "search_internet": _fmt_search_internet,
    "generate_image": _fmt_generate_image,
    "find_generated_image": _fmt_find_generated_image,
    "analyze_image": _fmt_analyze_image,
    "transcribe_media": _fmt_transcribe_media,
    "python_run": _fmt_python_run,
    "shell": _fmt_shell,
}


def summarize_tool_call(tc: dict, result: Any) -> str | None:
    """Строка для tool_trace или None, если вызов писать не нужно."""
    name = tc.get("name") or ""
    pol = policy_for(name)
    if pol.get("skip"):
        return None
    args = tc.get("args") or {}
    fmt = FORMATTERS.get(pol.get("formatter") or "")
    if fmt is not None and isinstance(args, dict):
        return fmt(name, args, result, pol)
    return _generic(name, args, result, pol)


_missing = missing_policies()
if _missing:
    logger.warning(f"[tool_trace] в TOOL_TRACE_POLICY нет политики для: {sorted(_missing)}")
