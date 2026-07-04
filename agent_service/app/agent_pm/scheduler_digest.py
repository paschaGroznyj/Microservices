import os
import time
import json
import logging
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .core.agent_provider import get_async_gemini
from .core.botocore_client import OBSWorker
from .email_sender import EmailSender
from .core.config import (SMTP_HOST, SMTP_PORT, 
                          SMTP_LOGIN, SMTP_PASSWORD, 
                          DIGEST_RECIPIENTS, DIGEST_ENABLED, 
                          DIGEST_UPLOAD_TO_OBS, DIGEST_SCHEDULE_HOUR,
                          DIGEST_SCHEDULE_MINUTE, DIGEST_SCHEDULE_DAY_OF_WEEK,
                          DIGEST_TIMEZONE, DIGEST_RUN_MODE)
from .summary_period import load_json_metadata, date_filter, build_digest_content, save_digest

logger = logging.getLogger("scheduled_digest")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


def parse_recipients(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace(";", ",").replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def send_digest_to_many(
        recipients: List[str],
        filename: str,
        email_sender: Optional[EmailSender] = None,
        base_download_url: str = "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/",
) -> Dict[str, Any]:
    if not recipients:
        return {
            "email_requested": False,
            "email_sent": False,
            "email_message": "Список получателей пуст.",
            "recipients": [],
        }

    if not filename:
        return {
            "email_requested": True,
            "email_sent": False,
            "email_message": "Не найдено имя файла дайджеста.",
            "recipients": recipients,
        }

    full_link = f"{base_download_url}{filename}"

    body_text = (
        "Здравствуйте!\n\n"
        "Автоматический дайджест за последние 2 недели сформирован.\n\n"
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
        to_addresses=recipients,
        subject="AgentPM: автоматический дайджест за последние 2 недели",
        body_html=body_text,
    )
    
    return {
        "email_requested": True,
        "email_sent": send_result.get("status") == "ok",
        "email_message": send_result.get("message", ""),
        "recipients": recipients,
        "download_link": full_link,
    }


def create_last_2_weeks_digest(
        json_path: str,
        md_folder: str,
        output_dir: str,
        llm_client=None,
        lookback_days: int = 14,
) -> Dict[str, Any]:
    articles = load_json_metadata(json_path)
    if not articles:
        return {"status": "error", "message": "Нет статей в metadata.json"}

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days)

    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    filtered = date_filter(articles, start_date, end_date)
    query_label = f"Автоматический дайджест за период {start_date} - {end_date}"

    full_text = build_digest_content(
        articles=filtered,
        md_path=Path(md_folder),
        query=query_label,
        llm_client=llm_client,
    )

    filter_slug = f"auto_last_{lookback_days}_days_{start_date}_{end_date}"
    output_path, filename = save_digest(
        content=full_text,
        filter_type=filter_slug,
        output_dir=Path(output_dir),
    )

    return {
        "status": "created",
        "created_at": datetime.now().isoformat(),
        "found_articles": len(filtered),
        "start_date": start_date,
        "end_date": end_date,
        "digest_file": str(output_path),
        "filename": filename,
    }


def upload_digest_to_obs(output_path: str, filename: str) -> Dict[str, Any]:
    if not output_path or not filename:
        return {
            "obs_upload_requested": False,
            "obs_uploaded": False,
            "obs_message": "Не хватает output_path или filename для загрузки в OBS.",
        }

    try:
        output_path_str = str(output_path)
        filename_str = Path(str(filename)).name
        obs_loader = OBSWorker()
        asyncio.run(obs_loader.upload_single_digest_cond(output_path_str, filename_str))
        return {
            "obs_upload_requested": True,
            "obs_uploaded": True,
            "obs_message": "Дайджест успешно загружен в OBS.",
        }
    except Exception as exc:
        logger.exception("Ошибка загрузки дайджеста в OBS")
        return {
            "obs_upload_requested": True,
            "obs_uploaded": False,
            "obs_message": f"Ошибка загрузки в OBS: {exc}",
        }


