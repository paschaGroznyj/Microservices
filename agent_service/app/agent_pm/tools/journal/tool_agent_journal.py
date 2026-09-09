from langchain.tools import tool

def make_journal_tool(agent, chat_id: int, user_id: int):

    @tool
    async def work_with_agent_journal(
        action: str,
        entry_type: str = "",
        content: str = "",
        replace: str = "",
        entry_id: int = 0,
        archive_summary: str = "",
    ) -> str:
        """
        Работа с бортовым журналом (agent_journal) — ПАМЯТЬ ВНУТРИ ЭТОГО ЧАТА.
        Используй, когда пользователь явно просит "запомни это", "зафиксируй",
        "не забывай про X" — или когда сам считаешь важным сохранить факт
        для будущих сообщений в этом же диалоге.

        Журнал привязан к конкретному чату — записи из других чатов не видны
        и не затрагиваются.

        АРХИВ: записи можно свернуть в архив. Архивная запись НЕ тянется в
        основной контекст (экономит токены), но остаётся видимой в компактном
        реестре "── В АРХИВЕ ──" как "ID: N — краткое описание". По этому id
        её можно вернуть обратно (unarchive).

        Аргументы:
            action: "insert"    — новая запись
                    "update"    — заменить часть текста в существующей записи
                    "archive"   — свернуть запись в архив по её id
                                  (нужны entry_id + archive_summary)
                    "unarchive" — вернуть архивную запись в активный контекст
                                  (нужен entry_id)
            entry_id: id записи журнала — ТОЛЬКО для archive/unarchive.
                      Берётся из блока "── В АРХИВЕ ──" (разархивация) или из
                      "ID: N" активной записи (архивация).
            archive_summary: одно короткое предложение о содержимом записи —
                      ОБЯЗАТЕЛЬНО для archive. Именно оно показывается в реестре
                      архива вместо полного текста.
            entry_type: тип записи (для insert/update) — один из:
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

            Свернуть запись в архив (по id из реестра):
                action="archive", entry_id=157,
                archive_summary="Ход по PoC graph-world: нарезка героя-кота и анимация ходьбы."

            Вернуть запись из архива в контекст:
                action="unarchive", entry_id=157

        НЕ пиши SQL — только эти параметры. Все операции выполняются
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
            entry_id=(entry_id or None),
            archive_summary=(archive_summary or None),
        )
        return str(result)

    return work_with_agent_journal