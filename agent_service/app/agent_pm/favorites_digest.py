import json
import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel, Field
from langchain.schema import SystemMessage, HumanMessage

from .core.botocore_client import OBSWorker
from .core.agent_provider import get_model

llm_client = get_model()

from pydantic import BaseModel, Field


class QueryDateRange(BaseModel):
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    has_date_filter: bool = Field(default=False)


DATE_EXTRACT_PROMPT = """
Ты извлекаешь диапазон дат из запроса пользователя для фильтрации статей.

Текущая дата: 2026-05-04.

Нужно вернуть ТОЛЬКО JSON в формате:
{
  "start_date": "YYYY-MM-DD" | null,
  "end_date": "YYYY-MM-DD" | null,
  "has_date_filter": true | false
}

Правила:
- Если в запросе нет упоминания периода или даты, верни:
  {"start_date": null, "end_date": null, "has_date_filter": false}
- Если указан месяц, верни полный диапазон месяца.
- Если указан год, верни диапазон с 1 января по 31 декабря этого года.
- Если указан квартал, верни диапазон квартала.
- Если указан "последний квартал", считай относительно текущей даты.
- Если указан "за сегодня", "за вчера", "за неделю", "за месяц" — преобразуй в точные даты.
- Все даты строго в формате YYYY-MM-DD.
- Верни только валидный JSON без markdown.
""".strip()

def extract_date_filters_from_query(query: str) -> QueryDateRange:
    messages = [
        SystemMessage(content=DATE_EXTRACT_PROMPT),
        HumanMessage(content=query),
    ]

    response = llm_client.invoke(messages)
    content = extract_json(response.content)

    try:
        data = json.loads(content)
        return QueryDateRange(**data)
    except Exception:
        cleaned = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(cleaned)
        return QueryDateRange(**data)

class FavoritesDigestFilters(BaseModel):
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")

    @property
    def has_date_filter(self) -> bool:
        return bool(self.start_date and self.end_date)

    def to_slug(self) -> str:
        if self.has_date_filter:
            return f"date_{self.start_date}_{self.end_date}"
        return "all"


def extract_json(text: str) -> str:
    text = text.strip()

    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        return match.group(1)

    if text.startswith("json"):
        return text[4:].strip()

    return text


