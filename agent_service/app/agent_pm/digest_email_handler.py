import re
import logging
import asyncio
from datetime import datetime
from html import escape
from typing import Dict, Any, List, Optional

from .email_sender import EmailSender
from .core.config import SMTP_HOST, SMTP_PORT, SMTP_LOGIN, SMTP_PASSWORD
from .summary_period import agent_composite_digest_tool

logger = logging.getLogger(__name__)


class DigestEmailHandler:
    """Обработчик формирования и отправки дайджестов по email."""

    def __init__(self, email_sender: Optional[EmailSender] = None):
        self.email_sender = email_sender or EmailSender(
            smtp_host=SMTP_HOST,
            smtp_port=SMTP_PORT,
            smtp_login=SMTP_LOGIN,
            smtp_password=SMTP_PASSWORD,
        )

    def execute(
        self,
        user_input: str,
        gigachat,
        obs_loader,
        name_meta_dir,
        digest_path,
    ) -> Dict[str, Any]:
        """
        Сформировать дайджест и отправить на email.

        Возвращает dict с ключами status и message (совместим с _execute в agent.py).
        """
        asyncio.run(obs_loader.run_download())
        asyncio.run(obs_loader.download_reviews())

        json_file = name_meta_dir / "metadata.json"
        results = agent_composite_digest_tool(
            llm_client=gigachat,
            query=user_input,
            json_path=json_file,
            md_folder=digest_path,
        )

        if results["found_articles"] == 0:
            return {
                "status": "error",
                "message": "Нет данных для формирования дайджеста. Невозможно отправить email.",
            }

        # Определяем получателя из текста запроса
        email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", user_input)
        if not email_match:
            return {
                "status": "error",
                "message": "Укажите email в запросе, например: отправь дайджест на user@mail.ru",
            }
        recipient_email = email_match.group(0)

        digest_file = results.get("digest_file", "")
        count = results["found_articles"]
        filename = results.get("filename", "digest")

        asyncio.run(obs_loader.upload_single_digest_cond(digest_file, filename))

        body_text = (
            "Здравствуйте!\n\n"
            "Это письмо из системы AgentPM.\n\n"
            "Во вложении находится архив с дайджестом.\n\n"
            "С уважением,\nAgentPM"
        )

        send_result = self.email_sender.send_digest(
            to_addresses=[recipient_email],
            subject="AgentPM",
            body_html=body_text,
            attachment_path=digest_file if digest_file else None,
        )

        if send_result["status"] == "ok":
            return {
                "status": "success",
                "message": f"Дайджест успешно отправлен на {recipient_email}. Обработано статей: {count}.",
            }
        else:
            return {
                "status": "error",
                "message": f"Дайджест сформирован, но ошибка отправки: {send_result.get('message', 'неизвестная ошибка')}",
            }

    def build_digest_email_html(
        self, user_input: str, results: Dict[str, Any]
    ) -> str:
        """Формирует HTML-тело письма с дайджестом."""
        composite_filters = results.get("composite_filters") or {}
        top_articles = results.get("top_articles") or []
        filename = escape(str(results.get("filename", "digest")))
        found_articles = results.get("found_articles", 0)
        generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

        filter_items = []
        date_range = composite_filters.get("date_range") or {}
        start_date = date_range.get("start_date")
        end_date = date_range.get("end_date")
        if start_date and end_date:
            filter_items.append(f"Период: {start_date} - {end_date}")

        for label, key in (
            ("Категория", "category"),
            ("Тема", "topic"),
            ("Название", "title"),
        ):
            value = composite_filters.get(key)
            if value:
                filter_items.append(f"{label}: {value}")

        if not filter_items:
            filter_items.append(
                "Дополнительные фильтры не выделены, использован исходный текст запроса."
            )

        filters_html = "".join(
            f"<li>{escape(item)}</li>" for item in filter_items
        )

        article_items = []
        for article in top_articles[:5]:
            title = escape(str(article.get("title") or "Без названия"))
            category = escape(str(article.get("category") or "Без категории"))
            publication_date = escape(str(article.get("date_sub") or "не указана"))

            tags = article.get("tags")
            if isinstance(tags, list):
                tags_text = ", ".join(str(tag) for tag in tags if tag)
            else:
                tags_text = str(tags or "")
            tags_text = escape(tags_text) if tags_text else ""

            url = str(article.get("url_article") or "").strip()
            url_html = ""
            if url and url != "N/A":
                safe_url = escape(url, quote=True)
                url_html = (
                    '<p style="margin:6px 0 0 0;">'
                    f'Источник: <a href="{safe_url}">{escape(url)}</a>'
                    "</p>"
                )

            tags_html = ""
            if tags_text:
                tags_html = (
                    f'<p style="margin:6px 0 0 0;">Ключевые слова: {tags_text}</p>'
                )

            article_items.append(
                '<li style="margin-bottom:12px;">'
                f"<b>{title}</b>"
                f'<p style="margin:6px 0 0 0;">Категория: {category} | Дата: {publication_date}</p>'
                f"{tags_html}"
                f"{url_html}"
                "</li>"
            )

        if article_items:
            articles_html = (
                '<ol style="padding-left:20px;">'
                f"{''.join(article_items)}"
                "</ol>"
            )
        else:
            articles_html = (
                "<p>Краткий список материалов не удалось сформировать в теле письма. "
                "Полный результат сохранён во вложении.</p>"
            )

        remaining_count = max(found_articles - min(len(top_articles), 5), 0)
        more_articles_html = ""
        if remaining_count:
            more_articles_html = (
                f"<p>Во вложенном файле доступно ещё {remaining_count} "
                "материалов, не показанных в этом письме.</p>"
            )

        return f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #222; line-height: 1.5;">
            <h2>Дайджест материалов AgentPM</h2>
            <p>Здравствуйте.</p>
            <p>
              По вашему запросу подготовлен дайджест материалов из внешних источников.
              Полная версия результата приложена к письму отдельным файлом.
            </p>
            <p><b>Запрос:</b> {escape(user_input)}</p>
            <p><b>Файл дайджеста:</b> {filename}</p>
            <p><b>Время формирования:</b> {generated_at}</p>
            <p><b>Количество найденных статей:</b> {found_articles}</p>

            <h3>Параметры отбора</h3>
            <ul>
              {filters_html}
            </ul>

            <h3>Материалы, вошедшие в дайджест</h3>
            <p>Ниже приведены первые материалы из выборки для быстрого ознакомления:</p>
            {articles_html}
            {more_articles_html}

            <p>
              Если письмо отображается корректно, для полной версии откройте приложенный файл.
              Если нужен другой период, тема или категория, сформируйте новый запрос.
            </p>
            <p>Это письмо отправлено автоматически сервисом AgentPM.</p>
          </body>
        </html>
        """
