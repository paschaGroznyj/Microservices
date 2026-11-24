from langchain.tools import tool


def _user_tz(agent):
    """Часовой пояс пользователя текущего запроса (браузер / настройки TG и MAX)."""
    from agent.core.timezone_utils import user_tzinfo
    getter = getattr(agent, "_ctx_get", None)
    resolved = getter("user_tz") if callable(getter) else None
    if resolved is not None:
        return resolved
    name = getter("timezone_name") if callable(getter) else None
    offset = getter("tz_offset_minutes") if callable(getter) else None
    return user_tzinfo(name, offset)


def make_journal_tool(agent, chat_id: str, user_id: int):

    @tool
    async def work_with_agent_journal(
        action: str,
        entry_type: str = "",
        content: str = "",
        replace: str = "",
        entry_id: int = 0,
        archive_summary: str = "",
        priority: int = 0,
        expires_at: str = "",
    ) -> str:
        """
        Working with the logbook (agent_journal) IS THE MEMORY INSIDE THIS CHAT.
        Use it when the user explicitly asks for "remember this", "fix it",
        "don't forget about X" — or when you yourself consider it important to preserve the fact
        for future messages in the same dialog.

        The log is linked to a specific chat — recordings from other chats are not visible.
        and they are not affected.

        ARCHIVE: Recordings can be collapsed into an archive. The archive record does NOT extend to
        the main context (saves tokens), but remains visible in a compact
        in the registry "── IN THE ARCHIVE ──" as "ID: N — short description". By this id
        it can be returned back (unarchive).

        PRIORITY: every entry has a priority from 1 (highest) to 5. YOU choose it on insert:
            1 — critical rules/facts that must never be lost (access, hard requirements);
            2 — important long-lived context (key artifacts, decisions, preferences);
            3 — regular working facts (default);
            4 — minor details;
            5 — short-lived or low-value notes.
        Auto-compression archives low-priority entries first: a priority-1 entry stays in
        the context the longest even if it is old. identity entries are never compressed,
        but still get a priority. Change it later with action="update", entry_id, priority.

        ONE-TIME ENTRIES: if an entry concerns a one-time action or event with a date/time
        ("remind me tomorrow at 10:00", "the deploy is on Friday"), set expires_at — the moment
        after which the entry is useless. Use the USER'S LOCAL time from «ТЕКУЩИЙ КОНТЕКСТ»:
        "YYYY-MM-DD HH:MM", or "YYYY-MM-DD" for the end of that day. After expires_at the entry
        is marked expired and disappears from your context (it is NOT deleted from the DB).
        If the action is already done or the time has passed, call action="expire".

        Arguments:
            action: "insert" is a new entry.
                    "update" — change an existing entry: by entry_id or by the replace substring;
                               replace part of the text and/or set new priority / expires_at
                    "archive"   — move an entry to the archive by its id
                                  (entry_id + archive_summary required)
                    "unarchive" — return an archived entry to the active context
                                  (entry_id required)
                    "expire"    — the one-time entry is no longer relevant: hide it from
                                  the context by its id (entry_id required)
            entry_id: the id of the journal entry ("ID: N" in the journal / archive registry).
                      Required for archive/unarchive/expire, optional for update.
            archive_summary: One short sentence about the contents of the record is
                      MANDATORY for archive. It is shown in the registry.
                      archive instead of the full text.
            entry_type: The record type (for insert/update) is one of:
                identity        — how to behave in this chat, style, role
                user_preference — the other person's preferences in this chat
                anomaly         — a noticed oddity/recurring problem
                evolution       — what you've learned in the course of this dialogue
                summary         — a concise summary (usually not created by you directly;
                                  at most 2 active summaries are kept, older ones go to the archive)
            content: the text of the entry (for insert/update — what to write),
                     MANDATORY for insert and for update with replace
            replace: update ONLY — the substring we are looking for in an existing record
                     (something that needs to be found and replaced). Not used for insert.
            priority: 1–5, 1 is the highest. For insert, default is 3. 0 — leave unchanged.
            expires_at: user's local time "YYYY-MM-DD HH:MM" / "YYYY-MM-DD" for one-time entries;
                     "none" in update removes the expiry. Empty — no expiry / unchanged.

        Examples:
            Memorize a new fact:
                action="insert", entry_type="user_preference", priority=2,
                content="Prefers short answers without emojis"

            Memorize a one-time task:
                action="insert", entry_type="evolution", priority=3,
                content="Remind Pavel about the release v1.0.9 review", expires_at="2026-11-25 10:00"

            Refine an existing record:
                action="update", entry_type="user_preference",
                replace="short answers", content="detailed answers with examples"

            Raise the priority:
                action="update", entry_id=157, priority=1

            The one-time action is done:
                action="expire", entry_id=158

            Archive the entry (using the id from the registry):
                action="archive", entry_id=157,
                archive_summary="Walkthrough of the PoC graph-world: slicing the cat hero and walking animation."

            Return the entry from the archive to the context:
                action="unarchive", entry_id=157

        DON'T write SQL — just these parameters. All operations are performed
        strictly within the scope of the current chat, no other entries are affected.
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
            priority=(priority or None),
            expires_at=(expires_at or None),
            tz=_user_tz(agent),
        )
        return str(result)

    return work_with_agent_journal
