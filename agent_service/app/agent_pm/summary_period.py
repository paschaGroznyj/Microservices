"""
Универсальный модуль дайджестов с LLM-парсингом и СОСТАВНЫМИ ФИЛЬТРАМИ
LLM выделяет ВСЕ параметры (дата+тема+категория) и применяет их КОМБИНИРОВАННО
"""
import re
import json
import numpy as np
from pathlib import Path
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import cosine_similarity
from sentence_transformers import SentenceTransformer
from .core.config import EMBEDDING_MODEL
from .core.agent_provider import get_model
from langchain.schema import SystemMessage

# =============================================================================
# 1. РАСШИРЕННАЯ СТРУКТУРА ДЛЯ СОСТАВНЫХ ФИЛЬТРОВ
# =============================================================================

EMBED_MODEL = SentenceTransformer(EMBEDDING_MODEL)

class CompositeFilters(BaseModel):
    """Составные фильтры: можно комбинировать date+topic, category+date и т.д."""
    date_range: Optional[Dict[str, str]] = Field(None, description="{'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'}")
    category: Optional[str] = Field(None)
    topic: Optional[str] = Field(None)
    title: Optional[str] = Field(None)
    threshold: float = Field(0.3, description="Порог семантики")
    limit: int = Field(50)
    
    @property
    def has_date(self) -> bool:
        return self.date_range is not None
    
    @property 
    def has_topic(self) -> bool:
        return bool(self.topic)
    
    @property
    def has_category(self) -> bool:
        return bool(self.category)
    
    @property
    def is_composite(self) -> bool:
        """True если несколько фильтров активны."""
        count = sum([self.has_date, self.has_topic, self.has_category, bool(self.title)])
        return count > 1

    def to_slug(self) -> str:
        """
        Компактная строка фильтров для имени файла
        """
        parts = []

        if self.date_range:
            parts.append(
                f"date_{self.date_range['start_date']}_{self.date_range['end_date']}"
            )

        if self.category:
            parts.append(f"cat_{_slugify(self.category)}")

        if self.topic:
            parts.append(f"topic_{_slugify(self.topic)}")

        if self.title:
            parts.append(f"title_{_slugify(self.title)}")

        return "__".join(parts) if parts else "all"

def _slugify(text: str) -> str:
    return (
        text.lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace(":", "")
    )


def extract_json(text: str) -> str:
    text = text.strip()

    # ```json ... ```
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    # ``` ... ```
    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    if text.startswith("json"):
        return text[4:].strip()

    return text

