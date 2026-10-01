from langchain.tools import tool
from pathlib import Path

SKILLS_PATH = Path("/app/app/agent_pm/SKILLS")

@tool
async def connect_mcp_git():
    """
    tools/git_mcp_tool.py
    =====================
    Tool for the agent: working with Git repositories..
    Supports two modes: GitVerse (MCP) and GitHub (subprocess + PAT)..
    
    Run this tool to get a complete guide on working with remote repositories.
    """
    try:
        with open(SKILLS_PATH / "work_git.md", mode='r', encoding='utf-8') as f:
            text = f.read()
        return text
    except Exception as e:
        return "Не получилось открыть скилл, возможно он перемещен или удален"
