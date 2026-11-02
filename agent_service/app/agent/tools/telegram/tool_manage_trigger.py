"""
tools/telegram/tool_manage_trigger.py
─────────────────────────────────────
Универсальный тул: агент сам ставит себе Telegram и MAX триггеры (отложенные само-запуски).

Слушатель каждую минуту сканирует telegram_triggers / max_triggers и в нужное время
в часовом поясе чата (по умолчанию Europe/Moscow) дёргает агента с заданным prompt.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, date, timezone, timedelta
from typing import Optional
from langchain_core.tools import tool

from ...core.timezone_utils import now_in_timezone

log = logging.getLogger("tool_manage_trigger")

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DOW = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}


def _valid_hhmm(s: str) -> bool:
    return bool(s and _HHMM_RE.match(s.strip()))


def make_manage_trigger_tool(
    agent,
    chat_id: str,
    user_id,
    tg_chat_id=None,
    is_max: bool = False,
    is_telegram: bool = False,
    timezone_name: str = "Europe/Moscow",
):
    @tool
    async def manage_trigger(
        action: str,
        kind: str = "once",
        time_msk: str = "",
        prompt: str = "",
        title: str = "",
        trigger_date: str = "",
        days_of_week: Optional[list[int]] = None,
        trigger_id: int = 0,
        addressed: bool = False,
    ) -> str:
        """
        Scheduler for your own delayed launches (Telegram and MAX triggers).
        Allows you to SCHEDULE a future action in this chat yourself: the listener
        checks the chat's local time every minute and, at the right moment, will wake you up
        with the text from the prompt field — you will complete it as a task and write in the chat.

        When to use:
          - the user asks for “remind me at 18:00”, “send a digest every morning”,
            “Generate an image tomorrow at 9,” etc.;
          - when you need to postpone an action until a specific time.
        Set the trigger ONLY upon the user’s explicit request.

        Parameters:
          action     — "create", "list", "cancel"
          kind       — "once" (one‑time, trigger_date is required) |
                       "recurring" (regular, you can use days_of_week)
          time_msk   — launch time in the chat's timezone (shown in Current date and time) in the format "HH:MM" (required for create)
          prompt     — what you will need to do when the trigger is activated
                       (required for create) — this is the text that will be returned to you
          title      — a short label for the list (optional)
          trigger_date — the date for kind=once in the format "YYYY-MM-DD".
                         If not specified, today’s date in the chat's timezone is used.
          days_of_week — for kind=recurring: a list of days 1..7 (1=mon..7=sun).
                         An empty list / None = every day.
          trigger_id — trigger ID for action="cancel"
          addressed — False (default): you write in the chat without a specific addressee.

        Examples:
          create once:      action=create, kind=once, time_msk="18:30",
                            trigger_date="2026-09-27", prompt="Remind about the call"
          create recurring: action=create, kind=recurring, time_msk="09:00",
                            days_of_week=[1,2,3,4,5], prompt="Morning PM digest"
          list:             action=list
          cancel:           action=cancel, trigger_id=42

        IMPORTANT: triggers work in Telegram and MAX chats with proactive mode enabled.
        Time is always interpreted in the chat's local timezone (indicated in Current date and time, default Europe/Moscow). Minute granularity.
        """
        try:
            act = (action or "").strip().lower()

            ctx_tg_chat_id = agent._ctx_get("tg_chat_id")
            target_chat_id = str(tg_chat_id or ctx_tg_chat_id or chat_id)
            gateway = str(agent._ctx_get("gateway_target") or ("max" if is_max else ("telegram" if is_telegram else "main"))).lower()
            use_max = bool(is_max or gateway == "max")

            ctx_tz_name = agent._ctx_get("timezone_name") or timezone_name or "Europe/Moscow"
            now_local, tz_label = now_in_timezone(ctx_tz_name)

            if act == "list":
                if use_max:
                    rows = await agent.db.list_max_triggers(target_chat_id)
                else:
                    rows = await agent.db.list_tg_triggers(target_chat_id)
                if not rows:
                    return "Активных триггеров нет."
                lines = []
                for r in rows:
                    when = r["time_msk"]
                    if r["kind"] == "once":
                        when = f"{r['trigger_date']} {r['time_msk']} ({tz_label}, разово)"
                    else:
                        dw = r.get("days_of_week") or []
                        days = ",".join(_DOW.get(d, str(d)) for d in dw) if dw else "каждый день"
                        when = f"{r['time_msk']} ({tz_label}, {days})"
                    prompt_preview = (r.get("prompt") or f"[preset: {r.get('preset_key')}]")[:80]
                    lines.append(f"#{r['id']} [{r['kind']}] {when} — {r.get('title') or ''}: {prompt_preview}")
                return "Активные триггеры:\n" + "\n".join(lines)

            if act == "cancel":
                if not trigger_id:
                    return "Для cancel нужен trigger_id (см. action=list)."
                if use_max:
                    ok = await agent.db.deactivate_max_trigger(int(trigger_id), target_chat_id)
                else:
                    ok = await agent.db.deactivate_tg_trigger(int(trigger_id), target_chat_id)
                return f"Триггер #{trigger_id} отменён." if ok else f"Триггер #{trigger_id} не найден в этом чате."

            if act == "create":
                if not _valid_hhmm(time_msk):
                    return f"Нужно time_msk в формате HH:MM ({tz_label}), например 09:30."
                time_msk = time_msk.strip()
                if not prompt or not prompt.strip():
                    return "Нужен prompt — что именно сделать когда триггер сработает."
                k = (kind or "once").strip().lower()
                if k not in ("once", "recurring"):
                    return "kind должен быть 'once' или 'recurring'."

                td = None
                dow = None
                if k == "once":
                    if trigger_date and trigger_date.strip():
                        try:
                            td = date.fromisoformat(trigger_date.strip())
                        except Exception:
                            return "trigger_date должен быть YYYY-MM-DD."
                    else:
                        td = now_local.date()
                    if td == now_local.date() and time_msk <= now_local.strftime("%H:%M"):
                        return (
                            f"Время {time_msk} ({tz_label}) на {td} уже прошло "
                            f"(сейчас {now_local.strftime('%H:%M')} {tz_label}). Задай позже или другую дату."
                        )
                else:
                    if days_of_week:
                        dow = [int(d) for d in days_of_week if 1 <= int(d) <= 7]
                        if not dow:
                            return "days_of_week: допустимы только числа 1..7 (1=пн..7=вс)."

                if use_max:
                    row = await agent.db.create_max_trigger(
                        chat_id=target_chat_id,
                        added_by_user_id=int(user_id) if user_id else None,
                        kind=k, time_msk=time_msk,
                        title=(title or None), days_of_week=dow,
                        trigger_date=td, prompt=prompt.strip(),
                        addressed=bool(addressed),
                    )
                else:
                    row = await agent.db.create_tg_trigger(
                        chat_id=target_chat_id,
                        added_by_user_id=int(user_id) if user_id else None,
                        kind=k, time_msk=time_msk,
                        title=(title or None), days_of_week=dow,
                        trigger_date=td, prompt=prompt.strip(),
                        addressed=bool(addressed),
                    )
                if k == "once":
                    when = f"{row['trigger_date']} в {row['time_msk']} ({tz_label})"
                else:
                    days = ",".join(_DOW.get(d, str(d)) for d in (dow or [])) if dow else "каждый день"
                    when = f"{row['time_msk']} ({tz_label}, {days})"
                return (
                    f"Триггер #{row['id']} создан [{k}], сработает {when}. "
                    f"При срабатывании выполню: {prompt.strip()[:120]}"
                )

            return "Неверный action. Используй create | list | cancel."

        except Exception as e:
            log.error("manage_trigger failed: %s", e)
            return f"Ошибка работы с триггером: {e}"

    return manage_trigger
