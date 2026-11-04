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

    return resolved_label, current_datetime, tg_settings, max_settings
