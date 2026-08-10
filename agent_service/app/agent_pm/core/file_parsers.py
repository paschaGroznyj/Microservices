"""
agent_service/core/file_parsers.py
────────────────────────────────────
Парсеры файлов из бинарного содержимого.
Используется в эндпоинте /api/tg/file.

Поддерживаемые форматы:
  .pdf            — PyPDF2 (первые N страниц)
  .docx           — python-docx
  .txt .md .csv и прочий текст — прямо как UTF-8
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

log = logging.getLogger(__name__)

# Расширения которые читаем как plain text
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".html", ".htm", ".sql", ".sh", ".log", ".py", ".js", ".ts",
}

PDF_MAX_PAGES  = 40    # сколько страниц PDF читать
DOCX_MAX_PARAS = 200  # сколько параграфов docx читать


def parse_file(filename: str, content: bytes) -> str:
    """
    Универсальный парсер. Принимает имя файла и байты,
    возвращает извлечённый текст или сообщение об ошибке.
    """
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        return _parse_pdf(content)
    elif ext == ".docx":
        return _parse_docx(content)
    elif ext in TEXT_EXTS:
        return _parse_text(content)
    else:
        return f"[Бинарный файл {filename!r} — формат не поддерживается для извлечения текста]"


def _parse_pdf(content: bytes) -> str:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return "[PDF] PyPDF2 не установлен: pip install PyPDF2"

    try:
        reader = PdfReader(BytesIO(content))
        pages  = min(len(reader.pages), PDF_MAX_PAGES)
        parts  = []
        for i in range(pages):
            text = reader.pages[i].extract_text()
            if text:
                parts.append(text.strip())
        result = "\n\n".join(parts)
        truncated = f"\n\n... (показано {pages} из {len(reader.pages)} страниц)" \
                    if len(reader.pages) > PDF_MAX_PAGES else ""
        return result + truncated if result else "[PDF] Текст не удалось извлечь (возможно, скан)"
    except Exception as e:
        log.warning("PDF parse error: %s", e)
        return f"[PDF] Ошибка парсинга: {e}"


def _parse_docx(content: bytes) -> str:
    try:
        from docx import Document
    except ImportError:
        return "[DOCX] python-docx не установлен: pip install python-docx"

    try:
        doc    = Document(BytesIO(content))
        paras  = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        shown  = paras[:DOCX_MAX_PARAS]
        result = "\n\n".join(shown)
        truncated = f"\n\n... (показано {len(shown)} из {len(paras)} параграфов)" \
                    if len(paras) > DOCX_MAX_PARAS else ""
        return result + truncated if result else "[DOCX] Документ пуст"
    except Exception as e:
        log.warning("DOCX parse error: %s", e)
        return f"[DOCX] Ошибка парсинга: {e}"


def _parse_text(content: bytes) -> str:
    try:
        return content.decode("utf-8", errors="replace")
    except Exception as e:
        return f"[TEXT] Ошибка чтения: {e}"