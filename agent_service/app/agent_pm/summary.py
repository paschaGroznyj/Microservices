import hashlib
# from langchain.chat_models.gigachat import GigaChat
from .gigachat_manager import manager
from langchain.schema import SystemMessage
from core.config import GIGACHAT_CREDENTIALS, STORAGE_PATH, EMBED_MODEL_GLOSS, PATH_TO_GLOSSARY
from typing import Dict, Any, List
import json
import re
import os
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from dotenv import load_dotenv


load_dotenv()

HF_TOKEN = os.getenv("HF_TOKEN")

# from .glossary import find_relevant_glossary
#
# EMBED_MODEL = SentenceTransformer(EMBED_MODEL_GLOSS)


class SummaryGenerator:
    def __init__(self, digest_dir: str = "./digests"):
        base_dir = Path(__file__).resolve().parent  # final_agent
        self.knowledge_dir = base_dir / STORAGE_PATH
        self.digest_dir = base_dir / digest_dir
        self.digest_dir.mkdir(exist_ok=True)
        # self.llm = GigaChat(
        #     credentials=GIGACHAT_CREDENTIALS,  # Обязательный параметр
        #     verify_ssl_certs=False,  # Отключаем проверку SSL
        # )
        # self.llm = InferenceClient(api_key=HF_TOKEN)

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

    def generate_summary(self, content_: str, filename: str = None, theme: str = None, pdf_url: str = None) -> Dict[str, Any]:
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

        prompt = [SystemMessage(content=f"""Твоя задача: прочитать текст статьи из "{filename}", извлечь и адаптировать информацию для русскоязычного эксперта.

        ## ПРАВИЛА (строго соблюдай):
        1. Переводи для русскоязычного эксперта, сохраняя академический стиль, сохраняя точность терминов
        2. Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА, без домыслов
        3. Первый раз термин: русский (original). Далее: только русский
        4. Алгоритмы - пошагово с оригинальными названиями
        5. GitHub: найди в тексте ИЛИ проанализируй авторов/метод, иначе 'отсутствует'
        6. Если в статье нет какой-либо информации для шаблона - напиши 'не указано'
        7. Выводи результат строго по шаблону, без дополнительных комментариев и пояснений
        8. Не меняй порядок пунктов шаблона и не добавляй новые

        ## Пример шаблона саммари:
        "Название статьи на русском:",
        "Название оригинальной статьи на английском:",
        "Авторы:",
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
        {content_[:1500]}.""")]

        # prompt2 = [SystemMessage(content=f"""Выбери одну категорию из следующего списка:
        #                                     Категории: {categories_str}
        #                                     Выбирай наиболее релевантную категорию к которой ты можешь отнести следующий текст:

        #                                     ## Текст статьи из PDF:
        #                                     {content_[:500]}.
        #                                     В ответ выводи только выбранную категорию, без лишнего текста.""")]
        # =====================================
        # response = self.llm.chat.completions.create(
        #     model="Qwen/Qwen3.5-397B-A17B",
        #     messages=[{"role": "user", "content": prompt}],
        #     temperature=0.6,
        #     top_p=0.95
        # )
        # summary = response.choices[0].message.content
        # =====================================
        response_sum = manager.invoke(prompt)
        # response_sum = self.llm(prompt) # Для гигачата
        summary = response_sum.content.strip()

        # response_theme = self.llm(prompt2)
        # theme = response_theme.content.strip()

        # with open(self.knowledge_dir / "metadata.json", 'r', encoding='utf-8') as f:
        #     data_list = json.load(f)

        # for dl in data_list:
        #     filename_meta = f"{dl['data_sub']}_{dl['title']}.md"
        #     if filename == filename_meta:
        #         dl['category'] = theme

        # with open(self.knowledge_dir / "metadata.json", 'w', encoding='utf-8') as f:
        #     json.dump(data_list, f, ensure_ascii=False, indent=2)
        print("Получили summary", summary[:200])
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

        # except Exception as e:
        #     return {
        #         "status": "error",
        #         "message": f"Ошибка: {str(e)[:100]}",
        #         "content_hash": content_hash
        #     }

    # def get_new_articles_digests(self, articles_path: Path) -> List[Dict]:
    #     """Создать дайджесты только для новых/измененных статей"""
    #     new_digests = []

    #     for md_file in articles_path.rglob("*.md"):
    #         filename = md_file.name
    #         try:
    #             with open(md_file, 'r', encoding='utf-8') as f:
    #                 content = f.read()

    #             result = self.generate_summary(content, filename)
    #             new_digests.append({
    #                 "filename": filename,
    #                 "result": result
    #             })

    #         except Exception as e:
    #             new_digests.append({
    #                 "filename": filename,
    #                 "error": str(e)
    #             })

    #     return new_digests