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
MAX_MODEL_LABELS = {"coder": "GPT", "designer": "Claude", "universal": "GLM", "gemini": "Gemini"}
MAX_TRIGGERS = ["mention", "orchestrator"]
MAX_TOOL_CATEGORIES = ["internet", "content", "sessions"]
# Платные/админские категории — видны только админу (аналог TG_ADMIN_TOOL_CATEGORIES).
MAX_ADMIN_TOOL_CATEGORIES = ["media_gen", "admin"]


def _btn(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def keyboard(rows: list[list[dict]]) -> list[dict]:
    """Оборачивает матрицу кнопок в attachments для send/edit."""
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


def settings_summary(settings: dict) -> str:
    model = settings.get("agent_model") or "coder"
    model_label = MAX_MODEL_LABELS.get(model, model)
    mode = settings.get("trigger_mode") or "orchestrator"
    tools = settings.get("selected_tools") or []
    tools_str = ", ".join(tools) if tools else "нет (только базовые)"
    name = settings.get("agent_name")
    name_str = name.strip() if (name and name.strip()) else "по умолчанию"
    return (
        "**Настройки агента для этого чата**\n\n"
        f"Имя: `{name_str}`\n"
        f"Модель: `{model_label}`\n"
        f"Триггер: `{mode}`\n"
        f"Инструменты: `{tools_str}`"
    )


def menu_root(is_admin: bool = False) -> list[dict]:
    rows = [
        [_btn("Имя агента", "max:menu:name")],
        [_btn("Модель", "max:menu:model")],
        [_btn("Инструменты", "max:menu:tools")],
        [_btn("Триггер", "max:menu:trigger")],
        [_btn("Будильники", "max:menu:alarms")],
        [_btn("\U0001f4ac Сессии", "max:sess:list:0")],
    ]
    rows.append([_btn("Закрыть", "max:close")])
    return keyboard(rows)


def menu_model(current) -> list[dict]:
    cur = current or "coder"
    rows = [[_btn(f"{'✓ ' if m == cur else ''}{MAX_MODEL_LABELS.get(m, m)}", f"max:set:model:{m}")]
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


MAX_SESS_PAGE = 6


def _session_title(item: dict) -> str:
    t = " ".join(str(item.get("title") or "").split())
    if len(t) > 38:
        t = t[:37] + "\u2026"
    return t or f"\u0421\u0435\u0441\u0441\u0438\u044f {item.get('id')}"


def menu_sessions(items: list[dict], page: int, active_id) -> list[dict]:
    """\u041c\u0435\u043d\u044e \u0441\u043f\u0438\u0441\u043a\u0430 \u0441\u0435\u0441\u0441\u0438\u0439: \u043d\u043e\u0432\u0430\u044f + \u0434\u043e MAX_SESS_PAGE \u043a\u043d\u043e\u043f\u043e\u043a + \u043f\u0430\u0433\u0438\u043d\u0430\u0446\u0438\u044f."""
    total = len(items)
    start = page * MAX_SESS_PAGE
    chunk = items[start:start + MAX_SESS_PAGE]
    rows = [[_btn("\u2795 \u041d\u043e\u0432\u0430\u044f \u0441\u0435\u0441\u0441\u0438\u044f", "max:sess:new")]]
    for it in chunk:
        mark = "\u2713 " if str(it.get("id")) == str(active_id) else ""
        rows.append([_btn(f"{mark}{_session_title(it)}",
                          f"max:sess:open:{it.get('id')}")])
    nav = []
    if page > 0:
        nav.append(_btn("\u2b05\ufe0f", f"max:sess:list:{page-1}"))
    if start + MAX_SESS_PAGE < total:
        nav.append(_btn("\u27a1\ufe0f", f"max:sess:list:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("\u2039 \u041d\u0430\u0437\u0430\u0434", "max:menu:root")])
    return keyboard(rows)