class LLMCompositeExtractor:
    """LLM извлекает ВСЕ параметры одновременно."""
    @staticmethod
    def extract_composite_filters(query: str, llm_client) -> CompositeFilters:
        """
        LLM анализирует запрос и выделяет ВСЕ параметры сразу.
        Поддерживает составные запросы: "pm4py за ноябрь", "CRISP_DM + ML 2025"
        """
        prompt = f"""
        Проанализируй запрос дайджеста: "{query}"

        Извлеки ВСЕ параметры фильтрации одновременно в JSON CompositeFilters:

        ПРИМЕРЫ СОСТАВНЫХ ЗАПРОСОВ:
        • "дайджест по pm4py за ноябрь" 
          → {{"date_range": {{"start_date": "2025-11-01", "end_date": "2025-11-30"}}, "topic": "pm4py"}}
        • "статьи с марта по октябрь 2025 по категории Общие принципы: CRISP_DM"
          → {{"date_range": {{"start_date": "2025-03-01", "end_date": "2025-10-31"}}, "category": "Общие принципы: CRISP_DM"}}
        • "ML и agent systems за последний квартал"
          → {{"date_range": {{"start_date": "2025-10-01", "end_date": "2025-12-31"}}, "topic": "ML agent systems"}}
        • "дайджест за период с 2025-01-01 до 2025-03-01"
          → {{"date_range": {{"start_date": "2025-01-01", "end_date": "2025-03-01"}}}}
        • "дайджест по категории Общие принципы"
          → {{"category": "Общие принципы"}}
        • "дайджест по статьям связанным с AI machine learning"
          → {{"topic": "AI machine learning"}}
        • "дайджест по статьям по категории Общие принципы, связанными с AI machine learning"
          → {{"category": "Общие принципы", "topic": "AI machine learning"}}

        
        
        ПРАВИЛА:
        1. ДАТЫ всегда в YYYY-MM-DD ("ноябрь" → "2025-11-01/30")
        2. НЕ удаляй параметры, даже если их несколько
        3. ТОЛЬКО если нет фильтров по дате, теме, категории, названию → topic=весь_запрос
        
        ВЕРНИ ТОЛЬКО VALID JSON:
        """
        messages = [SystemMessage(content=prompt)]
        response = llm_client.invoke(messages)
        
        json_str = extract_json(response.content)

        # 2. Попытка парсинга
        try:
            filters = CompositeFilters.model_validate_json(json_str)
            return filters
        except Exception as parse_error:
            # 3. АВТО-ФИКС: Если JSON обрезан (EOF error), пробуем дозакрыть скобки
            print(f"Ошибка парсинга JSON: {parse_error}. Пробую восстановить...")
            
            open_braces = json_str.count('{')
            close_braces = json_str.count('}')
            open_brackets = json_str.count('[')
            close_brackets = json_str.count(']')
            open_quotes = json_str.count('"') % 2 # Если нечетное кол-во, значит кавычка открыта
            
            fixed_json = json_str
            if open_quotes:
                fixed_json += '"' # Закрываем строку
            while close_brackets < open_brackets:
                fixed_json += ']'
                close_brackets += 1
            while close_braces < open_braces:
                fixed_json += '}'
                close_braces += 1
            
            print(f"Исправленный JSON (фрагмент): {fixed_json[:200]}...")
            filters = CompositeFilters.model_validate_json(fixed_json)
            return filters

    @staticmethod
    def create_summary(article_text: str, file_name, llm_client) -> str:
        prompt = f"""Твоя задача: прочитать текст статьи из "{file_name}", извлечь и адаптировать информацию для русскоязычного эксперта.

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
        "Статья в сообществе": "'отсутствует' или 'нет'",
        "Описание":
            1. **Цель:** ...
            2. **Проблема:** ...
            3. **Задачи:** ...
            4. **Подход - суть алгоритма/метода:** ...
            5. **Как реализуется подход:** ...
            6. **Пример из статьи:** ...
            7. **Вывод:** ...

        ## Текст статьи из PDF:
        {article_text}."""

        messages = [SystemMessage(content=prompt)]
        response = llm_client.invoke(messages)

        return response.content

# =============================================================================
# 2. ГЛАВНАЯ ФУНКЦИЯ С СОСТАВНОЙ ФИЛЬТРАЦИЕЙ
# =============================================================================

def create_composite_digest(json_path: str, query: str, llm_client, md_folder: str = "digests"):
    """
    🧠 ПОЛНЫЙ ПАЙПЛАЙН: LLM → СОСТАВНЫЕ ФИЛЬТРЫ → md_files
    """
    # 1. LLM выделяет ВСЕ параметры
    filters = LLMCompositeExtractor.extract_composite_filters(query, llm_client)
    
    # 2. Загрузка данных
    articles = load_json_metadata(json_path)
    if not articles:
        return {"error": "Нет статей"}
    
    # 3. Многоэтапная составная фильтрация
    filtered = apply_composite_filters(articles, filters)
    
    # 4. Результат
    md_paths = find_matching_md_files(filtered, Path(md_folder))
    
    return filtered, {
        'status': "created",
        "created_at": datetime.now().isoformat(),
        'query': query,
        'composite_filters': filters.model_dump(),
        'is_composite': filters.is_composite,
        'found_articles': len(filtered),
        'top_articles': filtered[:5],
        'md_files': md_paths,
        'action': 'summarize_batch' if md_paths else 'no_matches',
        'filters': filters
    }

