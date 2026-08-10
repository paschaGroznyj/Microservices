import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from pydantic import BaseModel, Field
from langchain.schema import SystemMessage, HumanMessage

from .core.botocore_client import OBSWorker
from .core.agent_provider import get_llm_model


llm_client = get_llm_model()

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
        cleaned = (
            content
            .removeprefix("```json")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        data = json.loads(cleaned)
        return QueryDateRange(**data)


def parse_date_safe(value: Any) -> Optional[date]:
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


def normalize_review_item(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "article_id": item.get("article_id"),
        "title": item.get("title"),
        "pdf_url": item.get("pdf_url"),
        "date_sub": str(item.get("date_sub")) if item.get("date_sub") else None,
        "review": item.get("review") or item.get("review_text"),
    }


def build_favorites_reviews_digest_payload(
    username: str,
    favorites: List[Dict[str, Any]],
    filters: Optional[FavoritesDigestFilters] = None,
) -> Dict[str, Any]:
    filters = filters or FavoritesDigestFilters()

    normalized = [
        normalize_review_item(dict(item) if not isinstance(item, dict) else item)
        for item in favorites
    ]

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
        "period": {
            "start_date": filters.start_date,
            "end_date": filters.end_date,
        },
        "stats": {
            "total_favorites": len(filtered),
            "total_reviewed": len(reviewed_articles),
            "total_not_reviewed": len(not_reviewed_articles),
        },
        "reviewed_articles": reviewed_articles,
        "not_reviewed_articles": not_reviewed_articles,
    }


def render_reviews_digest_to_markdown(digest: Dict[str, Any]) -> str:
    period = digest.get("period", {})
    stats = digest.get("stats", {})

    start_date = period.get("start_date") or "не задано"
    end_date = period.get("end_date") or "не задано"

    lines = [
        f"# Дайджест review пользователя: {digest.get('user', 'unknown')}",
        "",
        f"**Период:** {start_date} — {end_date}",
        f"**Всего избранных:** {stats.get('total_favorites', 0)}",
        f"**С review:** {stats.get('total_reviewed', 0)}",
        f"**Без review:** {stats.get('total_not_reviewed', 0)}",
        "",
        "## Статьи с review",
        "",
    ]

    reviewed_articles = digest.get("reviewed_articles", [])
    if not reviewed_articles:
        lines.extend([
            "По выбранному периоду нет статей с review.",
            "",
        ])
    else:
        for idx, article in enumerate(reviewed_articles, start=1):
            title = article.get("title", "Без названия")
            article_id = article.get("article_id")
            date_sub = article.get("date_sub") or "не указана"
            pdf_url = article.get("pdf_url") or "отсутствует"
            review = (article.get("review") or "").strip()

            lines.extend([
                f"### {idx}. {title}",
                f"- article_id: {article_id}",
                f"- date_sub: {date_sub}",
                f"- pdf_url: {pdf_url}",
                "",
                review if review else "_review отсутствует_",
                "",
                "---",
                "",
            ])

    lines.extend([
        "## Статьи без review",
        "",
    ])

    not_reviewed_articles = digest.get("not_reviewed_articles", [])
    if not not_reviewed_articles:
        lines.append("Таких статей нет.")
    else:
        for article in not_reviewed_articles:
            title = article.get("title", "Без названия")
            article_id = article.get("article_id")
            date_sub = article.get("date_sub") or "не указана"
            pdf_url = article.get("pdf_url") or "отсутствует"

            lines.append(
                f"- {title} (article_id={article_id}, date_sub={date_sub}, pdf_url={pdf_url})"
            )

    lines.append("")
    return "\n".join(lines)


def save_digest(
    content: str,
    username: str,
    filter_type: str = "all",
    output_dir: Optional[Path] = None,
) -> Tuple[Path, str]:
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
    filters = FavoritesDigestFilters(
        start_date=start_date,
        end_date=end_date,
    )

    digest_json = build_favorites_reviews_digest_payload(
        username=username,
        favorites=favorites_with_reviews,
        filters=filters,
    )

    md_content = render_reviews_digest_to_markdown(digest_json)

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
    output_dir: Optional[Path] = None) -> Dict[str, Any]:
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
    created_at = result["created_at"].replace(":", "-")
    filename = f"digest_{us_name}_{created_at}.md"

    result["file_name"] = filename
    await obs_loader.upload_digest_markdown_content(result["digest_markdown"], filename)
    print(f"Дайджест {filename} добавлен")

    return result


__all__ = [
    "FavoritesDigestFilters",
    "QueryDateRange",
    "build_favorites_reviews_digest_payload",
    "build_user_favorites_review_digest",
    "build_user_favorites_review_digest_from_query",
    "extract_date_filters_from_query",
    "extract_json",
    "filter_favorites_by_date",
    "normalize_review_item",
    "parse_date_safe",
    "render_reviews_digest_to_markdown",
    "save_digest",
]