import hashlib
# from langchain.chat_models.gigachat import GigaChat
from .core.agent_provider import get_model
from langchain.schema import SystemMessage, HumanMessage
from .core.config import STORAGE_PATH, PATH_TO_GLOSSARY
from typing import Dict, Any, List
import re
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv


load_dotenv()

gigachat = get_model()


class SummaryGenerator:
    def __init__(self, digest_dir: str = "./digests"):
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.knowledge_dir = base_dir / STORAGE_PATH
        self.digest_dir = base_dir / digest_dir
        self.digest_dir.mkdir(exist_ok=True)


    def _get_content_hash(self, content: str) -> str:
        """Хэш содержимого для проверки изменений"""
        return hashlib.sha256(content.encode()).hexdigest()

    def _check_digest_exists(self, filename: str, content_hash: str) -> bool:
        """Проверить, есть ли уже дайджест для этой версии файла"""
        digest_file = self.digest_dir / f"{filename}.md"
        return digest_file.exists()

    def format_markdown(self, text: str) -> str:
        """Преобразует текст в структурированный Markdown"""

        # 1. Обнаруживаем заголовки по номерам пунктов
        text = re.sub(r'^\s*(\d+)\.\s*(.+)$', r'### \1. \2', text, flags=re.MULTILINE)

        # 2. Markdown-заголовки по ключевым словам
        patterns = [
            (r'^(\s*)(Цель:|Проблема:|Задачи:|Подход:|Реализация:|Пример:|Вывод:)', r'\1## \2'),
            (r'^(\s*)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+:)', r'\1### \2'),
        ]
        for pattern, repl in patterns:
            text = re.sub(pattern, repl, text, flags=re.MULTILINE)

        # 3. Нумерованные списки
        text = re.sub(r'^\s*(\d+\.)\s*(.+)$', r'\1 \2', text, flags=re.MULTILINE)

        # 4. Маркированные списки
        text = re.sub(r'^\s*[-*+]\s*(.+)$', r'- \1', text, flags=re.MULTILINE)

        # 5. Жирный для ключевых терминов
        text = re.sub(r'\b(агент|LLM|система|архитектура)\b', r'**\1**', text, flags=re.I)

        # 6. Кодовые блоки для JSON/кода
        text = re.sub(r'```json|```python|```', '```', text)

        return text.strip()

    def save_to_md(self, text: str, folder: Path, filename: str = None) -> str:
        """
        Сохраняет текст в Markdown-файл с автоформатированием.

        Args:
            text: Входной текст (строка или многострочный)
            filename: Имя файла (по умолчанию генерируется из даты/времени)
            folder: Папка для сохранения

        Returns:
            Полный путь к сохраненному файлу
        """
        # Создаем папку если нет
        folder_path = Path(folder)
        folder_path.mkdir(exist_ok=True)

        # Генерируем имя файла
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"document_{timestamp}.md"

        filepath = folder_path / filename

        # Автоформатирование Markdown
        formatted = self.format_markdown(text)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(formatted)

        # print(f"Сохранено: {filepath}")
        return str(filepath)

    def generate_summary(self, content: str, filename: str = None, theme: str = None, pdf_url: str = None) -> Dict[str, Any]:
        """Генерирует академическое саммари PDF-статей по строгому шаблону"""
        # НЕ забыть расскоментить =============================
        # final_text = find_relevant_glossary(content_[:4000], PATH_TO_GLOSSARY) # Закомментил

        # content_hash = self._get_content_hash(content_)

        # # Проверяем, есть ли уже дайджест
        # if self._check_digest_exists(filename, content_hash):
        #     digest_file = self.digest_dir / f"{filename}.md"
        #     return {"status": "exists", "digest_file": str(digest_file), "content_hash": content_hash}

        # try:
        # # Полный список категорий для промпта
        # categories = [
        #     "Общие принципы: определение PM",
        #     "Общие принципы: Стандарты PM",
        #     "Общие принципы: Фреймворки PM",
        #     "Общие принципы: Моделирование в PM",
        #     "Общие принципы: Неэффективности",
        #     "Методики исследования: CRISP_DM",
        #     "Методики исследования: PM Square",
        #     "PM-лог: Структура логов",
        #     "PM-лог: Форматы данных",
        #     "Проверка качества: Качество данных",
        #     "Проверка качества: Качество лога"
        # ]
        # categories_str = "\n".join(f"- {cat}" for cat in categories)

        # prompt = [SystemMessage(content=f"""Твоя задача: прочитать текст статьи из "{filename}", извлечь и адаптировать информацию для русскоязычного эксперта.

        # ## ГЛОССАРИЙ (обязательно используй для всех технических терминов из текста если необходимо):
        # {final_text}

        # ## ПРАВИЛА (строго соблюдай):
        # 1. Переводи для русскоязычного эксперта, сохраняя академический стиль, сохраняя точность терминов
        # 2. Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА, без домыслов
        # 3. Первый раз термин: русский (original). Далее: только русский
        # 4. Алгоритмы - пошагово с оригинальными названиями
        # 5. GitHub: найди в тексте ИЛИ проанализируй авторов/метод, иначе 'отсутствует'
        # 6. Если в статье нет какой-либо информации для шаблона - напиши 'не указано'
        # 7. Выводи результат строго по шаблону, без дополнительных комментариев и пояснений
        # 8. Не меняй порядок пунктов шаблона и не добавляй новые

        # ## Пример шаблона саммари:
        # "Название статьи на русском:",
        # "Название оригинальной статьи на английском:",
        # "Авторы:",
        # "Ссылка на источник статьи: {pdf_url}"
        # "Описание":
        #     1. **Цель:** ...
        #     2. **Проблема:** ...
        #     3. **Задачи:** ...
        #     4. **Подход - суть алгоритма/метода:** ...
        #     5. **Как реализуется подход:** ...
        #     6. **Пример из статьи:** ...
        #     7. **Вывод:** ...

        # ## Текст статьи из PDF:
        # {content_[:200]}.""")]
        print(f"КОНТЕКСТ 📄 {content[:500]}")
        SYSTEM_PROMT = f"""Твоя задача: прочитать текст статьи из "{filename}", извлечь и адаптировать информацию для русскоязычного эксперта.

        ## ПРАВИЛА (строго соблюдай):
        1. Переводи для русскоязычного эксперта, сохраняя академический стиль, , сохраняй точные термины в оригинале, например 'process mining' или названия инструментов: 'SOUP: Simplifying Event...' оставляй как есть в оригинале
        2. Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА, без домыслов
        3. Первый раз термин сокращенно: на original языке. Далее: термин на оригинальном языке, перевод и расшифровки на русском
        4. Алгоритмы - пошагово с оригинальными названиями
        5. GitHub: найди в тексте ИЛИ проанализируй авторов/метод, иначе 'отсутствует'
        6. Выводи результат строго по шаблону, без дополнительных комментариев и пояснений
        7. Не меняй порядок пунктов шаблона и не добавляй новые
        8. Не переводи авторов, оставляй в оригинале


        ## Пример шаблона саммари:
        "Название статьи на русском:",
        "Название оригинальной статьи на английском:",
        "Авторы на английском (в оригинале):",
        "Ссылка на источник статьи: {pdf_url}"
        "Описание":
            1. **Цель:** ...
            2. **Проблема:** ...
            3. **Задачи:** ...
            4. **Подход - суть алгоритма/метода:** ...
            5. **Как реализуется подход:** ...
            6. **Пример из статьи:** ...
            7. **Вывод:** ...

        ## Текст статьи из PDF:
        """

        response = gigachat.invoke([
                SystemMessage(content=SYSTEM_PROMT),
                HumanMessage(content=content[:3000])
            ])
        summary = response.content.strip()
        print("✨ Получили summary от модели", summary[:500])
        if theme is None:
            folder = self.digest_dir / 'Без категории'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
            return {
                "status": "created",
                "filename": filename,
                "created_at": datetime.now().isoformat(),
                "digest": summary,
                "category": theme,
                "digest_file": str(digest_file),  # Потом подумать над неймингом в message
                "digest_folder": folder.name
            }
        if 'Общие принципы' in theme:
            folder = self.digest_dir / 'Общие_принципы'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
        elif 'Методики исследования' in theme:
            folder = self.digest_dir / 'Методики_исследования'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
        elif 'PM-лог' in theme:
            folder = self.digest_dir / 'PM_лог'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
        elif 'категория не найдена' in theme:
            folder = self.digest_dir / 'Без категории'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
        else:
            folder = self.digest_dir / 'Проверка_качества'
            digest_file = self.save_to_md(summary, filename=filename, folder=folder)
        print("Сохранили в md")
        return {
            "status": "created",
            "filename": filename,
            "created_at": datetime.now().isoformat(),
            "digest": summary,
            "category": theme,
            "digest_file": str(digest_file),  # Потом подумать над неймингом в message
            "digest_folder": folder.name
        }
