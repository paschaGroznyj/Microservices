"""
Универсальный модуль дайджестов с LLM-парсингом и СОСТАВНЫМИ ФИЛЬТРАМИ
LLM выделяет ВСЕ параметры (дата+тема+категория) и применяет их КОМБИНИРОВАННО
"""
import re
import json
from pathlib import Path
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, date
from pydantic import BaseModel, Field
from sklearn.metrics.pairwise import cosine_similarity
from .core.agent_provider import get_embedd_model

from langchain.schema import SystemMessage, HumanMessage
from .email_sender import EmailSender
from .core.config import SMTP_HOST, SMTP_PORT, SMTP_LOGIN, SMTP_PASSWORD
from .summary import SummaryGenerator

# =============================================================================
# 1. РАСШИРЕННАЯ СТРУКТУРА ДЛЯ СОСТАВНЫХ ФИЛЬТРОВ
# =============================================================================

EMBED_MODEL = get_embedd_model()

def extract_email_from_query(query: str) -> Optional[str]:
    """
    Извлекает первый email из текста пользовательского запроса.
    """
    if not query:
        return None

    match = re.search(r'(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+', query)
    return match.group(0) if match else None

def send_digest_email_if_needed(
    recipient_email: Optional[str],
    filename: str,
    email_sender: Optional[EmailSender] = None,
    base_download_url: str = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/",
) -> Dict[str, Any]:
    """
    Отправляет email, если найден адрес получателя.
    Предполагается, что файл будет/может быть доступен по predictable URL через filename.
    """
    if not recipient_email:
        return {
            "email_requested": False,
            "email_sent": False,
            "email_message": "Email в запросе не найден.",
        }

    if not filename:
        return {
            "email_requested": True,
            "email_sent": False,
            "email_message": "Не найдено имя файла дайджеста.",
            "recipient_email": recipient_email,
        }

    full_link = f"{base_download_url}{filename}"

    body_text = (
        "Здравствуйте!\n\n"
        "Дайджест сформирован.\n\n"
        f"Имя файла: {filename}\n"
        f"Ссылка на скачивание: {full_link}\n\n"
        "С уважением,\nAgentPM"
    )

    sender = email_sender or EmailSender(
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_login=SMTP_LOGIN,
        smtp_password=SMTP_PASSWORD,
    )

    send_result = sender.send_digest(
        to_addresses=[recipient_email],
        subject="AgentPM: дайджест сформирован",
        body_html=body_text,
    )

    return {
        "email_requested": True,
        "email_sent": send_result.get("status") == "ok",
        "email_message": send_result.get("message", ""),
        "recipient_email": recipient_email,
        "download_link": full_link,
    }

