import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict
import re
from typing import Tuple


def create_semantic_digest(json_file: Path,
                           md_folder: str = "digests/Общие_принципы",
                           query: str = "",
                           json_path: str = "test.json",
                           output_dir: str = "digests_all") -> str:
    """
    Инструмент AGENT_DIGEST: создает общий дайджест по дате/теме из запроса

    Автоматически:
    1. Извлекает дату/тему из запроса пользователя
    2. Фильтрует JSON по семантике (косинус + точное совпадение)
    3. Собирает MD дайджесты в единый отчет

    Args:
        query: Запрос пользователя ("дайджест за ноябрь", "по теме CRISP_DM")
        json_file: JSON с метаданными
        md_folder: Папка с .md файлами
        json_path: Путь для сохранения обновленного JSON
        output_dir: Папка для дайджестов

    Returns:
        Путь к созданному дайджесту
    """

    # 1. Анализ запроса
    filter_type, filter_value = analyze_user_query(query)

    # 2. Загрузка данных
    with open(json_file, 'r', encoding='utf-8') as f:
        articles = json.load(f)

    md_path = Path(md_folder)

    # 3. Фильтрация по типу
    if filter_type == "date":
        filtered = filter_by_date(articles, filter_value)
    elif filter_type == "theme":
        filtered = filter_by_semantics(articles, filter_value)
    elif filter_type == 'both':
        filtered = filter_by_semantics(articles, filter_value[1])
        filtered = filter_by_date(filtered, filter_value[0])
    else:
        filtered = articles

    if not filtered:
        raise ValueError(f"Нет статей для '{query}'")

    # 4. Сборка дайджеста
    digest_content = build_digest_content(filtered, md_path, query)

    # 5. Сохранение
    output_path = save_digest(digest_content, filter_type, filter_value, output_dir)
    filename = f"digest_{filter_type}_{filter_value}.md"

    # print(f"[{filter_type.upper()}] Дайджест: {output_path} ({len(filtered)} статей)")
    return {"status": "created",
            "filename": filename,
            "created_at": datetime.now().isoformat(),
            "digest_file": str(output_path)
            }


# === ИНТЕЛЛЕКТУАЛЬНЫЙ АНАЛИЗ ЗАПРОСА ===
def analyze_user_query(query: str) -> tuple[str, str]:
    """Определяет тип фильтра и значение из естественного языка"""
    query_lower = query.lower()

    # 🔍 Поиск дат (ноябрь, 2025-11, за прошлый месяц и т.д.)
    date_patterns = [
        r'\b(январ[яь]|феврал[яь]|март[а]|апрел[яь]|ма[йя]|июн[яь]|июл[яь]|'
        r'август[а]|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])\b',
        r'\b(202[0-6]|202[0-6]-[0-1][0-9]|202[0-6]-[0-1][0-9]-[0-3][0-9])\b',
        r'\b(прошл(?:ый|ого|ом)|текущ(?:ий|его|ем)|след(?:ующий|ующего|ующим))\s+(?:месяц|год)\b'
    ]

    comb = {}

    for pattern in date_patterns:
        if re.search(pattern, query_lower):
            typ, val = "date", extract_date_from_query(query)
            comb.update({typ: val})

    # 🎯 Поиск тем (CRISP_DM, методики, агенты и т.д.)
    theme_keywords = ["общие принципы", "определение pm",
                      "cтандарты pm",
                      "фреймворки pm",
                      "моделирование в pm",
                      "неэффективности",
                      "методики исследования", "crisp_dm",
                      "pm square",
                      "pm-лог", "структура логов",
                      "форматы данных",
                      "проверка качества", "качество данных", "качество лога"]
    if any(kw in query_lower for kw in theme_keywords):
        typ, val = "theme", extract_theme_from_query(query)
        comb.update({typ: val})

    if len(comb) > 1:
        return 'both', [comb['date'], comb['theme']]
    elif len(comb) == 1:
        return typ, val
    else:
        return "all", "все статьи"


