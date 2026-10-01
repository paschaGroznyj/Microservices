"""
tools/telegram/tool_manage_trigger.py
─────────────────────────────────────
Universal тул: агент сам ставит себе Telegram-триггеры (отложенные само-запуски).

Слушатель chats_service каждую минуту сканирует telegram_triggers и в нужное время
МСК дёргает агента с заданным prompt. Значит агент может запланировать себе
будущее действие (напомнить, сгенерить, саморефлексия и т.д.).

Подключение в langchain_agent.py:
    from .tools.telegram.tool_manage_trigger import make_manage_trigger_tool
    manage_trigger = make_manage_trigger_tool(agent, chat_id, user_id)
"""

import logging
from datetime import datetime, date
try:
    from zoneinfo import ZoneInfo
    _MSK = ZoneInfo("Europe/Moscow")
except Exception:  # pragma: no cover
    _MSK = None

from langchain.tools import tool

log = logging.getLogger(__name__)

_DOW = {1: "пн", 2: "вт", 3: "ср", 4: "чт", 5: "пт", 6: "сб", 7: "вс"}


def _valid_hhmm(s: str) -> bool:
    try:
        h, m = s.split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59 and len(m) == 2
    except Exception:
        return False


def make_manage_trigger_tool(agent, chat_id: str, user_id):

    @tool
    async def manage_trigger(
        action: str,
        kind: str = "once",
        time_msk: str = "",
        prompt: str = "",
        title: str = "",
        trigger_date: str = "",
        days_of_week: list[int] | None = None,
        trigger_id: int = 0,
        addressed: bool = False,
    ) -> str:
        """
        Scheduler for your own delayed launches (Telegram triggers).
        Allows you to SCHEDULE a future action in this chat yourself: the listener
        checks the time in Moscow every minute and, at the right moment, will wake you up
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
          time_msk   — launch time in Moscow in the format "HH:MM" (required for create)
          prompt     — what you will need to do when the trigger is activated
                       (required for create) — this is the text that will be returned to you
          title      — a short label for the list (optional)
          trigger_date — the date for kind=once in the format "YYYY-MM-DD" (MSK).
                         If not specified, today’s MSK date is used.
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

        IMPORTANT: triggers only work in Telegram chats with proactive mode enabled.
        Time is always interpreted as MSK. Minute granularity.
        """
        try:
            act = (action or "").strip().lower()

            # текущее время МСК
            now_msk = datetime.now(_MSK) if _MSK else datetime.utcnow()

            if act == "list":
                rows = await agent.db.list_tg_triggers(str(chat_id))
                if not rows:
                    return "Активных триггеров нет."
                lines = []
                for r in rows:
                    when = r["time_msk"]
                    if r["kind"] == "once":
                        when = f"{r['trigger_date']} {r['time_msk']} МСК (разово)"
                    else:
                        dw = r.get("days_of_week") or []
                        days = ",".join(_DOW.get(d, str(d)) for d in dw) if dw else "каждый день"
                        when = f"{r['time_msk']} МСК ({days})"
                    lines.append(f"#{r['id']} [{r['kind']}] {when} — {r.get('title') or ''}: {r['prompt'][:80]}")
                return "Активные триггеры:\n" + "\n".join(lines)

            if act == "cancel":
                if not trigger_id:
                    return "Для cancel нужен trigger_id (см. action=list)."
                ok = await agent.db.deactivate_tg_trigger(int(trigger_id), str(chat_id))
                return f"Триггер #{trigger_id} отменён." if ok else f"Триггер #{trigger_id} не найден в этом чате."

            if act == "create":
                if not _valid_hhmm(time_msk):
                    return "Нужно time_msk в формате HH:MM (МСК), например 09:30."
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
                        td = now_msk.date()
                    # не ставим в прошлое на сегодня
                    if td == now_msk.date() and time_msk <= now_msk.strftime("%H:%M"):
                        return (f"Время {time_msk} МСК на {td} уже прошло "
                                f"(сейчас {now_msk.strftime('%H:%M')} МСК). Задай позже или другую дату.")
                else:  # recurring
                    if days_of_week:
                        dow = [int(d) for d in days_of_week if 1 <= int(d) <= 7]
                        if not dow:
                            return "days_of_week: допустимы только числа 1..7 (1=пн..7=вс)."

                row = await agent.db.create_tg_trigger(
                    chat_id=str(chat_id),
                    added_by_user_id=int(user_id) if user_id else None,
                    kind=k, time_msk=time_msk,
                    title=(title or None), days_of_week=dow,
                    trigger_date=td, prompt=prompt.strip(),
                    addressed=bool(addressed),
                )
                if k == "once":
                    when = f"{row['trigger_date']} в {row['time_msk']} МСК"
                else:
                    days = ",".join(_DOW.get(d, str(d)) for d in (dow or [])) if dow else "каждый день"
                    when = f"{row['time_msk']} МСК ({days})"
                return (f"Триггер #{row['id']} создан [{k}], сработает {when}. "
                        f"При срабатывании выполню: {prompt.strip()[:120]}")

            return "Неверный action. Используй create | list | cancel."

        except Exception as e:
            log.error("manage_trigger failed: %s", e)
            return f"Ошибка работы с триггером: {e}"

    return manage_trigger
