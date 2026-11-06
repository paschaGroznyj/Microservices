"""
api_gateway/file_parsers.py
───────────────────────────
Политика вложений: решает на gateway, пойдёт ли файл ТЕКСТОМ в промпт
модели или ФАЙЛОМ в media_store (Redis) агента.

Agent_service ничего не парсит: он получает от gateway готовый режим
для каждого файла (`file_modes`) и только раскладывает байты.

Правила:
  • картинки, видео, аудио, pdf, docx, xlsx и любые бинарники -> "file";
  • текстовые расширения (TEXT_EXTS) читаются как UTF-8:
      - не декодируется (это не текст)        -> "file";
      - длиннее INLINE_MAX_CHARS символов     -> "file";
      - пользователь сам нажал «Отправить файлом» (force_file) -> "file";
      - иначе                                 -> "inline" (текст в промпт).
  • размер одного файла — не больше MAX_UPLOAD_BYTES (50 МБ), проверяется
    в эндпоинте при чтении, до форварда в agent_service.
"""

from __future__ import annotations

from pathlib import Path

MODE_INLINE = "inline"
MODE_FILE = "file"

# Текст длиннее — в контекст модели не кладём, только файлом.
INLINE_MAX_CHARS = 10_000

# Лимит на один файл на gateway (дублирует фронтовый лимит file-attach.js).
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# Расширения, которые gateway вправе читать как plain text.
TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".html", ".htm", ".css", ".scss", ".sql", ".sh", ".bash", ".log",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go",
    ".rs", ".c", ".h", ".cpp", ".hpp", ".cs", ".php", ".rb", ".swift",
    ".vue", ".svelte", ".dockerfile", ".gitignore",
})


def is_text_ext(filename: str) -> bool:
    name = (filename or "").lower()
    ext = Path(name).suffix
    if ext in TEXT_EXTS:
        return True
    # Файлы без расширения с «говорящим» именем.
    return Path(name).name in {"dockerfile", "makefile", "readme", "license"}


def decode_text(content: bytes) -> str | None:
    """UTF-8 (с BOM) -> str. None, если это не текст."""
    if b"\x00" in content[:8192]:
        return None
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def classify(filename: str, content: bytes, force_file: bool = False) -> tuple[str, int | None]:
    """Возвращает (mode, chars).

    chars — длина текста в символах для текстовых файлов, иначе None.
    """
    if not is_text_ext(filename):
        return MODE_FILE, None
    text = decode_text(content)
    if text is None:
        return MODE_FILE, None
    chars = len(text)
    if force_file or chars > INLINE_MAX_CHARS:
        return MODE_FILE, chars
    return MODE_INLINE, chars
