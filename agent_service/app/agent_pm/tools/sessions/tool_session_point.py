"""
tools/session/tool_session_point.py
─────────────────────────────────────
Тул-триггер для агента: фиксирует начало и конец сессии.

Подключение в langchain_agent.py:
    from tools.session.tool_session_point import make_session_point_tool
    session_point = make_session_point_tool(agent)
    # добавить в список tools
"""

import logging
from langchain.tools import tool

log = logging.getLogger(__name__)


def make_session_point_tool(agent):

    @tool
    async def session_point(chat_id: int, point_type: str, session_id: int = 0) -> str:
        """
        Фиксирует точку сессии — начало или конец диалога.
        Используй этот инструмент когда:
          - пользователь начинает новый разговор по новой теме (point_type="start")
          - разговор завершён или пользователь прощается, фиксирует сесиию, уходит вообще в другую тему (point_type="end")

        Параметры:
          chat_id    — ID чата (берётся из контекста сообщения)
          point_type — "start" или "end"
          session_id — обязателен для point_type="end" (вернулся при start).

        При start:
          - Создаёт запись в session_summaries
          - Генерит 6-значный session_id
          - Возвращает session_id — запомни его до конца сессии

        При end:
          - Берёт диапазон сообщений [start_msg_id..now]
          - LLM формирует саммари напрямую (не через агентный цикл)
          - Сохраняет summary_text в session_summaries
        
        Если забыл session_id, подсмотри в session_summaries
        """
        if point_type == "start":
            try:
                result = await agent.db.session_start(chat_id=chat_id)
                return (
                    f"Сессия начата. session_id={result['session_id']} "
                    f"start_msg_id={result['start_msg_id']}. "
                    f"Запомни session_id={result['session_id']} до конца сессии."
                )
            except Exception as e:
                log.error("session_point start failed: %s", e)
                return f"Ошибка старта сессии: {e}"

        elif point_type == "end":
            if not session_id:
                return "Для point_type='end' передай session_id (получен при start)."
            try:
                result = await agent.db.session_end(
                    chat_id=chat_id,
                    session_id=session_id,
                    llm=agent.llm,
                )
                preview = result["summary_text"][:120] + "..." \
                    if len(result["summary_text"]) > 120 else result["summary_text"]
                return f"Сессия завершена. Саммари: {preview}"
            except Exception as e:
                log.error("session_point end failed: %s", e)
                return f"Ошибка завершения сессии: {e}"

        else:
            return "Неверный point_type. Используй 'start' или 'end'."

    return session_point
