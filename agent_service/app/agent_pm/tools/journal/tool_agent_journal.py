from langchain.tools import tool

def make_journal_tool(agent, chat_id: int, user_id: int):

    @tool
    async def work_with_agent_journal(
        action: str,
        entry_type: str,
        content: str,
        replace: str = "",
    ) -> str:
        """
        Работа с бортовым журналом (agent_journal) — ПАМЯТЬ ВНУТРИ ЭТОГО ЧАТА.
        Используй, когда пользователь явно просит "запомни это", "зафиксируй",
        "не забывай про X" — или когда сам считаешь важным сохранить факт
        для будущих сообщений в этом же диалоге.

        Журнал привязан к конкретному чату — записи из других чатов не видны
        и не затрагиваются.

        Аргументы:
            action: "insert" — новая запись
                    "update" — заменить часть текста в существующей записи
                    "delete" — удалить существующую запись
            entry_type: тип записи — один из:
                identity        — как вести себя в этом чате, стиль, роль
                user_preference — предпочтения собеседника в этом чате
                anomaly         — замеченная странность/повторяющаяся проблема
                evolution       — что нового понял по ходу этого диалога
                summary         — сжатая сводка (обычно не создаётся тобой напрямую)
            content: текст записи (для insert/update — что писать),
                     ОБЯЗАТЕЛЕН, не может быть пустым
            replace: ТОЛЬКО для update/delete — подстрока, по которой ищем
                     существующую запись (то, что нужно найти и заменить/удалить).
                     Для insert не используется.

        Примеры:
            Запомнить новый факт:
                action="insert", entry_type="user_preference", content="Предпочитает короткие ответы без эмодзи"

            Уточнить существующую запись:
                action="update", entry_type="user_preference",
                replace="короткие ответы", content="развёрнутые ответы с примерами"

            Убрать устаревший факт:
                action="delete", entry_type="anomaly", replace="повторяющийся вопрос про X"

        НЕ пиши SQL — только эти четыре параметра. Все операции выполняются
        строго в рамках текущего чата, никакие другие записи не затрагиваются.
        """
        from .journal_handler import handle_journal_operation
        result = await handle_journal_operation(
            db=agent.db,
            llm=agent.llm_lite,
            chat_id=chat_id,
            action=action,
            entry_type=entry_type,
            content=content,
            replace=replace or None,
            user_id=user_id,
        )
        return str(result)

    return work_with_agent_journal