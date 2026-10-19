import pytest

from menu import (
    MAX_ADMIN_TOOL_CATEGORIES,
    MAX_MODELS,
    MAX_TOOL_CATEGORIES,
    MAX_TRIGGERS,
    _btn,
    keyboard,
    menu_model,
    menu_root,
    menu_tools,
    menu_trigger,
    settings_summary,
)

def test_btn_builds_callback_button():
    assert _btn("Привет", "max:x") == {
        "type": "callback", "text": "Привет", "payload": "max:x"
    }

#list[list[dict]]
def test_keyboard_wraps_rows_into_attachment():
    rows = [[{"A": "a"}], [{"B": "b"}]]
    assert keyboard(rows) == [{"type": "inline_keyboard", "payload": {"buttons": rows}}]


def test_summary_defaults_for_empty_settings():
    text = settings_summary({})
    assert "Имя: `по умолчанию`" in text
    assert "Модель: `coder`" in text
    assert "Триггер: `orchestrator`" in text
    assert "Инструменты: `нет (только базовые)`" in text


def test_summary_with_filled_settings():
    text = settings_summary({
        "agent_name": "  Макс  ",
        "agent_model": "gemini",
        "trigger_mode": "mention",
        "selected_tools": ["internet", "content"],
    })
    assert "Имя: `Макс`" in text        
    assert "Модель: `gemini`" in text
    assert "Триггер: `mention`" in text
    assert "internet, content" in text


@pytest.mark.parametrize("name", [None, "", "   "])
def test_summary_blank_name_means_default(name):
    assert "по умолчанию" in settings_summary({"agent_name": name})

# ---------- menu_model ----------

def buttons(attachments):
    """Достаёт плоский список кнопок из результата keyboard()."""
    return [b for row in attachments[0]["payload"]["buttons"] for b in row]

def test_menu_model_marks_only_current():
    texts = [b["text"] for b in buttons(menu_model("gemini"))]
    assert "✓ gemini" in texts
    assert sum(t.startswith("✓") for t in texts) == 1


def test_menu_model_none_falls_back_to_coder():
    texts = [b["text"] for b in buttons(menu_model(None))]
    assert "✓ coder" in texts


def test_menu_model_has_all_models_and_back_button():
    payloads = [b["payload"] for b in buttons(menu_model("coder"))]
    for m in MAX_MODELS:
        assert f"max:set:model:{m}" in payloads
    assert payloads[-1] == "max:menu:root"


# ---------- menu_tools ----------

def test_menu_tools_hides_admin_categories_for_regular_user():
    payloads = [b["payload"] for b in buttons(menu_tools([], is_admin=False))]
    for c in MAX_ADMIN_TOOL_CATEGORIES:
        assert f"max:tool:{c}" not in payloads


def test_menu_tools_shows_admin_categories_with_lock_for_admin():
    btns = buttons(menu_tools([], is_admin=True))
    admin_btns = [b for b in btns if b["payload"] in
                  {f"max:tool:{c}" for c in MAX_ADMIN_TOOL_CATEGORIES}]
    assert len(admin_btns) == len(MAX_ADMIN_TOOL_CATEGORIES)
    assert all("🔒" in b["text"] for b in admin_btns)


def test_menu_tools_checkboxes_reflect_selection():
    btns = {b["payload"]: b["text"]
            for b in buttons(menu_tools(["internet"]))}
    assert btns["max:tool:internet"].startswith("☑")
    assert btns["max:tool:content"].startswith("☐")


# ---------- инвариант для ВСЕХ меню ----------

@pytest.mark.parametrize("attachments", [
    menu_root(),
    menu_model("coder"),
    menu_tools(["internet"], is_admin=True),
    menu_trigger("mention"),
])
def test_all_payloads_fit_max_limit(attachments):
    for b in buttons(attachments):
        assert len(b["payload"].encode("utf-8")) <= 64