class CompositeFilters(BaseModel):
    """Составные фильтры: можно комбинировать date+topic, category+date и т.д."""
    date_range: Optional[Dict[str, str]] = Field(None, description="{'start_date': 'YYYY-MM-DD', 'end_date': 'YYYY-MM-DD'}")
    category: Optional[str] = Field(None)
    topic: Optional[str] = Field(None)
    title: Optional[str] = Field(None)
    source: Optional[str] = Field(None, description="Источник: yandex_disk:ICPM, googlescholar, arxiv.org, ieeexplore.ieee.org")
    threshold: float = Field(0.3, description="Порог семантики")
    limit: int = Field(100)
    
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
    def has_source(self) -> bool:
        return bool(self.source)
    
    @property
    def is_composite(self) -> bool:
        """True если несколько фильтров активны."""
        count = sum([self.has_date, self.has_topic, self.has_category, bool(self.title), self.has_source])
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

        if self.source:
            parts.append(f"source_{_slugify(self.source)}")

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

        SOURCE = ["yandex_disk:ICPM", "googlescholar", "arxiv.org", "ieeexplore.ieee.org"]

        prompt = f"""
        Проанализируй запрос дайджеста: "[query]"

        Извлеки ВСЕ параметры фильтрации одновременно в JSON CompositeFilters:

        ПРИМЕРЫ СОСТАВНЫХ ЗАПРОСОВ:
        • "дайджест по ICPM" 
          → {{"source": "yandex_disk:ICPM"}}
        • "pm4py за ноябрь из googlescholar" 
          → {{"date_range": {{"start_date": "2025-11-01", "end_date": "2025-11-30"}}, "topic": "pm4py", "source":"googlescholar"}}
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
        • "статьи по категории Общие принципы из googlescholar, связанными с AI machine learning"
          → {{"category": "Общие принципы", "topic": "AI machine learning", "source": "googlescholar"}}

        ДОПУСТИМЫЕ ИСТОЧНИКИ (source):
        {', '.join(SOURCE)}
        
        ПРАВИЛА:
        1. ДАТЫ всегда в YYYY-MM-DD ("ноябрь" → "2025-11-01/30")
        2. НЕ удаляй параметры, даже если их несколько
        3. "ICPM" → source="yandex_disk:ICPM" (по умолчанию)
        4. ТОЛЬКО если нет фильтров по дате, теме, категории, названию → topic=весь_запрос
        
        ВЕРНИ ТОЛЬКО VALID JSON:
        """
        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=query) 
        ]
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
        1. Переводи для русскоязычного эксперта, сохраняя академический стиль, сохраняй точные термины в оригинале, например 'process mining' или названия инструментов: 'SOUP: Simplifying Event...' оставляй как есть в оригинале
        2. Факты БЕРИ ТОЛЬКО ИЗ ТЕКСТА, без домыслов
        3. Первый раз термин сокращенно: на original языке. Далее: термин на оригинальном языке, перевод и расшифровки на русском
        4. Алгоритмы - пошагово с оригинальными названиями
        5. GitHub: найди в тексте ИЛИ проанализируй авторов/метод, иначе 'отсутствует'
        6. Если в статье нет какой-либо информации для шаблона - напиши 'не указано'
        7. Выводи результат строго по шаблону, без дополнительных комментариев и пояснений
        8. Не меняй порядок пунктов шаблона и не добавляй новые
        9. Не переводи авторов, оставляй в оригинале

        ## Пример шаблона саммари:
        "Название статьи на русском:",
        "Название оригинальной статьи на английском:",
        "Авторы на английском (в оригинале):",
        "Статья в сообществе": "'отсутствует' или 'нет'",
        "Описание":
            1. **Цель:** ...
            2. **Проблема:** ...
            3. **Задачи:** ...
            4. **Подход - суть алгоритма/метода:** ...
            5. **Как реализуется подход:** ...
            6. **Пример из статьи:** ...
            7. **Вывод:** ...

        ## Текст статьи из PDF:"""

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=article_text)  # ← добавить
        ]
        response = llm_client.invoke(messages)

        return response.content

# =============================================================================
# 2. ГЛАВНАЯ ФУНКЦИЯ С СОСТАВНОЙ ФИЛЬТРАЦИЕЙ
# =============================================================================

