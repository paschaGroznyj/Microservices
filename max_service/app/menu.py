"""
max_service/app/menu.py
───────────────────────
Меню настроек /start для MAX (аналог cmd_start/on_callback из telegram_service).
Чистые билдеры inline-клавиатур + текст сводки. HTTP-работа с настройками
живёт в bot.py, сюда передаются готовые dict настроек.

MAX inline-keyboard: attachment type=inline_keyboard,
  payload.buttons = [[{type:"callback", text, payload}], ...]
callback_data у MAX зовётся payload (<=64 байта по контракту).
"""
from __future__ import annotations

MAX_MODELS = ["coder", "designer", "universal", "gemini"]
MAX_TRIGGERS = ["mention", "orchestrator"]
MAX_TOOL_CATEGORIES = ["pm_research", "internet", "content", "sessions", "skills"]
# Платные/админские категории — видны только админу (аналог TG_ADMIN_TOOL_CATEGORIES).
MAX_ADMIN_TOOL_CATEGORIES = ["media_gen", "admin"]


def _btn(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def keyboard(rows: list[list[dict]]) -> list[dict]:
    """Оборачивает матрицу кнопок в attachments для send/edit."""
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


def settings_summary(settings: dict) -> str:
    model = settings.get("agent_model") or "coder"
    mode = settings.get("trigger_mode") or "orchestrator"
    tools = settings.get("selected_tools") or []
    tools_str = ", ".join(tools) if tools else "нет (только базовые)"
    name = settings.get("agent_name")
    name_str = name.strip() if (name and name.strip()) else "по умолчанию"
    return (
        "**Настройки агента для этого чата**\n\n"
        f"Имя: `{name_str}`\n"
        f"Модель: `{model}`\n"
        f"Триггер: `{mode}`\n"
        f"Инструменты: `{tools_str}`"
    )


def menu_root(is_admin: bool = False) -> list[dict]:
    rows = [
        [_btn("Модель", "max:menu:model")],
        [_btn("Инструменты", "max:menu:tools")],
        [_btn("Триггер", "max:menu:trigger")],
    ]
    rows.append([_btn("Закрыть", "max:close")])
    return keyboard(rows)


def menu_model(current) -> list[dict]:
    cur = current or "coder"
    rows = [[_btn(f"{'✓ ' if m == cur else ''}{m}", f"max:set:model:{m}")]
            for m in MAX_MODELS]
    rows.append([_btn("‹ Назад", "max:menu:root")])
    return keyboard(rows)


def menu_tools(selected, is_admin: bool = False) -> list[dict]:
    sel = set(selected or [])
    cats = list(MAX_TOOL_CATEGORIES)
    if is_admin:
        cats = cats + list(MAX_ADMIN_TOOL_CATEGORIES)
    rows = []
    for c in cats:
        box = "☑ " if c in sel else "☐ "
        label = f"{box}🔒 {c}" if c in MAX_ADMIN_TOOL_CATEGORIES else f"{box}{c}"
        rows.append([_btn(label, f"max:tool:{c}")])
    rows.append([_btn("‹ Назад", "max:menu:root")])
    return keyboard(rows)


def menu_trigger(current) -> list[dict]:
    cur = current or "orchestrator"
    rows = [[_btn(f"{'✓ ' if t == cur else ''}{t}", f"max:set:trigger:{t}")]
            for t in MAX_TRIGGERS]
    rows.append([_btn("‹ Назад", "max:menu:root")])
    return keyboard(rows)
