"""
tools/send_messages/tool_send_email.py
───────────────────────────────────────
Tool для агента: принимает список review_ids, тянет данные из БД,
формирует Markdown-дайджест, загружает в OBS и отправляет письмо
со ссылкой + файлом во вложении.

Сценарий работы агента:
  1. execute_sql_query → получить нужные review_ids
  2. send_digest_email(emails=[...], review_ids=[101, 105], comment="...")
"""

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from agent_pm.email_sender import EmailSender
from agent_pm.core.config import SMTP_HOST, SMTP_PORT, SMTP_LOGIN, SMTP_PASSWORD

logger = logging.getLogger(__name__)

# ── SQL ────────────────────────────────────────────────────────────────────────

_SQL_FETCH_REVIEWS = """
    SELECT
        r.id           AS review_id,
        r.external_id,
        pm.title,
        pm.date_sub,
        pm.category,
        pm.authors,
        pm.url_article,
        r.review_text,
        r.created_at   AS review_created
    FROM reviews r
    JOIN papers_metadata pm ON r.external_id = pm.external_id
    WHERE r.id = ANY($1::bigint[])
    ORDER BY pm.date_sub DESC NULLS LAST
"""


# ── Markdown builder ───────────────────────────────────────────────────────────

def _build_markdown(rows: list[dict], comment: str) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    parts: list[str] = []

    parts.append(f"# AgentPM — Дайджест ревью\n")
    parts.append(f"_Сформирован: {now}_\n")

    if comment:
        parts.append(f"\n> {comment}\n")

    parts.append(f"\nКоличество статей: **{len(rows)}**\n")
    parts.append("---\n")

    for i, row in enumerate(rows, 1):
        title    = row.get("title") or "Без названия"
        date_sub = row.get("date_sub") or "—"
        category = row.get("category") or "—"
        authors  = row.get("authors")
        url      = row.get("url_article") or ""
        review   = row.get("review_text") or "—"

        # authors может быть JSONB-списком или строкой
        if isinstance(authors, list):
            authors_str = ", ".join(authors)
        elif isinstance(authors, str):
            authors_str = authors
        else:
            authors_str = "—"

        parts.append(f"## {i}. {title}\n")
        parts.append(f"- **Дата:** {date_sub}")
        parts.append(f"- **Категория:** {category}")
        parts.append(f"- **Авторы:** {authors_str}")
        if url:
            parts.append(f"- **Ссылка:** [{url}]({url})")
        parts.append(f"\n### Ревью\n\n{review}\n")
        parts.append("---\n")

    return "\n".join(parts)


# ── DigestEmailHandler ─────────────────────────────────────────────────────────

