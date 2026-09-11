from langchain.tools import tool


def make_manage_skills_tool(agent, user_id: int):
    @tool
    async def manage_skills(
        action: str,
        theme: str = "",
        body: str = "",
        tools: list[str] | None = None,
        skill_id: int = 0,
        sort_by: str = "new",
        query: str = "",
        tool_filter: str = "",
    ) -> str:
        """
        Управление библиотекой скиллов.
        action:
        - create: создать скилл (нужны theme, body, опционально tools[])
        - list: показать список скиллов (sort_by=new|popular, query, tool_filter)
        - like: лайк/анлайк скилла (нужен skill_id)
        - export_prompt: отдать готовый промпт скилла для копирования в чат
        """
        act = (action or "").strip().lower()

        if act == "create":
            if not theme.strip() or not body.strip():
                return "Для create нужны непустые theme и body."
            # Модель, которой сгенерирован скилл, берём из состояния агента
            # (проставляется в process_query перед агентным циклом).
            model_used = getattr(agent, "current_agent_model", None)
            row = await agent.db.create_skill(
                theme=theme,
                body=body,
                published_by_user_id=user_id,
                published_by_name=None,
                tools=tools or [],
                model_used=model_used,
            )
            return (
                f"Скилл создан: id={row['id']}, тема={row['theme']}, "
                f"модель={row.get('model_used') or 'неизвестна'}."
            )

        if act == "list":
            rows = await agent.db.list_skills(
                query=query,
                sort_by=sort_by,
                tool=(tool_filter or None),
                limit=20,
            )
            if not rows:
                return "Скиллы не найдены."
            out = [f"Найдено скиллов: {len(rows)}"]
            for r in rows:
                out.append(
                    f"• ID {r['id']} | ❤️ {r['likes_count']} | {r['theme']} "
                    f"| модель: {r.get('model_used') or '—'} "
                    f"| tools: {', '.join(r.get('tools') or []) or '—'}"
                )
            return "\n".join(out)

        if act == "like":
            if int(skill_id or 0) <= 0:
                return "Для like нужен skill_id > 0."
            result = await agent.db.toggle_skill_like(skill_id=int(skill_id), user_id=int(user_id))
            state = "лайк поставлен" if result["liked"] else "лайк снят"
            return f"Готово: {state}. Всего лайков: {result['likes_count']}."

        if act == "export_prompt":
            if int(skill_id or 0) <= 0:
                return "Для export_prompt нужен skill_id > 0."
            r = await agent.db.get_skill_by_id(int(skill_id))
            if not r:
                return "Скилл не найден."
            tools_line = ", ".join(r.get("tools") or []) or "не указаны"
            return (
                f"ТЕМА: {r['theme']}\n"
                f"ИНСТРУМЕНТЫ: {tools_line}\n\n"
                f"ИНСТРУКЦИЯ/ПРОМПТ:\n{r['body']}"
            )

        return "Неизвестный action. Используй: create | list | like | export_prompt."

    return manage_skills
