import re
import json
import logging
import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from .email_sender import EmailSender
from .core.config import SMTP_HOST, SMTP_PORT, SMTP_LOGIN, SMTP_PASSWORD
from .summary_period import agent_composite_digest_tool

logger = logging.getLogger(__name__)

RECIPIENTS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "recipients.json"


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

        s3_key = asyncio.run(obs_loader.upload_single_digest_cond(digest_file, filename))

        bucket_endpoint = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru"

        obs_url = f"{bucket_endpoint}/{s3_key}"

        body_text = (
            "Здравствуйте!\n\n"
            "Это письмо из системы AgentPM.\n\n"
            f"Дайджест можно найти по ссылке: {obs_url}.\n\n"
            "С уважением,\nAgentPM"
        )

        send_result = self.email_sender.send_digest(
            to_addresses=[recipient_email],
            subject="AgentPM",
            body_html=body_text,
            # attachment_path=digest_file if digest_file else None,
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

    @staticmethod
    def _load_recipients() -> List[str]:
        """Загружает список получателей из config/recipients.json."""
        if not RECIPIENTS_CONFIG.exists():
            logger.warning(f"Файл получателей не найден: {RECIPIENTS_CONFIG}")
            return []
        try:
            with open(RECIPIENTS_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)
            recipients = data.get("recipients", [])
            # Фильтруем пустые строки и невалидные адреса
            valid = [r.strip() for r in recipients if r.strip() and "@" in r]
            return valid
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Ошибка чтения recipients.json: {e}")
            return []

    def send_auto_digest(self, digest_files: List[str], obs_loader) -> Dict[str, str]:
        """
        Автоматическая отправка новых дайджестов после парсинга.

        Склеивает все дайджесты в один .md файл и отправляет
        через EmailSender (который сам делает zip.txt).
        """
        recipients = self._load_recipients()
        if not recipients:
            logger.info("Автоотправка: список получателей пуст, пропускаем")
            return {"status": "skipped", "message": "Нет получателей в recipients.json"}

        existing_files = [f for f in digest_files if Path(f).exists()]
        if not existing_files:
            logger.info("Автоотправка: нет файлов дайджестов для отправки")
            return {"status": "skipped", "message": "Нет файлов для отправки"}

        count = len(existing_files)

        # Склеиваем все дайджесты в один .md
        today = datetime.now().strftime("%Y-%m-%d")
        merged_path = Path(tempfile.gettempdir()) / f"digests_{today}.md"
        try:
            with open(merged_path, "w", encoding="utf-8") as out:
                for filepath in existing_files:
                    p = Path(filepath)
                    category = p.parent.name  # Без категории, Общие_принципы, ...
                    out.write(f"## Категория: {category}\n\n")
                    out.write(p.read_text(encoding="utf-8"))
                    out.write("\n\n---\n\n")

            # Загрузка склеенного дайджеста в OBS
            obs_url = asyncio.run(obs_loader.upload_single_digest_cond(str(merged_path), f"digests_{today}"))
            if not obs_url:
                return {
                    "status": "error",
                    "message": "Не удалось загрузить склеенный дайджест в хранилище.",
                }
            
            body_text = (
                f"Здравствуйте!\n\n"
                f"Это автоматическое письмо из системы AgentPM.\n\n"
                f"После парсинга сформировано новых дайджестов: {count}.\n"
                f"Все дайджесты собраны в один файл по ссылке: {obs_url}\n\n"
                f"С уважением,\nAgentPM"
            )

            result = self.email_sender.send_digest(
                to_addresses=recipients,
                subject=f"AgentPM: новые дайджесты ({count} шт.)",
                body_html=body_text,
                # attachment_path=str(merged_path),
            )

            logger.info(f"Автоотправка: {result}")
            return result

        except Exception as e:
            logger.error(f"Ошибка автоотправки дайджестов: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            merged_path.unlink(missing_ok=True)
