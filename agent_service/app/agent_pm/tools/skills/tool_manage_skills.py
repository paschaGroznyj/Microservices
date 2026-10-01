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
        old_str: str = "",
        new_str: str = "",
    ) -> str:
        """
        Skill library management.
        action:
        - create: create a skill (requires theme, body, and optionally tools[])
        - list: show a list of skills (sort_by=new|popular, query, tool_filter)
        - like: like/unlike a skill (requires skill_id)
        - export_prompt: return the finished skill prompt for copying to a chat
        - edit: точечная правка тела скилла через find & replace
          (skill_id, old_str, new_str are required). old_str must appear
          in body EXACTLY once and must match exactly in terms of characters
          (including spaces/newlines). Only the skill author can make changes.
          An empty new_str deletes the found fragment.

        SKILL BODY FORMAT (body) WHEN CREATING:
        body MUST be written in Markdown. The frontend renders it as md when the card is expanded.
        Recommended structure:
          ## Goal — 1-2 sentences explaining why the skill is needed.
          ## Steps — a numbered list of actions.
          ## Tools — which tools are used and why.
          ## Example — a code/query block in ``` if necessary.
        Use headings (##/###), lists (- / 1.), inline code (`code`), and code blocks (```lang ... ```). Do not write HTML in the body.


        If the request body already contains:             Skill topic: ...            Recommended tools: ...

            Skill body: ...
        This means you’ve already been given a ready‑made skill; you don’t need to add this skill. You should ask the user for additional information on what to use this skill for if nothing has been provided.
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

        if act == "edit":
            if int(skill_id or 0) <= 0:
                return "Для edit нужен skill_id > 0."
            if not old_str:
                return "Для edit нужен непустой old_str (точный фрагмент body для замены)."
            res = await agent.db.replace_skill_body(
                skill_id=int(skill_id),
                user_id=int(user_id),
                old_str=old_str,
                new_str=new_str,
            )
            status = res.get("status")
            if status == "not_found":
                return (
                    "Скилл не найден или вы не являетесь его автором — редактировать нельзя."
                )
            if status == "no_match":
                return (
                    "old_str не найден в теле скилла. Скопируй точный фрагмент "
                    "(с теми же пробелами/переносами), например через export_prompt."
                )
            if status == "ambiguous":
                return (
                    f"old_str встречается {res.get('count')} раз(а) — неоднозначно. "
                    "Добавь больше окружающего контекста, чтобы фрагмент стал уникальным."
                )
            sk = res.get("skill") or {}
            return (
                f"Скилл обновлён: id={sk.get('id')}, тема={sk.get('theme')}. "
                "Заменено 1 вхождение в теле скилла."
            )

        return "Неизвестный action. Используй: create | list | like | export_prompt | edit."

    return manage_skills