def find_matching_md_files(articles: List[Dict], root_folder: Path) -> List[Path]:
    """Рекурсивно ищет md файлы по всем подпапкам, соответствующие отфильтрованным статьям."""
    matching_files = []
    for article in articles:
        expected_filename = f"{article['date_sub']}_{article['title']}.md"
        expected_filename = Path(expected_filename)
        matching_files.append(expected_filename)
        # Ниже очень жесткая проверка, список всегда пустой на выходе
        # Ищем файл рекурсивно во всех подпапках
        # for md_file in root_folder.rglob('*'):
        #     print("md_file.name", md_file.name)
        #     if md_file.is_file() and md_file.suffix == '.md' and md_file.name == expected_filename:
        #         matching_files.append(md_file)
        #         print(f"  Найден: {md_file}")
        #         break  # Берем первый найденный
    
    print(f"Найдено md файлов: {len(matching_files)} из {len(articles)} статей")
    return matching_files

# =============================================================================
# 3. МНОГОЭТАПНАЯ СОСТАВНАЯ ФИЛЬТРАЦИЯ
# =============================================================================

def apply_composite_filters(articles: List[Dict], filters: CompositeFilters) -> List[Dict]:
    """Применяет ВСЕ активные фильтры последовательно."""
    filtered = articles.copy()
    
    # print(f"Применение {len([f for f in [filters.date_range, filters.category, filters.topic, filters.title] if f])} фильтров:")
    #
    # 1️⃣ ДАТА (самый строгий фильтр - применяется первым)
    if filters.has_date:
        print("Фильтр по дате...")
        filtered = date_filter(filtered, filters.date_range['start_date'], filters.date_range['end_date'])
    
    # 2️⃣ КАТЕГОРИЯ (точная)
    if filters.has_category:
        print("Фильтр по категории...")
        filtered = category_filter(filtered, filters.category)
    
    # 3️⃣ НАЗВАНИЕ (точная)
    if filters.title:
        print("Фильтр по названию...")
        filtered = title_filter(filtered, filters.title)
    
    # 4️⃣ ТЕМА (семантическая - последней, с учетом предыдущих фильтров)
    if filters.has_topic:
        print("Семантический фильтр...")
        filtered = semantic_filter(filtered, filters.topic, filters.threshold)
    
    # Сортировка по релевантности
    filtered.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
    filtered = filtered[:filters.limit]
    
    print(f"Итог: {len(filtered)} статей после всех фильтров")
    return filtered

# Остальные функции фильтрации (без изменений)
def date_filter(articles: List[Dict], start_date: str, end_date: str) -> List[Dict]:
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        return [art for art in articles if _date_in_range(art.get('date_sub'), start, end)]
    except ValueError:
        return articles

def category_filter(articles: List[Dict], category: Optional[str]) -> List[Dict]:
    if not category:
        return articles

    cat = category.lower()
    return [
        art for art in articles
        if (art.get('category') or '').lower().find(cat) != -1
    ]
def title_filter(articles: List[Dict], title: Optional[str]) -> List[Dict]:
    if not title:
        return articles
    t = title.lower()
    return [
        art for art in articles
        if (art.get('title') or '').lower().find(t) != -1
    ]
def semantic_filter(articles: List[Dict], topic: str, threshold: float) -> List[Dict]:

    if topic == 'весь_запрос':
        return articles

    texts = []
    valid_articles = []

    for art in articles:
        tags = art.get('tags', '')
        tags_text = ' '.join(tags) if isinstance(tags, list) else tags

        text = f"{art.get('title', '')} {art.get('category', '')} {tags_text}".strip()

        if text:
            texts.append(text)
            valid_articles.append(art)

    if not texts:
        return []

    query_emb = EMBED_MODEL.encode([topic])
    arts_emb = EMBED_MODEL.encode(texts)

    scores = cosine_similarity(query_emb, arts_emb)[0]

    filtered = []

    for art, score in zip(valid_articles, scores):
        if score >= threshold:
            art['relevance_score'] = float(score)
            filtered.append(art)

    return filtered


def _date_in_range(data_sub: str, start: datetime, end: datetime) -> bool:
    try:
        art_date = datetime.strptime(data_sub, '%Y-%m-%d')
        return start <= art_date <= end
    except:
        return False

