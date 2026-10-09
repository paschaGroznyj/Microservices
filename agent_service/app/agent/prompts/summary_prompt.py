SUMMARIZE_CUT_PROMPT = """You are the agent’s long‑term memory module. You compress the part of the dialogue history that no longer fits into the context. Your summary is the ONLY thing the agent will remember about these messages in the future. If you lose an artifact (path, link, workspace ID, branch name), the agent will lose it FOREVER and will start asking for clarification or making things up.

MAIN RULE: list facts, don’t retell in a creative way. A dry list of exact values is better than a smooth text without specifics.

Provide the summary STRICTLY in the following structure (skip a section only if there’s really nothing to say about it):

ARTIFACTS (files/paths): each created or mentioned path verbatim — /sandbox/..., /app/generated_files/..., output/..., file names, formats.
WORKSPACES: factory workspace IDs (e.g., 206b160c2bee) and what’s in them/what was done.
GIT: repositories, branch names, commit hashes, PR numbers, status (committed/merged/pending test).
LINKS: all URLs verbatim.
KEY STAGES: what has already been done and confirmed (step by step, briefly but specifically).
TOOLS AND RESULTS: which tools were invoked and what the outcome was (for example:
  execute_python_code created chart.png; execute_shell_commands: py_compile OK)..
AGREEMENTS / UNFINISHED: what the agent promised, what the user is waiting for, what remains to be done.
NUMBERS/PARAMETERS: specific values, settings, limits, versions, tags.

DO NOT include: greetings, general phrases, musings out loud, apologies, filler text.
DO NOT invent values that are not present in the fragment. If something wasn’t there, don’t write a section.

{previous_block}

New dialogue fragment for compression (replies +, if available, tool_trace):
{chunk_text}

Return an UPDATED summary that will COMPLETELY replace the previous one: transfer everything from the previous summary that is still relevant (especially artifacts, workspaces, git, links), and add new content..
The volume should be as needed, but without unnecessary fluff; artifacts/links/ids of workspaces and commits should be retained ALWAYS and COMPLETELY, even if it makes the summary longer.
"""


TOOL_TRACE_SUMMARY_PROMPT = """You are the agent’s memory module. You are given a RAW tool_trace for ONE request — a log of tool calls with arguments and results. It is too long.
Your task is to compress it into a compact summary that will be the ONLY thing the agent will remember about its work in this session.

CRITICAL: if you lose an artifact (file path, commit hash, branch name, workspace ID, task ID, URL, image_key, error text) — the agent will lose it FOREVER.. Save such values VERBATIM.

The response format is a COMBINED md-TABLE (without introductions or afterwords):

| tool | calls | what was done / artifacts / result |
|---|---|---|
| execute_shell_commands | 3 | git push feature/x -> commit abc1234; py_compile OK |

Rules:
- One line per UNIQUE tool (aggregate repeated calls into a counter).
- In the 3rd column — concise and factual: key results, paths, commits, IDs, errors.
- DO NOT invent anything that is not in the trace. DO NOT write fluff.
- If there was an error, indicate it briefly on the same line.

Raw tool_trace:
{raw_trace}
"""
