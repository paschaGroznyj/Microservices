"""Очистка reasoning от markdown/HTML без склейки текста.

Провайдеры шлют reasoning кусками по 1–3 слова (GLM: " **Analyze", " the Request:").
Если чистить каждый кусок отдельно через strip()/regex, пропадают пробелы и
переносы на стыках, а маркеры, разрезанные между чанками (``*`` + ``*``), проходят.
Поэтому чистка потоковая: ``ReasoningCleaner`` держит состояние строки и придерживает
только хвост, по которому ещё нельзя решить (``*`` в конце, ``1.`` в начале строки,
незакрытый ``<tag``). Пробелы между словами и переносы строк сохраняются.

Что делает:
- ``**жирный**``, ``*курсив*``, `` `код` `` → текст без маркеров;
- ``# Заголовок`` → ``Заголовок``; ``* пункт`` / ``- пункт`` → ``• пункт``, вложенный → ``◦ пункт``
  (отступы в начале строки убираются, иначе marked на фронте сделает из них блок кода);
- строки-ограды ````` ``` ````` и строки только из ``= - _ *`` (setext, ``<hr>``) выбрасываются,
  содержимое оград остаётся; ``> цитата`` → ``цитата``;
- ``<br>``, ``</p>``, ``</div>``, ``</li>`` → перенос, прочие известные HTML-теги удаляются,
  остальные ``<...>`` (``List<int>``, ``<T>``) остаются текстом;
- ``**A****B**`` и ``текст.**Заголовок**`` разводятся переносом (так GPT склеивает
  заголовки summary-частей);
- больше одной пустой строки подряд не бывает, пустые строки в начале убираются.
"""
from __future__ import annotations

import re

_HOLD_PREFIX = re.compile(r"[ \t]*(?:>[ \t]?)*(?:#{1,6}|[*+-]|\d{1,3}[.)]?)?[ \t]*`{0,2}")
# Строка только из = - _ * : setext-подчёркивание или <hr>, marked сделал бы из неё заголовок/линию
_RULE = re.compile(r"[ \t]*(?:[=\-_*][ \t]*)+")
_PREFIX = re.compile(
    r"(?P<indent>[ \t]*)(?:>[ \t]?)*"
    r"(?:(?P<head>#{1,6})|(?P<bullet>[*+-])|(?P<num>\d{1,3}[.)]))?"
    r"(?(head)[ \t]+|(?(bullet)[ \t]+|(?(num)[ \t]+|)))"
)
_FENCE = ("```", "~~~")
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)\b[^<>\n]{0,200}?/?>")
_TAG_PARTIAL = re.compile(r"</?(?:[A-Za-z][A-Za-z0-9]*\b[^<>\n]{0,200})?")
_HTML_TAGS = {
    "a", "b", "i", "u", "s", "em", "strong", "code", "pre", "span", "div", "p",
    "br", "hr", "ul", "ol", "li", "small", "sup", "sub", "mark", "del", "ins",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "table", "tr", "td", "th",
}
_BREAK_TAGS = {"br", "hr", "p", "div", "li", "tr", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
_PLAIN = re.compile(r"[^\n*`<]+")
_SENTENCE_END = set(".!?:;)»\"")


class ReasoningCleaner:
    """Потоковый очиститель: ``feed()`` на каждый чанк, ``flush()`` в конце шага."""

    def __init__(self) -> None:
        self._buf = ""
        self._line_start = True
        self._bold = False
        self._bold_closed = False
        self._last = ""
        self._nl = 0
        self._started = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._buf += text
        return self._drain(final=False)

    def flush(self) -> str:
        out = self._drain(final=True)
        self.__init__()
        return out

    # ── вывод ──────────────────────────────────────────────────────
    def _put(self, out: list, text: str) -> None:
        if not text:
            return
        out.append(text)
        self._last = text[-1]
        self._nl = 0
        self._started = True
        self._bold_closed = False

    def _newline(self, out: list) -> None:
        if self._started and self._nl < 2:
            out.append("\n")
            self._nl += 1
            self._last = "\n"
        self._line_start = True
        self._bold = False

    # ── разбор ─────────────────────────────────────────────────────
    def _drain(self, final: bool) -> str:
        s, i, n, out = self._buf, 0, len(self._buf), []
        while i < n:
            if self._line_start:
                rest = s[i:]
                eol = rest.find("\n")
                line = rest if eol < 0 else rest[:eol]
                if line and _RULE.fullmatch(line):
                    if eol < 0 and not final:
                        break
                    i = n if eol < 0 else i + eol + 1
                    continue
                if not final and _HOLD_PREFIX.fullmatch(rest):
                    break
                m = _PREFIX.match(s, i)
                j = m.end()
                if s.startswith(_FENCE, j):
                    nl = s.find("\n", j)
                    if nl < 0:
                        if not final:
                            break
                        i = n
                        continue
                    i = nl + 1
                    continue
                self._line_start = False
                i = j
                if j < n and s[j] == "\n":
                    continue
                # Отступ не переносим: фронт рендерит reasoning через marked,
                # и строка с 4+ пробелами стала бы блоком кода.
                nested = len(m.group("indent").replace("\t", "    ")) >= 2
                if m.group("bullet"):
                    self._put(out, "◦ " if nested else "• ")
                elif m.group("num"):
                    self._put(out, m.group("num") + " ")
                continue

            c = s[i]
            if c == "\n":
                self._newline(out)
                i += 1
            elif c == "*":
                if i + 1 >= n and not final:
                    break
                nxt = s[i + 1] if i + 1 < n else ""
                if nxt == "*":
                    if not self._bold:
                        if self._started and self._nl == 0 and (
                            self._bold_closed or self._last in _SENTENCE_END
                        ):
                            self._newline(out)
                            self._line_start = False
                        self._bold = True
                    else:
                        self._bold = False
                        self._bold_closed = True
                    i += 2
                else:
                    prev = self._last
                    keep = (prev.isdigit() and nxt.isdigit()) or (prev in (" ", "\t") and nxt in (" ", "\t"))
                    if keep:
                        self._put(out, "*")
                    i += 1
            elif c == "`":
                i += 1
            elif c == "<":
                m = _TAG.match(s, i)
                if m and m.group(2).lower() in _HTML_TAGS:
                    name = m.group(2).lower()
                    if name in _BREAK_TAGS and (name in ("br", "hr") or m.group(1)):
                        self._newline(out)
                    i = m.end()
                elif not m and not final and _TAG_PARTIAL.fullmatch(s, i):
                    break
                else:
                    self._put(out, "<")
                    i += 1
            else:
                m = _PLAIN.match(s, i)
                self._put(out, m.group(0))
                i = m.end()
        self._buf = s[i:]
        return "".join(out)


def clean_reasoning(text: str) -> str:
    """Чистит готовый (склеенный) текст reasoning целиком."""
    if not text:
        return ""
    cleaner = ReasoningCleaner()
    return (cleaner.feed(text) + cleaner.flush()).rstrip()