def extract_date_from_query(query: str) -> Tuple[str, str]:
    pattern = r'\b(?:\d{2}[-.]\d{2}[-.]\d{4}|\d{4}[-.]\d{2}[-.]\d{2})\b'
    dates = re.findall(pattern, query)
    return dates


def extract_theme_from_query(query: str) -> str:
    """Извлекает ключевые слова темы"""
    # Простое извлечение (заменить на LLM если нужно)
    words = re.findall(r'([\'"])((?:(?!\1).)*?)\1', query)
    print(words[0][1])
    return words[0][1]


# === ФИЛЬТРАЦИЯ ===
def filter_by_date(articles: List[Dict], date_filter: list) -> List[Dict]:
    """Фильтр по дате (2025-11, 2025 и т.д.)"""
    filtered = []
    start_date = datetime.strptime(date_filter[0], "%Y-%m-%d")
    end_date = datetime.strptime(date_filter[1], "%Y-%m-%d")
    for article in articles:
        article_date = article.get('data_sub', '')
        article_date = datetime.strptime(article_date, "%Y-%m-%d")
        if start_date <= article_date <= end_date:
            filtered.append(article)
    return filtered


def filter_by_semantics(articles: List[Dict], query_theme: str) -> List[Dict]:
    """Семантическая фильтрация по category/tags (простое + косинус)"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    categories = [a.get('category', '') + ' ' + ' '.join(a.get('tags', []))
                  for a in articles]

    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform([query_theme] + categories)

    similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]

    # Топ по схожести + точное совпадение
    for i, score in enumerate(similarities):
        if score > 0.3 or query_theme.lower() in categories[i].lower():
            articles[i]['_similarity'] = float(score)

    return [a for a in articles if a.get('_similarity', 0) > 0.2]


# === СОБСТВЕННО ДАЙДЖЕСТ ===
def build_digest_content(articles: List[Dict], md_path: Path, query: list) -> str:
    """Строит итоговый дайджест"""
    content = [f"# 📋 Дайджест материалов внешних источников за период/по теме: '{query}'",
               f"**Создан:** {datetime.now().strftime('%d.%m.%Y %H:%M')}",
               f"**Найдено статей:** {len(articles)}"]

    # Сортировка по релевантности/дате
    articles.sort(key=lambda x: x.get('_similarity', 0), reverse=True)

    for article in articles:
        title = article.get('title', 'N/A')
        url = article.get('url_article', 'N/A')
        category = article.get('category', 'Не указана')
        date = article.get('data_sub', '')

        content.extend([
            "",
            f"## 🎯 {title}",
            f"**Категория:** {category} | **Дата:** {date}",
            f"## Ссылка на источник {url}",
            ""
        ])

        content.append("### Саммари статьи")
        # Читаем MD
        filename_meta = f"{article['data_sub']}_{article['title']}.md"
        md_file = md_path / filename_meta
        if md_file.exists():
            with open(md_file, 'r', encoding='utf-8') as f:
                md_content = f.read()
                # Убираем YAML фронтматтер
                md_content = re.split(r'^---\s*$', md_content, maxsplit=2)[2] if '---' in md_content else md_content
                content.append(re.sub(r'^#{1,6}\s+', '### ', md_content, flags=re.MULTILINE))

        content.append("---" * 30)

    return "\n".join(content)


def save_digest(content: str, filter_type: str, filter_value: str, output_dir: str) -> Path:
    """Сохраняет дайджест"""
    Path(output_dir).mkdir(exist_ok=True)
    filename = f"digest_{filter_type}_{filter_value}.md"
    output_path = Path(output_dir) / filename

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return output_path

# === ИНТЕГРАЦИЯ С АГЕНТОМ ===
"""
AGENT TOOL REGISTRATION:
CREATE_DIGEST = "create_digest"

def agent_tool_call(tool_name, **kwargs):
    if tool_name == "CREATE_DIGEST":
        return create_semantic_digest(**kwargs)
"""