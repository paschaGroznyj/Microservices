"""ReasoningCleaner: markdown из reasoning убирается, текст не слипается."""
import pytest

from agent.core.reasoning_clean import ReasoningCleaner, clean_reasoning

# Начало реального стрима GLM-5.2 через cloud.ru (23.11.2026): куски по 1–3 слова,
# пробелы и переносы стоят на стыках, ** разрезаны между чанками.
GLM52_CHUNKS = ["1", ". ", " **Analyze", " the Request:", "**\n    *", "   **Topic", ":** Why", " the sky is", " blue.\n   ", " *  ", " **Format:**", " 3 short", " paragraphs.\n   ", " *  ", " **Pre-re", "quisite:** Create", " a bul", "leted list plan", " *", "before* the", " answer.\n   ", " *  ", " **Language:**", " Russian.\n\n2", ". ", " **Draft", "ing the Plan", " (", "Bulleted", " List):**\n", "    *  ", " Need to", " cover the", " main physical concepts", ":", " sunlight", " composition", ", Ray", "leigh scattering,", " human", " eye perception.\n", "    *  ", " *Plan:", "*\n       ", " *   С", "остав солн", "ечного света", " (спект", "р цветов", ").\n       ", " *   Расс", "еяние", " Рэ", "лея в", " атмосф", "ере Зем", "ли.\n", "        *  ", " Воспри", "ятие глаз", "ом именно", " голубого цвета", ".\n\n3.", "  **Draft"]
# GLM-5.3: обычный текст, почти без разметки
GLM53_CHUNKS = ["The", " user is", " asking in Russian to", " explain why the sky", " is blue in", " 3 short paragraphs", ", and to", " make", " a plan as", " a list before answering", ".\n\nLet me think", " about this:", "\n\n1. Why the", " sky is blue -", " Rayleigh scattering.", " Sunlight contains", " all colors of the", " spectrum. When light", " enters", " Earth", "'s atmosphere, it", " coll", "ides with gas", " molecules (nitrogen", ", oxygen). Short", "er wavelengths (blue", ", violet) scatter", " more than", " longer wavelengths (red", ", orange). The", " scattering intensity is invers", "ely proportional to the", " fourth power of wavelength", " (Ray", "leigh law", "). Blue light", " scatters about", " 4 times", " more than red light", ".\n\n2. Why"]


def _stream(chunks):
    c = ReasoningCleaner()
    return "".join(c.feed(x) for x in chunks) + c.flush()


def test_glm52_stream_keeps_spaces_and_lines():
    out = _stream(GLM52_CHUNKS)
    assert "**" not in out and "*" not in out
    assert out.startswith("1. Analyze the Request:\n◦ Topic: Why the sky is blue.\n")
    assert "◦ Pre-requisite: Create a bulleted list plan before the answer.\n" in out
    assert "\n\n2. Drafting the Plan (Bulleted List):\n" in out
    assert "◦ Состав солнечного света (спектр цветов).\n" in out
    # слова исходника не склеились и не потерялись: тот же набор слов в том же порядке
    raw_words = "".join(GLM52_CHUNKS).replace("*", " ").split()
    assert [w for w in out.split() if w not in ("•", "◦")] == raw_words


def test_glm53_plain_text_unchanged():
    raw = "".join(GLM53_CHUNKS)
    assert _stream(GLM53_CHUNKS).rstrip() == raw.rstrip()


@pytest.mark.parametrize("chunks", [GLM52_CHUNKS, GLM53_CHUNKS])
def test_stream_equals_whole_text(chunks):
    raw = "".join(chunks)
    assert _stream(chunks).rstrip() == clean_reasoning(raw)
    # и при любой нарезке, вплоть до посимвольной
    assert _stream(list(raw)).rstrip() == clean_reasoning(raw)


@pytest.mark.parametrize("raw, expected", [
    ("**Setting up gap enumeration****Summing coefficient cases**",
     "Setting up gap enumeration\nSumming coefficient cases"),
    ("Done.**Next step**", "Done.\nNext step"),
    ("This is **bold** inside.", "This is bold inside."),
    ("# Plan\n## Step one", "Plan\nStep one"),
    ("- a\n* b\n+ c\n  - nested", "• a\n• b\n• c\n◦ nested"),
    ("1) first\n10. tenth", "1) first\n10. tenth"),
    ("Use `foo()` here", "Use foo() here"),
    ("```python\nprint(1)\n```\nafter", "print(1)\nafter"),
    ("a<br>b<strong>c</strong></p>d", "a\nbc\nd"),
    ("List<int> and <T> and a < b", "List<int> and <T> and a < b"),
    ("2*3 = 6, a * b, *italic*", "2*3 = 6, a * b, italic"),
    ("\n\n  lead\n\n\n\nnext", "lead\n\nnext"),
    ("Title\n=====\nBody\n---\n***\nEnd", "Title\nBody\nEnd"),
    ("> quoted\n>> deep", "quoted\ndeep"),
    ("a - b\n- item", "a - b\n• item"),
    ("", ""),
])
def test_clean_cases(raw, expected):
    assert clean_reasoning(raw) == expected
    assert _stream(list(raw)).rstrip() == expected


def test_feed_holds_only_ambiguous_tail():
    c = ReasoningCleaner()
    assert c.feed("Hello ") == "Hello "
    assert c.feed("world *") == "world "     # «*» ждёт следующий символ
    assert c.feed("*bold**") == "bold"
    assert c.feed(" done.\n- ") == " done.\n"  # маркер списка ждёт текст
    assert c.feed("item") == "• item"
    assert c.flush() == ""
