from langchain.tools import tool
from pathlib import Path

SKILLS_PATH = Path("/app/app/agent_pm/SKILLS")

@tool
async def connect_mcp_git():
    """
    tools/git_mcp_tool.py
    =====================
    Тул для агента: работа с Git-репозиториями.
    Поддерживает два режима: GitVerse (MCP) и GitHub (subprocess + PAT).
    
    Дерни этот инструмент чтобы получить полную инструкцию работы с удаленными репозиториями.
    """
    try:
        with open(SKILLS_PATH / "work_git.md", mode='r', encoding='utf-8') as f:
            text = f.read()
        return text
    except Exception as e:
        return "Не получилось открыть скилл, возможно он перемещен или удален"