def run_scheduled_digest_job(
        llm_client=None,
        email_sender: Optional[EmailSender] = None,
) -> Dict[str, Any]:
    load_dotenv()
    logging.info("Вызов функции по таймеру для отправки дайджестов на почту")
    enabled = DIGEST_ENABLED.lower() == "true"
    if not enabled:
        result = {"status": "skipped", "message": "Автодайджест отключен"}
        logger.info(result)
        return result

    recipients = parse_recipients(DIGEST_RECIPIENTS)
    lookback_days = int(os.getenv("DIGEST_LOOKBACK_DAYS", 14))

    base_download_url = os.getenv(
        "DIGEST_BASE_DOWNLOAD_URL",
        "https://cds-iad.obs.ru-moscow-1.hc.sbercloud.ru/PM/digests/",
    )

    base_path = Path(__file__).resolve().parent
    json_path = os.getenv("DIGEST_JSON_PATH", str(base_path / "knowledge_base" / "metadata.json"))
    md_folder = os.getenv("DIGEST_MD_FOLDER", str(base_path / "digests"))
    output_dir = os.getenv("DIGEST_OUTPUT_DIR", str(base_path / "digests_all"))

    logger.info(
        "Старт автодайджеста | lookback_days=%s | recipients=%s | json_path=%s",
        lookback_days,
        recipients,
        json_path,
    )

    if llm_client is None:
        llm_client = get_async_gemini()

    result = create_last_2_weeks_digest(
        json_path=json_path,
        md_folder=md_folder,
        output_dir=output_dir,
        llm_client=llm_client,
        lookback_days=lookback_days,
    )

    if result.get("status") != "created":
        logger.error("Ошибка создания дайджеста: %s", result)
        return result

    obs_enabled = DIGEST_UPLOAD_TO_OBS.lower() == "true"
    if obs_enabled:
        obs_result = upload_digest_to_obs(
            output_path=str(result["digest_file"]),
            filename=str(result["filename"]),
        )
    else:
        obs_result = {
            "obs_upload_requested": False,
            "obs_uploaded": False,
            "obs_message": "Загрузка в OBS отключена через DIGEST_UPLOAD_TO_OBS.",
        }

    result.update(obs_result)

    email_result = send_digest_to_many(
        recipients=recipients,
        filename=result["filename"],
        email_sender=email_sender,
        base_download_url=base_download_url,
    )
    result.update(email_result)

    logger.info("Автодайджест завершен: %s", result)
    return result


async def start_digest_scheduler():
    load_dotenv()

    hour = int(DIGEST_SCHEDULE_HOUR)
    minute = int(DIGEST_SCHEDULE_MINUTE)
    day_of_week = DIGEST_SCHEDULE_DAY_OF_WEEK
    timezone = DIGEST_TIMEZONE

    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_scheduled_digest_job,
        trigger=CronTrigger(
            day_of_week=day_of_week,
            hour=hour,
            minute=minute,
            timezone=timezone,
        ),
        id="scheduled_digest_last_2_weeks",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    logger.info(
        "Планировщик запущен (AsyncIO) | day_of_week=%s | hour=%s | minute=%s | timezone=%s",
        day_of_week,
        hour,
        minute,
        timezone,
    )
    scheduler.start()
    return scheduler

from apscheduler.triggers.interval import IntervalTrigger

async def start_interval_digest_scheduler():
    load_dotenv()

    interval_hours = int(os.getenv("DIGEST_INTERVAL_HOURS", 0))
    interval_minutes = int(os.getenv("DIGEST_INTERVAL_MINUTES", 0))
    interval_seconds = int(os.getenv("DIGEST_INTERVAL_SECONDS", 30))
    timezone = DIGEST_TIMEZONE

    scheduler = AsyncIOScheduler(timezone=timezone)
    scheduler.add_job(
        run_scheduled_digest_job,
        trigger=IntervalTrigger(
            hours=interval_hours,
            minutes=interval_minutes,
            seconds=interval_seconds,
            timezone=timezone,
        ),
        id="interval_digest_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    logger.info(
        "Планировщик запущен (интервальный) | hours=%s | minutes=%s | seconds=%s | timezone=%s",
        interval_hours,
        interval_minutes,
        interval_seconds,
        timezone,
    )
    scheduler.start()
    return scheduler

if __name__ == "__main__":
    load_dotenv()
    run_mode = DIGEST_RUN_MODE.lower()

    if run_mode == "once":
        result = run_scheduled_digest_job()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        asyncio.run(start_digest_scheduler())
