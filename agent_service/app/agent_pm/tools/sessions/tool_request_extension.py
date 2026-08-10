"""
tools/session/tool_request_extension.py
"""

from langchain.tools import tool


def make_extension_tool(loop_state: dict):
    """
    loop_state — общий изменяемый dict, передаётся по ссылке из process_query,
    чтобы тулза могла повлиять на состояние текущего цикла (тот же паттерн,
    что и с files_to_send/seen_paths — closure над state текущего запроса).
    """

    @tool
    async def request_more_steps(extra_steps: int, reason: str) -> dict:
        """
        Запрашивает дополнительные шаги в текущем цикле работы, если задача
        близка к завершению, но не укладывается в оставшийся лимит.

        МОЖНО ВЫЗВАТЬ ТОЛЬКО ОДИН РАЗ за весь текущий запрос. Используй только
        если ты близко к SOFT_LIMIT и видишь, что задача почти решена, но нужно
        ещё несколько шагов (например, доделать последний файл, поправить
        ошибку в уже почти готовом результате) — не для новых, ещё не начатых
        подзадач.

        Аргументы:
            extra_steps: сколько дополнительных шагов нужно, 1-10
            reason: коротко — зачем именно (попадёт в лог, не показывается пользователю)

        Возвращает: {"granted": bool, "new_limit": int|None, "reason": str}
        Если granted=False — лимит не увеличен (либо уже использовано, либо
        запрошено слишком рано, либо extra_steps вне допустимого диапазона).
        """
        if loop_state["extension_used"]:
            return {
                "granted": False,
                "new_limit": None,
                "reason": "Расширение лимита уже использовано в этом запросе, повторный вызов невозможен.",
            }

        open_from = loop_state["soft_limit"] - 2
        if loop_state["iteration"] < open_from:
            return {
                "granted": False, "new_limit": None,
                "reason": (
                    f"Ещё рано — расширение станет доступно с шага {open_from} "
                    f"(сейчас шаг {loop_state['iteration']}). Вызови ближе к лимиту, "
                    f"когда станет ясно, что не успеваешь."
                ),
            }

        extra = max(1, min(int(extra_steps), 10))
        loop_state["hard_limit"] += extra
        loop_state["extension_used"] = True

        return {
            "granted": True,
            "new_limit": loop_state["hard_limit"],
            "reason": f"Выделено ещё {extra} шагов.",
        }

    return request_more_steps