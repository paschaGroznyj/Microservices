from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta, tzinfo
from typing import Any, Tuple

try:
    from zoneinfo import ZoneInfo
    _MSK: tzinfo = ZoneInfo("Europe/Moscow")
except Exception:
    ZoneInfo = None  # type: ignore
    _MSK = timezone(timedelta(hours=3))

DEFAULT_TIMEZONE = "Europe/Moscow"


def resolve_tzinfo(
    timezone_name: str | None = None,
    tz_offset_minutes: int | None = None,
) -> Tuple[tzinfo, str]:
    name = (timezone_name or "").strip() or DEFAULT_TIMEZONE
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name), name
        except Exception:
            pass
    if tz_offset_minutes is not None:
        try:
            off_min = -int(tz_offset_minutes)
            tz_obj = timezone(timedelta(minutes=off_min))
            sign = "+" if off_min >= 0 else "-"
            abs_m = abs(off_min)
            label = f"UTC{sign}{abs_m // 60:02d}:{abs_m % 60:02d}"
            return tz_obj, label
        except Exception:
            pass
    return _MSK, DEFAULT_TIMEZONE


def now_in_timezone(
    timezone_name: str | None = None,
    tz_offset_minutes: int | None = None,
) -> Tuple[datetime, str]:
    tz_obj, label = resolve_tzinfo(timezone_name, tz_offset_minutes)
    return datetime.now(tz_obj), label


def format_current_datetime(
    timezone_name: str | None = None,
    tz_offset_minutes: int | None = None,
) -> Tuple[str, str]:
    now_dt, label = now_in_timezone(timezone_name, tz_offset_minutes)
    return f"{now_dt.strftime('%d.%m.%Y %H:%M, %A')} ({label})", label


async def resolve_chat_context_and_timezone(
    db: Any,
    *,
    chat_id: str,
    tg_chat_id: Any = None,
    is_telegram: bool = False,
    is_max: bool = False,
    timezone_name: str | None = None,
    tz_offset_minutes: int | None = None,
    request_ctx: dict | None = None,
) -> Tuple[str, str, dict | None, dict | None]:
    tg_settings = None
    max_settings = None
    user_tz_name = (timezone_name or "").strip() or DEFAULT_TIMEZONE
    target_id = tg_chat_id or chat_id

    if is_telegram:
        try:
            from agent.telegram.tg_integration import get_tg_chat_settings
            tg_settings = await get_tg_chat_settings(db, target_id)
            user_tz_name = (tg_settings or {}).get("timezone_name") or user_tz_name
        except Exception as e:
            logging.warning(f"[tg] get_tg_chat_settings fail: {e}")
    elif is_max:
        try:
            from agent.max.max_integration import get_max_chat_settings
            max_settings = await get_max_chat_settings(db, target_id)
            user_tz_name = (max_settings or {}).get("timezone_name") or user_tz_name
        except Exception as e:
            logging.warning(f"[max] get_max_chat_settings fail: {e}")

    current_datetime, resolved_label = format_current_datetime(user_tz_name, tz_offset_minutes)
    if isinstance(request_ctx, dict):
        request_ctx["timezone_name"] = resolved_label
        # resolved_label бывает меткой вида UTC+04:00, а не IANA-именем;
        # смещение нужно, чтобы восстановить tzinfo в инструментах.
        request_ctx["tz_offset_minutes"] = tz_offset_minutes

    return resolved_label, current_datetime, tg_settings, max_settings


def user_tzinfo(label: str | None, tz_offset_minutes: int | None = None) -> tzinfo:
    """
    tzinfo по метке из resolve_chat_context_and_timezone: это IANA-имя
    или «UTC+04:00», если браузер прислал только смещение.
    """
    name = label if isinstance(label, str) and not label.startswith("UTC") else None
    if name is None and tz_offset_minutes is None and isinstance(label, str) and label.startswith("UTC"):
        try:
            sign = 1 if label[3] == "+" else -1
            hh, mm = label[4:].split(":")
            return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))
        except Exception:
            pass
    return resolve_tzinfo(name, tz_offset_minutes)[0]


HISTORY_TIME_FORMAT = "%d.%m.%Y %H:%M"

HISTORY_TIME_NOTE = (
    "Previous user messages in the history start with [DD.MM.YYYY HH:MM]: the time they were "
    "sent, in the user's local time zone (same clock as the current date and time above). "
    "Use it to reason about elapsed time; never add such a stamp to your own answers."
)


def stamp_history_text(text: str, created_at: datetime | None, tz: tzinfo | None) -> str:
    """Метка времени отправки по часам пользователя перед прошлым сообщением."""
    if created_at is None or tz is None:
        return text
    return f"[{format_local(created_at, tz, HISTORY_TIME_FORMAT)}] {text}"


def format_local(dt: datetime | None, tz: tzinfo | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Время из БД (TIMESTAMPTZ) в часовом поясе пользователя. Naive считаем UTC."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz or _MSK).strftime(fmt)


_LOCAL_FORMATS = (
    ("%Y-%m-%d %H:%M:%S", False), ("%Y-%m-%d %H:%M", False), ("%Y-%m-%dT%H:%M", False),
    ("%d.%m.%Y %H:%M:%S", False), ("%d.%m.%Y %H:%M", False),
    ("%Y-%m-%d", True), ("%d.%m.%Y", True),
)


def parse_local_datetime(text: str, tz: tzinfo | None) -> datetime:
    """
    Разбирает время, которое модель указала по часам пользователя.

    "2026-11-25 15:00", "25.11.2026 15:00" — местное время в tz;
    "2026-11-25" / "25.11.2026" — конец этого дня (23:59:59) по местному времени;
    ISO со смещением ("2026-11-25T15:00+04:00") принимается как есть.
    Возвращает aware datetime. ValueError — формат не распознан.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("пустое время")
    tz = tz or _MSK
    try:
        iso = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if iso.tzinfo is not None:
            return iso
    except ValueError:
        pass
    for fmt, date_only in _LOCAL_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if date_only:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.replace(tzinfo=tz)
    raise ValueError(
        f"не распознано время {raw!r}; ожидается 'YYYY-MM-DD HH:MM' или 'YYYY-MM-DD' по местному времени"
    )
