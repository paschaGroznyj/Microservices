"""
tool_generate_reviews.py
────────────────────────
Tool для агента: принимает список статей (external_id + pdf_url + title),
скачивает PDF, генерирует ревью через LLM и сохраняет в таблицу reviews.

Агент сам составляет SQL через get_db_schema + run_readonly_query,
получает нужный список и передаёт сюда — никакого хардкода фильтров.

Типичный сценарий в голове агента:
  1. get_db_schema()
  2. run_readonly_query("SELECT external_id, pdf_url, title, category
                         FROM papers_metadata
                         WHERE review_flag IS NOT TRUE
                           AND date_sub >= '2026-01-01'
                         LIMIT 20")
  3. generate_reviews_for_articles(articles=[...результат...])

Права для agent_readonly (добавить):
    GRANT INSERT, UPDATE ON reviews TO agent_readonly;
    GRANT USAGE, SELECT ON SEQUENCE reviews_id_seq TO agent_readonly;
    GRANT UPDATE (review_flag) ON papers_metadata TO agent_readonly;
"""

import hashlib
import logging
from datetime import datetime
from typing import Optional

from langchain.schema import HumanMessage, SystemMessage
from langchain.tools import tool

from agent_pm.download_pdf_and_make import download_and_parse_pdf
from pathlib import Path

logger = logging.getLogger(__name__)

# ── SQL ────────────────────────────────────────────────────────────────────────

_SQL_INSERT_REVIEW = """
    INSERT INTO reviews (external_id, review_text, category, source_file)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (external_id) DO UPDATE
        SET review_text = EXCLUDED.review_text,
            category    = EXCLUDED.category,
            source_file = EXCLUDED.source_file
"""

_SQL_SET_FLAG = """
    UPDATE papers_metadata SET review_flag = TRUE WHERE external_id = $1
"""

# ── Промпт ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Твоя задача: прочитать текст статьи и подготовить структурированное ревью \
для русскоязычного эксперта в области process mining.

ПРАВИЛА:
1. Академический стиль, термины на языке оригинала (process mining, SOUP и т.п.). Глоссарий: {glossary}
2. Факты только из текста — без домыслов
3. При первом упоминании: термин на оригинале. Далее — оригинал + перевод
4. Алгоритмы — пошагово с оригинальными названиями
5. Строго по шаблону, без дополнительных комментариев

ШАБЛОН:
Название статьи на русском: ...
Название оригинальной статьи: ...
Авторы (в оригинале): ...
Ссылка: {link}
Описание:
  1. Цель: ...
  2. Проблема: ...
  3. Задачи: ...
  4. Подход — суть алгоритма/метода: ...
  5. Как реализуется подход: ...
  6. Пример из статьи: ...
  7. Вывод: ...
"""

MAX_CONTENT_CHARS = 6_000
glossary = ''

base_dir = Path(__file__).resolve().parent
with open(base_dir / "glossary_pm_cloud.md", mode='r', encoding='utf-8') as f:
    glossary = f.read()


async def _generate_review(llm, content: str, title: str, link: str) -> str:
    safe = content[:MAX_CONTENT_CHARS] if content else "(текст недоступен)"
    print(f"Часть глоссария: {glossary[:100]}")
    response = llm.invoke([
        SystemMessage(content=_SYSTEM_PROMPT.format(link=link, glossary=glossary)),
        HumanMessage(content=safe),
    ])
    usage = response.response_metadata.get("token_usage", {})
    logger.info(
        "Tokens | prompt=%s cached=%s completion=%s total=%s | %.40s",
        usage.get("prompt_tokens", "?"),
        usage.get("precached_prompt_tokens", "?"),
        usage.get("completion_tokens", "?"),
        usage.get("total_tokens", "?"),
        title,
    )
    return response.content.strip()


# ── Tool ───────────────────────────────────────────────────────────────────────

def make_generate_reviews_tool(agent):
    """
    Фабрика. Подключение:
        tools.append(make_generate_reviews_tool(self))
    """

    @tool
    async def generate_reviews_for_articles(
        articles: list[dict],
        request_id: str = "",
    ) -> str:
        """
        Скачивает PDF и генерирует ревью для переданного списка статей.
        Результат сохраняется в таблицу reviews, флаг review_flag выставляется в papers_metadata.

        Параметр articles — список словарей, каждый должен содержать:
          - external_id : str        — идентификатор статьи из papers_metadata
          - pdf_url     : str        — прямая ссылка на PDF
          - title       : str        — название статьи
          - category    : str | None — категория (из papers_metadata)
          - date_sub    : str | None — дата публикации YYYY-MM-DD (для имени файла)

        Список агент получает сам через run_readonly_query — может фильтровать
        по дате, категории, relevance_score или любому другому полю схемы.

        Пример запроса агента перед вызовом этого tool:
            SELECT external_id, pdf_url, title, category, date_sub
            FROM papers_metadata
            WHERE review_flag IS NOT TRUE
              AND date_sub BETWEEN '2026-01-01' AND '2026-03-31'
              AND is_relevant = TRUE
            ORDER BY date_sub DESC
            LIMIT 15
        """
        if not articles:
            return "Список статей пуст — нечего обрабатывать."

        total = len(articles)
        success, skipped = 0, []

        await agent._send_status(f"📥 Получено {total} статей для обработки...", request_id)

        for idx, art in enumerate(articles, 1):
            external_id = art.get("external_id")
            pdf_url     = art.get("pdf_url")
            title       = art.get("title") or "Untitled"
            category    = art.get("category")
            date_sub    = art.get("date_sub") or datetime.now().strftime("%Y-%m-%d")

            # Базовая валидация — агент мог ошибиться в запросе
            if not external_id or not pdf_url:
                skipped.append({
                    "title": title,
                    "reason": f"Пропущены обязательные поля "
                              f"(external_id={external_id!r}, pdf_url={pdf_url!r})"
                })
                continue

            try:
                # 1. Скачиваем PDF → текст для LLM
                content = await download_and_parse_pdf(pdf_url)

                # 2. Генерируем ревью
                review_text = await _generate_review(agent.llm, content, title, pdf_url)

                # 3. source_file — совместимость с migrate_reviews
                url_hash    = hashlib.sha256(title.encode()).hexdigest()[:12]
                source_file = f"{date_sub}_{url_hash}.md"

                # 4. Сохраняем в БД
                async with agent.db.pool.acquire() as conn:
                    await conn.execute(
                        _SQL_INSERT_REVIEW,
                        external_id, review_text, category, source_file,
                    )
                    await conn.execute(_SQL_SET_FLAG, external_id)

                success += 1
                logger.info("Review saved: %s → %s", external_id, source_file)

            except Exception as e:
                logger.error("Ошибка %s (%s): %s", title[:50], pdf_url, e)
                skipped.append({"title": title, "url": pdf_url, "reason": str(e)})

            if idx % 3 == 0 or idx == total:
                await agent._send_status(
                    f"⏳ {idx}/{total} | ✅ {success} | ❌ {len(skipped)}", request_id
                )

        lines = [f"Готово. Обработано {total} статей: успешно {success}, ошибок {len(skipped)}."]
        if skipped:
            lines.append("\nНе удалось обработать:")
            for s in skipped:
                lines.append(f"  • {s['title'][:60]} | {s.get('url','—')} | {s['reason']}")

        return "\n".join(lines)

    return generate_reviews_for_articles