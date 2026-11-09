"""analyze_image: ответ модели с thinking-блоками превращается в чистый текст."""
from agent.tools.vision.tool_analyze_image import _content_text


def test_string_passes_through():
    assert _content_text("Скриншот меню") == "Скриншот меню"


def test_thinking_blocks_dropped():
    content = [
        {"type": "thinking", "thinking": "Looking closely...", "signature": None},
        {"type": "text", "text": "На скриншоте меню «Фигуры»."},
    ]
    out = _content_text(content)
    assert out == "На скриншоте меню «Фигуры»."
    assert "Looking closely" not in out
    assert out.strip() == out  # attach_description делает .strip()


def test_several_text_blocks_joined():
    content = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    assert _content_text(content) == "A\nB"


def test_none_and_only_thinking():
    assert _content_text(None) == ""
    assert _content_text([{"type": "thinking", "thinking": "x"}]) == ""