class DigestEmailHandler:
    def __init__(self, email_sender: Optional[EmailSender] = None):
        self.email_sender = email_sender or EmailSender(
            smtp_host=SMTP_HOST,
            smtp_port=SMTP_PORT,
            smtp_login=SMTP_LOGIN,
            smtp_password=SMTP_PASSWORD,
        )

    async def execute(
        self,
        emails: List[str],
        review_ids: List[int],
        comment: str,
        database,
        obs_loader,
    ) -> Dict[str, Any]:

        if not emails:
            return {"status": "error", "message": "Список получателей пуст."}

        if comment and not review_ids:
            send_result = self.email_sender.send_digest(
                to_addresses=emails,
                subject="AgentPM",
                body_html=comment,
                attachment_path=None,
            )

            if send_result["status"] == "ok":
                recipients_str = ", ".join(emails)
                return {
                    "status": "success",
                    "message": (
                        f"Письмо долетело по адресам {[emails]}"
                    ),
                }

        
        if not review_ids:
            return {"status": "error", "message": "Список review_ids пуст."}

        # 1. Тянем данные из БД ────────────────────────────────────────────────
        async with database.pool.acquire() as conn:
            rows = await conn.fetch(_SQL_FETCH_REVIEWS, review_ids)

        if not rows:
            return {
                "status": "error",
                "message": f"По review_ids {review_ids} ничего не найдено. Проверь JOIN reviews → papers_metadata.",
            }

        rows = [dict(r) for r in rows]
        logger.info("Digest: получено %d записей по %d ID", len(rows), len(review_ids))

        # 2. Формируем Markdown ────────────────────────────────────────────────
        md_text  = _build_markdown(rows, comment)
        filename = f"digest_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"

        # 3. Пишем во временный файл ──────────────────────────────────────────
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False,
            encoding="utf-8", prefix="agentpm_digest_"
        ) as f:
            f.write(md_text)
            tmp_path = Path(f.name)

        obs_url: Optional[str] = None
        attachment_path: Optional[Path] = tmp_path

        # 4. Загружаем в OBS (опционально) ────────────────────────────────────
        try:
            s3_key  = await obs_loader.upload_single_digest_cond(str(tmp_path), filename)
            bucket  = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru"
            obs_url = f"{bucket}/{s3_key}"
            logger.info("Digest загружен в OBS: %s", obs_url)
        except Exception as e:
            logger.warning("OBS upload failed, отправляем только файлом: %s", e)

        # 5. Формируем тело письма ────────────────────────────────────────────
        body_lines = [
            "Здравствуйте!\n",
            "Это письмо из системы <b>AgentPM</b>.\n",
        ]
        if comment:
            body_lines.append(f"<i>{comment}</i>\n")

        body_lines.append(f"Дайджест содержит <b>{len(rows)}</b> статей.\n")

        if obs_url:
            body_lines.append(f'Ссылка на дайджест: <a href="{obs_url}">{obs_url}</a>\n')

        body_lines.append("Markdown-файл прикреплён к письму.\n\nС уважением,\nAgentPM")
        body_html = "\n".join(body_lines)

        # 6. Отправляем ───────────────────────────────────────────────────────
        send_result = self.email_sender.send_digest(
            to_addresses=emails,
            subject=f"AgentPM: Дайджест ревью ({len(rows)} статей)",
            body_html=body_html,
            attachment_path=str(attachment_path),   # всегда шлём файл
        )

        # Чистим временный файл
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

        if send_result["status"] == "ok":
            recipients_str = ", ".join(emails)
            return {
                "status": "success",
                "message": (
                    f"Дайджест по {len(rows)} статьям отправлен на {recipients_str}."
                    + (f" OBS: {obs_url}" if obs_url else "")
                ),
            }

        return {
            "status": "error",
            "message": f"Дайджест сформирован, но ошибка отправки: {send_result.get('message', '?')}",
        }

sender = DigestEmailHandler()

# ── Tool factory ───────────────────────────────────────────────────────────────

def make_send_digest_email_tool(agent):

    @tool
    async def send_digest_email(
        emails: list[str],
        review_ids: list[int] | None,
        comment: str = "",
    ) -> str:
        """
        The main purpose: to generate a digest and send it via email.

        Parameters:
          emails      — a list of recipient addresses, for example ["user@mail.ru"]
          review_ids  — a list of IDs from the reviews table (bigint).
                        Get them in advance using execute_sql_query:
                        SELECT id FROM reviews r
                        JOIN papers_metadata pm ON r.external_id = pm.external_id
                        WHERE <your filter>
          comment     — an optional comment at the beginning of the message

        What it does:
          1. Pulls records from reviews + papers_metadata based on the passed IDs
          2. Generates a Markdown digest
          3. Uploads to OBS and gets a link
          4. Sends an email with a link + MD file attached

        Example of a call:
            send_digest_email(
                emails=["maria@example.com"],
                review_ids=[101, 105, 108],
                comment="Maria, a selection of articles on Process Mining for July."
)

        Secondary purpose: the tool is capable of sending emails; the comment text is embedded in the email.
        To do this -> review_ids should be left as None.
        """
        result = await sender.execute(
            emails=emails,
            review_ids=review_ids,
            comment=comment,
            database=agent.db,
            obs_loader=agent.obs_loader,
        )
        return result.get("message", "Готово.")

    return send_digest_email