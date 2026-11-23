"""Route provider content deltas without exposing private <reasoning> blocks.

Only ambiguous tag suffixes are held; tool-call arguments never enter this router.
"""
import re

_TAG = re.compile(r'<\s*(/?)\s*(thinking|reasoning)\s*>', re.I)


class ContentStreamRouter:
    def __init__(self):
        self.buffer = ''
        self.stack = []
        self.answer_text = ''
        self.thinking_text = ''
        self.private_text = ''
        self.hidden_unclosed = False
        self.envelope = False
        self.started = False

    @staticmethod
    def _tag_prefix(text):
        """Return None while a legacy opening tag is still ambiguous."""
        text = text.lower()
        openings = ('<', r'\<', '&lt;', '&#60;', '&#x3c;')
        for opening in openings:
            if text.startswith(opening):
                tail = re.sub(r'\s+', '', text[len(opening):])
                for name in ('thinking', 'reasoning', '/thinking', '/reasoning'):
                    if name.startswith(tail):
                        return None
                    if tail.startswith(name):
                        end = tail[len(name):]
                        for closing in ('>', r'\>', '&gt;', '&#62;', '&#x3e;'):
                            if end.startswith(closing):
                                return True
                            if closing.startswith(end):
                                return None
                return False
        return None if any(o.startswith(text) for o in openings) else False

    def _classify_prefix(self):
        start = self.buffer.lstrip()
        if not start:
            return None
        if start.startswith('`'):
            ticks = len(start) - len(start.lstrip('`'))
            content = start[ticks:].lstrip()
            if not content:
                return None
            if ticks >= 3:
                # Wait for the first content token, not the entire fenced block.
                language = re.match(r'[\w+.-]+', content)
                if language:
                    content = content[language.end():].lstrip()
                    if not content:
                        return None
            return self._tag_prefix(content)
        if start.startswith(('&', '\\')):
            return self._tag_prefix(start)
        return False

    def feed(self, text):
        self.buffer += text
        if not self.started:
            classification = self._classify_prefix()
            if classification is None:
                return []
            self.envelope = classification
            self.started = True
        if self.envelope:
            return []
        return self._drain(False)

    def flush(self):
        if not self.started and self.buffer.strip():
            self.envelope = self._classify_prefix() is not False
        if self.envelope:
            # Authoritative extraction is performed by the existing parser.
            self.buffer = ''
            return []
        result = self._drain(True)
        self.hidden_unclosed = 'reasoning' in self.stack
        return result

    def _drain(self, final):
        result = []
        while self.buffer:
            match = _TAG.search(self.buffer)
            if match:
                self._emit(self.buffer[:match.start()], result)
                close, tag = match.groups()
                tag = tag.lower()
                if close:
                    if self.stack and self.stack[-1] == tag:
                        self.stack.pop()
                    # Malformed outer closes cannot terminate a nested private scope.
                else:
                    self.stack.append(tag)
                self.buffer = self.buffer[match.end():]
                continue
            cut = len(self.buffer)
            pos = self.buffer.rfind('<')
            if pos >= 0:
                tail = re.sub(r'\s+', '', self.buffer[pos:]).lower()
                if any(t.startswith(tail) for t in ('<thinking>', '</thinking>', '<reasoning>', '</reasoning>')):
                    if not final:
                        cut = pos
                    elif len(tail) > 1:
                        # Incomplete control tag is not user-facing text.
                        self.buffer = self.buffer[:pos]
                        cut = pos
            self._emit(self.buffer[:cut], result)
            self.buffer = self.buffer[cut:]
            break
        return result

    def _emit(self, text, result):
        if not text:
            return
        if 'reasoning' in self.stack:
            self.private_text += text
            return
        channel = 'reasoning' if 'thinking' in self.stack else 'answer'
        result.append((channel, text))
        if channel == 'answer':
            self.answer_text += text
        else:
            self.thinking_text += text
