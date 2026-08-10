"""
agent_service/tools/parsers/tool_mcp_browser.py
────────────────────────────────────────────────
Tool для агента: открывает страницу через Playwright MCP с полным JS-рендерингом.
Используется когда parse_web_page не справляется:
  - SPA / React / Vue / Angular приложения
  - Контент, подгружаемый через JS после DOMContentLoaded
  - Нужен accessibility tree (snapshot) для навигации по странице
  - WAF пропускает headless Chromium, но режет простые HTTP-запросы

Подключение в langchain_agent.py:
    from tools.parsers.tool_mcp_browser import make_mcp_browser_tool
    mcp_tool = make_mcp_browser_tool(agent)
    tools += [mcp_tool]
"""

import glob
import http.client
import json
import logging
import os
import random
import re
import subprocess
import threading
import time
from typing import Optional

from langchain.tools import tool

log = logging.getLogger(__name__)

# ── Пути к MCP ───────────────────────────────────────────────────────────────
NODE_PATH = "/usr/local/lib/python3.10/site-packages/playwright/driver/node"
MCP_BIN   = "/tmp/mcp_install/node_modules/@playwright/mcp/cli.js"

# ── Внутренние хелперы ───────────────────────────────────────────────────────

def _mcp_call(
    port: int,
    method: str,
    params: dict = None,
    req_id: int = 1,
    session_id: str = None,
    timeout: int = 25,
) -> tuple[dict, str]:
    """Один JSON-RPC 2.0 запрос к MCP-серверу через HTTP SSE."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    })
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    hdrs = {
        "Content-Type": "application/json",
        "Accept":       "application/json, text/event-stream",
        "Host":         f"127.0.0.1:{port}",
        "Origin":       f"http://127.0.0.1:{port}",
    }
    if session_id:
        hdrs["mcp-session-id"] = session_id

    conn.request("POST", "/mcp", body=payload, headers=hdrs)
    resp = conn.getresponse()
    sid  = resp.getheader("mcp-session-id") or session_id
    raw  = resp.read().decode("utf-8", errors="replace")
    conn.close()

    result = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                result = json.loads(line[5:].strip())
            except Exception:
                pass
    return result or {}, sid


def _get_text(r: dict) -> str:
    """Извлекает текст из MCP result.content[]."""
    return " ".join(
        c.get("text", "")
        for c in r.get("result", {}).get("content", [])
        if c.get("type") == "text"
    )


def _read_latest_snapshot(cwd: str, limit: int = 8000) -> str:
    """Читает последний .yml snapshot из .playwright-mcp/"""
    pattern = os.path.join(cwd, ".playwright-mcp", "*.yml")
    files = sorted(glob.glob(pattern))
    if not files:
        return ""
    with open(files[-1], "r", errors="replace") as f:
        return f.read(limit)


def _extract_nav_info(nav_text: str) -> tuple[Optional[int], Optional[str], Optional[str]]:
    """Парсит HTTP-статус, title и финальный URL из текста ответа browser_navigate."""
    status = None
    title  = None
    final_url = None

    m = re.search(r"HTTP status:\s*(\d+)", nav_text)
    if m:
        status = int(m.group(1))

    m = re.search(r"Page Title:\s*(.+)", nav_text)
    if m:
        title = m.group(1).strip()

    m = re.search(r"Page URL:\s*(https?://\S+)", nav_text)
    if m:
        final_url = m.group(1)

    return status, title, final_url


def _parse_via_mcp(url: str, wait_ms: int, port: int, snap_limit: int) -> dict:
    """
    Синхронное ядро парсера. Запускает MCP-сервер, делает navigate,
    читает snapshot + innerText, завершает процесс.
    """
    result = {
        "url":      url,
        "status":   None,
        "title":    None,
        "text":     "",
        "snapshot": "",
        "error":    None,
    }

    cwd = f"/tmp/mcp_cwd_{port}"
    os.makedirs(cwd, exist_ok=True)
    env = {**os.environ, "DISPLAY": ":99"}

    # ── 1. Запуск MCP-сервера ────────────────────────────────────────────────
    proc = subprocess.Popen(
        [
            NODE_PATH, MCP_BIN,
            "--headless", "--no-sandbox",
            "--port",    str(port),
            "--host",    "0.0.0.0",
            "--allowed-origins", "*",
            "--allowed-hosts",   "*",
            "--browser", "chromium",
            "--executable-path", "/usr/bin/chromium",
            "--isolated",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1,
        cwd=cwd,
    )
    # Дренируем stdout чтобы не блокировать процесс
    threading.Thread(
        target=lambda: [proc.stdout.readline() for _ in range(300)],
        daemon=True,
    ).start()
    time.sleep(3)   # даём серверу подняться

    if proc.poll() is not None:
        result["error"] = "MCP-сервер не поднялся (процесс завершился досрочно)"
        return result

    try:
        # ── 2. Initialize ────────────────────────────────────────────────────
        _, sid = _mcp_call(port, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities":    {},
            "clientInfo":      {"name": "PMych-MCP", "version": "1.0"},
        }, req_id=1)

        if not sid:
            result["error"] = "Не получен mcp-session-id при initialize"
            return result

        # ── 3. Navigate ──────────────────────────────────────────────────────
        nav_r, sid = _mcp_call(port, "tools/call", {
            "name":      "browser_navigate",
            "arguments": {"url": url},
        }, req_id=2, session_id=sid, timeout=30)

        nav_text = _get_text(nav_r)
        status, title, final_url = _extract_nav_info(nav_text)

        result["status"] = status
        result["title"]  = title
        if final_url:
            result["url"] = final_url

        # ── 4. Ждём JS-рендер ────────────────────────────────────────────────
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)

        # ── 5. Accessibility tree snapshot ───────────────────────────────────
        result["snapshot"] = _read_latest_snapshot(cwd, snap_limit)

        # ── 6. Текст через browser_evaluate ─────────────────────────────────
        eval_r, sid = _mcp_call(port, "tools/call", {
            "name": "browser_evaluate",
            "arguments": {
                "function": "() => document.body ? document.body.innerText : ''",
                "element":  "document body",
                "target":   "body",
            },
        }, req_id=3, session_id=sid, timeout=20)

        eval_text = _get_text(eval_r)
        # MCP оборачивает строку в ### Result "..." — убираем обёртку
        m = re.search(r'"([\s\S]+)"', eval_text)
        if m:
            raw_text = m.group(1).replace("\\n", "\n").replace("\\t", "\t")
        else:
            raw_text = eval_text
        result["text"] = raw_text.strip()

        # ── 7. Fallback: title через evaluate ────────────────────────────────
        if not result["title"]:
            title_r, _ = _mcp_call(port, "tools/call", {
                "name": "browser_evaluate",
                "arguments": {
                    "function": "() => document.title",
                    "element":  "document",
                    "target":   "html",
                },
            }, req_id=4, session_id=sid, timeout=10)
            t = _get_text(title_r)
            m = re.search(r'"(.+)"', t)
            if m:
                result["title"] = m.group(1)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        log.error("mcp_browser parse_page error url=%s: %s", url, e)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()

    return result


# ── Фабрика тула ─────────────────────────────────────────────────────────────

def make_mcp_browser_tool(agent):
    """
    Фабрика. agent передаётся для совместимости с паттерном проекта,
    но внутри не используется (MCP не требует доступа к сервисам агента).
    """

    @tool
    async def browse_page_mcp(
        url: str,
        wait_ms: int = 1500,
        snap_limit: int = 8000,
    ) -> dict:
        """
        Открывает страницу через Playwright MCP с полным JS-рендерингом.
        Используй ВМЕСТО parse_web_page когда:
          - Сайт — SPA (React, Vue, Angular, Next.js и т.п.)
          - Контент подгружается асинхронно через JS
          - Нужен accessibility tree (snapshot) для навигации/кликов
          - parse_web_page вернул пустой текст или заглушку

        НЕ используй для:
          - Массового параллельного парсинга (slow, однопоточный)
          - Сайтов с WAF по IP датацентра (dns-shop.ru, tass.ru — нужны proxy)

        Параметры:
          url        — полный URL страницы (https://...)
          wait_ms    — пауза после navigate для JS-рендера (мс, по умолчанию 1500)
          snap_limit — макс. символов accessibility tree (по умолчанию 8000)

        Возвращает dict:
          {
            "url":      str,   # финальный URL (после редиректов)
            "status":   int,   # HTTP-статус (или null)
            "title":    str,   # заголовок страницы
            "text":     str,   # innerText всего body
            "snapshot": str,   # YAML accessibility tree
            "error":    str,   # null если всё ок
          }

        Поисковые запросы для code_library:
          playwright mcp | mcp парсер | js spa парсер | headless chromium
          playwright javascript | mcp browser navigate | js рендеринг парсер

        Инфраструктура:
          NODE  = /usr/local/lib/python3.10/site-packages/playwright/driver/node
          MCP   = /tmp/mcp_install/node_modules/@playwright/mcp/cli.js
          Chromium = /usr/bin/chromium
          Протокол = JSON-RPC 2.0 / HTTP SSE
          Порт  = случайный 9400–9500 (авто)
        """
        port = random.randint(9400, 9500)
        log.info("browse_page_mcp: url=%s port=%s wait_ms=%s", url, port, wait_ms)

        # MCP-парсер синхронный — запускаем в executor чтобы не блокировать event loop
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _parse_via_mcp(url, wait_ms, port, snap_limit),
        )

        log.info(
            "browse_page_mcp: done url=%s status=%s text_len=%s error=%s",
            result["url"], result["status"], len(result["text"]), result["error"],
        )
        return result

    return browse_page_mcp