async def create_composite_digest(
    json_path: str,
    query: str,
    llm_client,
    database,
    md_folder: str = "digests",
):
    # 1. LLM выделяет ВСЕ параметры
    filters = LLMCompositeExtractor.extract_composite_filters(
        query, llm_client
    )

    # 2. Загрузка данных
    articles = await database.get_metadata_as_json()
    if not articles:
        return {"error": "Нет статей"}

    # 3. Многоэтапная составная фильтрация (получаем срез и общее число)
    filtered, total_found = apply_composite_filters(articles, filters)

    # 4. Результат
    md_paths = find_matching_md_files(filtered, Path(md_folder))

    return filtered, {
        "status": "created",
        "created_at": datetime.now().isoformat(),
        "query": query,
        "composite_filters": filters.model_dump(),
        "is_composite": filters.is_composite,
        "processed_articles": len(
            filtered
        ),  # <-- Сколько ушло в дайджест по лимиту
        "top_articles": filtered[:5],
        "md_files": md_paths,
        "action": "summarize_batch" if md_paths else "no_matches",
        "filters": filters,
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

def apply_composite_filters(
    articles: List[Dict], filters: CompositeFilters
) -> tuple[List[Dict], int]:
    """Применяет ВСЕ активные фильтры последовательно и возвращает (filtered_list, total_count)."""
    filtered = articles.copy()

    # 1. ДАТА
    if filters.has_date:
        filtered = date_filter(
            filtered,
            filters.date_range["start_date"],
            filters.date_range["end_date"],
        )

    # 2. ИСТОЧНИК
    if filters.has_source:
        filtered = source_filter(filtered, filters.source)

    # 3. КАТЕГОРИЯ
    if filters.has_category:
        filtered = category_filter(filtered, filters.category)

    # 4. НАЗВАНИЕ
    if filters.title:
        filtered = title_filter(filtered, filters.title)

    # 5. ТЕМА
    if filters.has_topic:
        filtered = semantic_filter(filtered, filters.topic, filters.threshold)

    # Сортировка по релевантности
    filtered.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    # Запоминаем НАСТОЯЩЕЕ количество найденных статей ДО среза limit
    total_found = len(filtered)

    # Применяем лимит
    limited_filtered = filtered[: filters.limit]

    print(
        f"Итог: найдено {total_found} статей, взято по лимиту ({filters.limit}): {len(limited_filtered)}"
    )
    return limited_filtered, total_found

def source_filter(articles: List[Dict], source: str) -> List[Dict]:
    """Фильтр по источнику данных."""
    if not source:
        return articles
    
    source_lower = source.lower()
    return [
        art for art in articles
        if (art.get('source', '') or '').lower().find(source_lower) != -1
    ]

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

    print("Вызываю модель эмбеддингов")
    query_emb = EMBED_MODEL.embed_documents([topic])
    arts_emb = EMBED_MODEL.embed_documents(texts)

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

async def agent_composite_digest_tool(query: str, llm_client, database, json_path="knowledge_base/metadata.json", md_folder='digests', send_email: bool = True, email_sender: Optional[EmailSender] = None, base_download_url: str = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/"):
    """Инструмент для агента с поддержкой составных фильтров."""
    base_path = Path(__file__).resolve().parent
    
    arts, result = await create_composite_digest(json_path, query, llm_client, database, md_folder)

    filter_slug = result['filters'].to_slug()
    full_text, count_articles, without_review = build_digest_content(arts, Path(md_folder), query, llm_client) #
    
    digest_all_path = base_path / 'digests_all'
    output_path, filename = save_digest(full_text,  filter_type=filter_slug, output_dir=digest_all_path)

    result.update({'digest_file':str(output_path)})
    result.update({'filename': filename})
    result.update({'found_articles': count_articles})
    result.update({'without_review': without_review})

    recipient_email = extract_email_from_query(query)
    result.update({
        "recipient_email": recipient_email,
        "email_requested": bool(recipient_email),
    })

    if send_email and recipient_email:
        email_result = send_digest_email_if_needed(
            recipient_email=recipient_email,
            filename=filename,
            email_sender=email_sender,
            base_download_url=base_download_url,
        )
        result.update(email_result)
    else:
        result.update({
            "email_sent": False,
            "email_message": "Отправка не запрошена." if not recipient_email else "Отправка отключена параметром send_email.",
        })

    return result


# =============================================================================
# 5. СОХРАНЕНИЕ ДАЙДЖЕСТА
# =============================================================================
def build_digest_content(articles: List[Dict], md_path: Path, query: str, llm_client) -> str:
    """Строит итоговый дайджест"""
    start = time.time()
    existing_files = {
        md_file.name: md_file
        for md_file in md_path.rglob("*.md")
    }
    sum = SummaryGenerator()
    # md_paths = find_matching_md_files(articles, md_path)

    # print(md_paths)
    relevant_articles = [a for a in articles if a.get("category") != "Не релевантна PM"]
    counter_relevant = len(relevant_articles)

    # 1. Добавляем шапку в самое начало списка
    content = [
        f"# 📋 Дайджест материалов внешних источников за период/по теме: '{query}'",
        f"**Создан:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        f"**Найдено статей:** {counter_relevant}",
        "---" * 30,
        ""
    ]
    without_review = 0
    base_path = Path(__file__).resolve().parent
    articles_dir = base_path / "knowledge_base" / "articles"
    counter_relevant = 0
    for i, article in enumerate(articles):
        
        category = article.get("category") or "Без категории"
        if category == "Не релевантна PM":
            continue
        counter_relevant += 1
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
            without_review += 1

        content.append("---" * 30)
    finish = time.time()
    print(finish - start)
    return "\n".join(content), counter_relevant, without_review # Число релеватных статей

def save_digest(content: str, filter_type: str, output_dir: Path) -> Path:
    """Сохраняет дайджест"""
    Path(output_dir).mkdir(exist_ok=True)
    filename = f"digest_{filter_type}.md"
    output_path = Path(output_dir) / filename

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return output_path, filename

