TOOLS_SETTINGS: dict[str, list[str]] = {
    "pm_research": [
        "research_prompt",
        "download_text_articles",
        "download_text_pdf",
        "upload_articles",
        "digest_basket",
        "make_basket_reviews",
        "rebuild_categories",
        "summarize_by_filter",
        "summarize_new_articles",
        "summarize_all_articles",
        "search_db",
        "make_generate_reviews_tool",
        "send_digest_email",
    ],
    "internet": [
        "parse_web_page",
        "search_internet",
    ],
    "content": [
        "execute_python_code",
        "edit_and_run",
        "search_code_library",
        "save_to_code_library",
        "send_file_to_user",
        "presentation_design_skill",
        "html_design_skill",
        "generate_image",
    ],
    "parallelism": [
        "create_workspace",
        "list_workspace_files",
        "download_workspace_file",
        "delete_workspace",
        "cleanup_workspaces",
        "submit_task",
        "check_task_status",
    ],
    "sessions": [
        "session_point",
        "work_with_agent_journal",
    ],
    "skills": [
        "manage_skills",
    ],
    "admin": [
        "get_db_schema",
        "execute_sql_query",
        "edit_sandbox_file", 
        "list_tree",
        "read_file",
        "write_sandbox_file",
        "grep_search",
        "find_files",
        # "push_message_to_telegram",
        "work_with_email",
        "connect_mcp_git",
        "execute_shell_commands",
        "upload_to_workspace",
        "read_workspace_file",
        "edit_workspace_file",
    ],
}

ADMIN_ONLY_CATEGORIES = {"admin"}

# Универсальные тулы — доступны всегда, независимо от выбранных категорий
# (не фильтруются по selected_tools). Пополняем сюда тулы, которые должны
# быть в наборе агента по умолчанию: базовые способности вроде анализа
# изображений, которые не привязаны к конкретному режиму работы.
UNIVERSAL_TOOLS: set[str] = {
    "analyze_image",
}