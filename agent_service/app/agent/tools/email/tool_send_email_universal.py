"""
tools/email/tool_send_email_universal.py
─────────────────────────────────────────
Универсальный tool отправки письма для агента.

В отличие от send_digest_email (заточен под PM-дайджесты из БД + OBS) и
work_with_email (докстринг-инструкция под mailcow env в песочнице), этот
тул — простой рабочий инструмент «отправить письмо»:

  • текст письма формирует сам агент (body);
  • список получателей (to);
  • произвольные вложения из /app/generated_files;
  • файлы прикрепляются КАК ЕСТЬ (без зипования), MIME по расширению;
  • жёсткий лимит 20 МБ на КАЖДЫЙ файл и 20 МБ суммарно.

SMTP берётся из core.config (SMTP_* — по умолчанию yandex, порт 587
STARTTLS). Тул универсальный → регистрируется в UNIVERSAL_TOOLS и
доступен всегда.
"""

import logging
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

from langchain.tools import tool

logger = logging.getLogger(__name__)

# Разрешённые корни для вложений — те же, что у send_file_to_user
ALLOWED_ATTACH_ROOTS = ["/app/generated_files"]

MAX_FILE_SIZE = 20 * 1024 * 1024        # 20 МБ на один файл
MAX_TOTAL_SIZE = 20 * 1024 * 1024       # 20 МБ суммарно на письмо


def _valid_attach_path(path: str) -> Optional[str]:
    """Возвращает абсолютный путь, если он внутри разрешённых корней и
    существует как файл; иначе None."""
    abs_path = os.path.realpath(path)
    inside = any(
        abs_path.startswith(os.path.realpath(r) + os.sep)
        for r in ALLOWED_ATTACH_ROOTS
    )
    if not inside:
        return None
    if not os.path.isfile(abs_path):
        return None
    return abs_path


def make_send_email_tool(agent=None):

    from ...core.config import SMTP_HOST, SMTP_PORT, SMTP_LOGIN, SMTP_PASSWORD

    @tool
    async def send_email(
        to: List[str],
        subject: str,
        body: str,
        attachment_paths: Optional[List[str]] = None,
    ) -> str:
        """
        Send an email on behalf of the agent.

        Universal email sending tool: you write the email text yourself,
        specify the recipients, and (optionally) attach files.

        Arguments:
            to: list of email addresses of the recipients (one is enough).
            subject: subject of the email.
            body: email text (plain text, you write it yourself).
            attachment_paths: a list of paths to the attachment files. The files should
                lie strictly inside /app/generated_files (results
                execute_python_code / downloaded from workspace)
                (drafts). They are attached as is. Limitation: each file
                no more than 20 MB And the total size of all attachments is no more
                20 MB. If None— it is an email without attachments.

        Returns a report string: to whom the email was sent and which files are attached,
        or a clear reason for an error (empty list, exceeding the limit,
        file outside allowed folders, SMTP error).
        """
        # ── Валидация получателей ──────────────────────────────────────────
        if isinstance(to, str):
            to = [to]
        recipients = [a.strip() for a in (to or []) if a and a.strip()]
        if not recipients:
            return "Ошибка: список получателей пуст. Укажи хотя бы один email."

        if not subject or not subject.strip():
            subject = "(без темы)"

        if not SMTP_LOGIN or not SMTP_PASSWORD:
            return ("Ошибка: SMTP-креденшалы не настроены (SMTP_LOGIN / "
                    "SMTP_PASSWORD пусты в окружении). Письмо не отправлено.")

        # ── Валидация вложений ─────────────────────────────────────────────
        resolved: List[Path] = []
        total = 0
        for raw in (attachment_paths or []):
            ap = _valid_attach_path(raw)
            if ap is None:
                return (f"Ошибка: вложение '{raw}' недоступно — файл не найден "
                        f"или лежит вне разрешённых папок "
                        f"({', '.join(ALLOWED_ATTACH_ROOTS)}). Ничего не отправлено.")
            size = os.path.getsize(ap)
            if size > MAX_FILE_SIZE:
                return (f"Ошибка: файл '{os.path.basename(ap)}' весит "
                        f"{size / 1024 / 1024:.1f} МБ, лимит на один файл — 20 МБ. "
                        f"Ничего не отправлено.")
            total += size
            resolved.append(Path(ap))

        if total > MAX_TOTAL_SIZE:
            return (f"Ошибка: суммарный размер вложений "
                    f"{total / 1024 / 1024:.1f} МБ превышает лимит 20 МБ на письмо. "
                    f"Убери часть файлов. Ничего не отправлено.")

        # ── Сборка письма ──────────────────────────────────────────────────
        msg = EmailMessage()
        msg["Subject"] = subject.strip()
        msg["From"] = SMTP_LOGIN
        msg["To"] = ", ".join(recipients)
        msg.set_content(body or "")

        for fp in resolved:
            ctype, encoding = mimetypes.guess_type(str(fp))
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            with open(fp, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype=maintype,
                    subtype=subtype,
                    filename=fp.name,
                )

        # ── Отправка (STARTTLS для порта 587, SSL для 465) ─────────────────
        try:
            context = ssl.create_default_context()
            if int(SMTP_PORT) == 465:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as srv:
                    srv.login(SMTP_LOGIN, SMTP_PASSWORD)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as srv:
                    srv.ehlo()
                    srv.starttls(context=context)
                    srv.ehlo()
                    srv.login(SMTP_LOGIN, SMTP_PASSWORD)
                    srv.send_message(msg)
        except smtplib.SMTPAuthenticationError:
            logger.exception("SMTP auth error")
            return "Ошибка: не удалось авторизоваться на SMTP-сервере (проверь логин/пароль)."
        except smtplib.SMTPRecipientsRefused as e:
            logger.exception("Recipients refused")
            return f"Ошибка: сервер отклонил адреса получателей: {e.recipients}"
        except smtplib.SMTPDataError as e:
            logger.exception("SMTP data error")
            return f"Ошибка: письмо отклонено сервером (возможно, спам-фильтр): {e}"
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.exception("SMTP connection error")
            return f"Ошибка подключения к SMTP-серверу: {e}"
        except Exception as e:
            logger.exception("Email send error")
            return f"Ошибка при отправке письма: {e}"

        files_note = ""
        if resolved:
            names = ", ".join(fp.name for fp in resolved)
            files_note = (f" Вложения ({len(resolved)}, "
                          f"{total / 1024 / 1024:.2f} МБ): {names}.")
        return (f"Письмо отправлено на {', '.join(recipients)}. "
                f"Тема: «{subject.strip()}».{files_note}")

    return send_email