def load_json_metadata(json_path: str) -> List[Dict]:
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return []

# =============================================================================
# 4. ИНТЕГРАЦИЯ С АГЕНТОМ
# =============================================================================

def agent_composite_digest_tool(query: str, llm_client, json_path="knowledge_base/metadata.json", md_folder='digests'):
    """Инструмент для агента с поддержкой составных фильтров."""
    arts, result = create_composite_digest(json_path, query, llm_client, md_folder)

    filter_slug = result['filters'].to_slug()
    full_text = build_digest_content(arts, Path(md_folder), query, llm_client)
    base_path = Path(__file__).resolve().parent
    digest_all_path = base_path / 'digests_all'
    output_path, filename = save_digest(full_text,  filter_type=filter_slug, output_dir=digest_all_path)

    result.update({'digest_file':str(output_path)})
    result.update({'filename': filename})

    
    return result

# =============================================================================
# 5. СОХРАНЕНИЕ ДАЙДЖЕСТА
# =============================================================================
def build_digest_content(articles: List[Dict], md_path: Path, query: list, llm_client) -> str:
    """Строит итоговый дайджест"""
    content = [f"# 📋 Дайджест материалов внешних источников за период/по теме: '{query}'",
               f"**Создан:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
               f"**Найдено статей:** {len(articles)}"]
    start = time.time()
    existing_files = {
        md_file.name: md_file
        for md_file in md_path.rglob("*.md")
    }

    # md_paths = find_matching_md_files(articles, md_path)

    # print(md_paths)

    base_path = Path(__file__).resolve().parent
    articles_dir = base_path / "knowledge_base" / "articles"

    for i, article in enumerate(articles):
        
        category = article.get("category") or "Без категории"
        if category == "Не релевантна PM":
            continue

        filename = article.get("file_article")
        title = article.get("title", "N/A")
        url = article.get("url_article", "N/A")
        date = article.get("date_sub", "")
        theme = article.get("tags", "")

        # md_file = None
        # for path in md_paths:
        #     if f"{date}_{title}.md" in path.name:
        #         md_file = path

        content.extend([
            "",
            f"## 🎯 {title}",
            f"**Категория:** {category} | **Дата:** {date}",
            f"**ТЕМА:** {theme}",
            f"## Ссылка на источник {url}",
            "",
            "### Саммари статьи"
        ])

        # Проверка — есть ли уже саммари
        if filename in existing_files:
            print(f"Используем готовый md: {filename}")
            md_file = existing_files[filename]

            with open(md_file, "r", encoding="utf-8") as f:
                md_content = f.read()

            content.append(md_content)
        # Читаем MD
        # filename_meta = f"{article['date_sub']}_{article['title']}.md"
        # md_file = Path(md_path) / filename_meta
        # base_path = Path(__file__).resolve().parent
        # md_file = base_path / 'knowledge_base' / 'articles' / md_file

        else:
            print(f"Генерируем через LLM: {filename}")
            article_md_path = articles_dir / filename

            if article_md_path.exists():
                with open(article_md_path, "r", encoding="utf-8") as f:
                    md_content = f.read()
                md_content = (
                    re.split(r'^---\s*$', md_content, maxsplit=2)[2]
                    if '---' in md_content
                    else md_content
                )

                # summary = LLMCompositeExtractor.create_summary(
                #     md_content[:2000],
                #     filename,
                #     llm_client
                # )

                summary = sum.generate_summary(md_content, filename=filename) # Заменил на Гигачат

                digest_dir = Path(md_path / category)
                digest_dir.mkdir(parents=True, exist_ok=True)

                with open(digest_dir / filename, "w", encoding="utf-8") as f:
                    f.write(summary['digest'])

                content.append(summary['digest'])

        content.append("---" * 30)
    finish = time.time()
    print(finish - start)
    return "\n".join(content)

def save_digest(content: str, filter_type: str, output_dir: Path) -> Path:
    """Сохраняет дайджест"""
    Path(output_dir).mkdir(exist_ok=True)
    filename = f"digest_{filter_type}.md"
    output_path = Path(output_dir) / filename

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return output_path, filename