def parse_date_safe(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def filter_favorites_by_date(
    favorites: List[Dict[str, Any]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not start_date or not end_date:
        return favorites

    start = parse_date_safe(start_date)
    end = parse_date_safe(end_date)
    if not start or not end:
        return favorites

    filtered = []
    for item in favorites:
        article_date = parse_date_safe(item.get("date_sub"))
        if article_date and start <= article_date <= end:
            filtered.append(item)
    return filtered


def _normalize_json_field(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            return [value]
    return [value]


def normalize_favorite_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "article_id": item.get("article_id"),
        "external_id": item.get("external_id"),
        "title": item.get("title"),
        "date_sub": str(item.get("date_sub")) if item.get("date_sub") else None,
        "url_article": item.get("url_article"),
        "pdf_url": item.get("pdf_url"),
        "authors": _normalize_json_field(item.get("authors_json")),
        "tags": _normalize_json_field(item.get("tags_json")),
        "abstract": item.get("abstract"),
        "category": item.get("category"),
        "review": item.get("review"),
        "review_created_at": str(item.get("review_created_at")) if item.get("review_created_at") else None,
        "review_updated_at": str(item.get("review_updated_at")) if item.get("review_updated_at") else None,
    }


def build_favorites_digest_payload(
    username: str,
    favorites: List[Dict[str, Any]],
    filters: Optional[FavoritesDigestFilters] = None,
) -> Dict[str, Any]:
    filters = filters or FavoritesDigestFilters()

    normalized = [normalize_favorite_item(item) for item in favorites]
    filtered = filter_favorites_by_date(
        normalized,
        start_date=filters.start_date,
        end_date=filters.end_date,
    )

    reviewed_articles = [x for x in filtered if x.get("review")]
    not_reviewed_articles = [x for x in filtered if not x.get("review")]

    return {
        "user": username,
        "created_at": datetime.now().isoformat(),
        "filters": filters.model_dump(),
        "total_favorites": len(filtered),
        "total_reviewed": len(reviewed_articles),
        "total_not_reviewed": len(not_reviewed_articles),
        "reviewed_articles": reviewed_articles,
        "not_reviewed_articles": not_reviewed_articles,
    }


DIGEST_SYSTEM_PROMPT = """
Ты — аналитик научных статей и пользовательских review.

Тебе передают список избранных статей пользователя и его review к ним.
Нужно сделать дайджест по интересам пользователя и его оценкам статей.

Верни результат СТРОГО в JSON.

Формат ответа:
{
  "user": "username",
  "period": {
    "start_date": null,
    "end_date": null
  },
  "stats": {
    "total_favorites": 0,
    "total_reviewed": 0,
    "total_not_reviewed": 0
  },
  "overview": "2-4 предложения с общим описанием интересов пользователя",
  "key_topics": [
    {
      "topic": "название темы",
      "summary": "почему эта тема важна для пользователя",
      "articles": [
        {
          "article_id": 0,
          "title": "название статьи",
          "category": "категория",
          "user_review_summary": "краткое содержание review",
          "sentiment": "positive | neutral | negative | mixed"
        }
      ]
    }
  ],
  "strong_signals": [
    "какие интересы/паттерны явно прослеживаются по review"
  ],
  "articles_without_reviews": [
    {
      "article_id": 0,
      "title": "название статьи"
    }
  ],
  "recommendations": [
    "что ещё изучить пользователю",
    "какие темы объединить",
    "на что обратить внимание дальше"
  ]
}

Правила:
- Используй только входные данные.
- Не придумывай факты, которых нет.
- Если review мало, так и укажи в overview.
- Если статей без review нет, верни пустой список.
- Верни только валидный JSON без markdown.
""".strip()


class FavoritesReviewDigestService:
    @staticmethod
    async def generate_digest(
        username: str,
        favorites_with_reviews: List[Dict[str, Any]],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        filters = FavoritesDigestFilters(
            start_date=start_date,
            end_date=end_date,
        )

        payload = build_favorites_digest_payload(
            username=username,
            favorites=favorites_with_reviews,
            filters=filters,
        )

        if payload["total_favorites"] == 0:
            return {
                "user": username,
                "period": {
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "stats": {
                    "total_favorites": 0,
                    "total_reviewed": 0,
                    "total_not_reviewed": 0,
                },
                "overview": "По выбранному периоду нет избранных статей.",
                "key_topics": [],
                "strong_signals": [],
                "articles_without_reviews": [],
                "recommendations": []
            }

        messages = [
            SystemMessage(content=DIGEST_SYSTEM_PROMPT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, indent=2)),
        ]

        response = llm_client.invoke(messages)
        content = extract_json(response.content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            cleaned = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(cleaned)

        return result


def render_digest_to_markdown(digest: Dict[str, Any]) -> str:
    period = digest.get("period", {})
    stats = digest.get("stats", {})

    lines = [
        f"# Дайджест по избранным review пользователя: {digest.get('user', 'unknown')}",
        "",
        f"**Период:** {period.get('start_date')} — {period.get('end_date')}",
        f"**Всего избранных:** {stats.get('total_favorites', 0)}",
        f"**С review:** {stats.get('total_reviewed', 0)}",
        f"**Без review:** {stats.get('total_not_reviewed', 0)}",
        "",
        "## Обзор",
        digest.get("overview", ""),
        "",
        "## Ключевые темы",
    ]

    for topic in digest.get("key_topics", []):
        lines.extend([
            f"### {topic.get('topic', 'Без темы')}",
            topic.get("summary", ""),
            "",
        ])

        for article in topic.get("articles", []):
            lines.extend([
                f"- **{article.get('title', 'Без названия')}** (article_id={article.get('article_id')}, category={article.get('category')}, sentiment={article.get('sentiment')})",
                f"  - review summary: {article.get('user_review_summary', '')}",
            ])

        lines.append("")

    lines.extend([
        "## Сильные сигналы",
    ])

    for item in digest.get("strong_signals", []):
        lines.append(f"- {item}")

    lines.extend([
        "",
        "## Статьи без review",
    ])

    for article in digest.get("articles_without_reviews", []):
        lines.append(f"- {article.get('title')} (article_id={article.get('article_id')})")

    lines.extend([
        "",
        "## Рекомендации",
    ])

    for rec in digest.get("recommendations", []):
        lines.append(f"- {rec}")

    return "\n".join(lines)


def save_digest(content: str, username: str, filter_type: str = "all", output_dir: Optional[Path] = None) -> Tuple[Path, str]:
    output_dir = output_dir or (Path(__file__).resolve().parent / "digests_all")
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_user = re.sub(r"[^a-zA-Zа-яА-Я0-9_-]+", "_", username)
    filename = f"favorites_digest_{safe_user}_{filter_type}.md"
    output_path = output_dir / filename

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_path, filename


async def build_user_favorites_review_digest(
    username: str,
    favorites_with_reviews: List[Dict[str, Any]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    save_to_file: bool = False,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    
    digest_json = await FavoritesReviewDigestService.generate_digest(
        username=username,
        favorites_with_reviews=favorites_with_reviews,
        start_date=start_date,
        end_date=end_date,
    )

    md_content = render_digest_to_markdown(digest_json)

    result = {
        "status": "created",
        "created_at": datetime.now().isoformat(),
        "username": username,
        "start_date": start_date,
        "end_date": end_date,
        "digest_json": digest_json,
        "digest_markdown": md_content,
    }

    if save_to_file:
        filters = FavoritesDigestFilters(start_date=start_date, end_date=end_date)
        output_path, filename = save_digest(
            content=md_content,
            username=username,
            filter_type=filters.to_slug(),
            output_dir=output_dir,
        )
        result["digest_file"] = str(output_path)
        result["filename"] = filename

    return result

async def build_user_favorites_review_digest_from_query(
    username: str,
    favorites_with_reviews: List[Dict[str, Any]],
    user_query: str,
    save_to_file: bool = False,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    
    obs_loader = OBSWorker()

    date_filters = extract_date_filters_from_query(user_query)

    result = await build_user_favorites_review_digest(
        username=username,
        favorites_with_reviews=favorites_with_reviews,
        start_date=date_filters.start_date if date_filters.has_date_filter else None,
        end_date=date_filters.end_date if date_filters.has_date_filter else None,
        save_to_file=save_to_file,
        output_dir=output_dir,
    )

    result["user_query"] = user_query
    result["detected_date_filter"] = date_filters.model_dump()

    us_name = result["username"]
    create = result["created_at"]
    
    filename = f"digest_{us_name}_{create}.md"

    result["file_name"] = filename
    await obs_loader.upload_digest_markdown_content(result["digest_markdown"], filename)
    print(f"Дайджест {filename} добавлен")

    return result


__all__ = [
    "FavoritesDigestFilters",
    "FavoritesReviewDigestService",
    "build_favorites_digest_payload",
    "build_user_favorites_review_digest",
    "extract_json",
    "filter_favorites_by_date",
    "normalize_favorite_item",
    "parse_date_safe",
    "render_digest_to_markdown",
    "save_digest",
]
