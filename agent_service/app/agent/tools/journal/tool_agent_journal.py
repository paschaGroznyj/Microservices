from langchain.tools import tool

def make_journal_tool(agent, chat_id: str, user_id: int):

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

        Arguments:
            action: "insert" is a new entry.
                    "update" — replace part of the text in an existing entry
                    "archive"   — move an entry to the archive by its id
                                  (entry_id + archive_summary required)
                    "unarchive" — return an archived entry to the active context
                                  (entry_id required)
            entry_id: the id of the journal entry — ONLY for archive/unarchive.
                      It is taken from the ARCHIVE section (unarchiving) or from
                      "ID: N" of the active record (archiving).
            archive_summary: One short sentence about the contents of the record is
                      MANDATORY for archive. It is shown in the registry.
                      archive instead of the full text.
            entry_type: The record type (for insert/update) is one of:
                identity        — how to behave in this chat, style, role
                user_preference — the other person’s preferences in this chat
                anomaly         — a noticed oddity/recurring problem
                evolution       — what you’ve learned in the course of this dialogue
                summary         — a concise summary (usually not created by you directly)
            content: the text of the entry (for insert/update — what to write),
                     MANDATORY, cannot be empty
            replace: update/delete ONLY — the substring we are looking for
                     an existing record (something that needs to be found and replaced/deleted).
                     It is not used for insert.

        Examples:
            Memorize a new fact:
                action="insert", entry_type="user_preference", content="Prefers short answers without emojis"

            Refine an existing record:
                action="update", entry_type="user_preference",
                replace="short answers", content="detailed answers with examples"

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
        )
        return str(result)

    return work_with_agent_journal