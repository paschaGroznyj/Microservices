"""
gitverse_mcp_client.py — переиспользуемый клиент GitVerse MCP

Проверено: 06.08.2026
Endpoint  : https://mcp.gitverse.ru
Протокол  : JSON-RPC 2.0 / Streamable HTTP (ответ всегда JSON, не SSE)
Auth      : Bearer-токен в заголовке Authorization
Сессия    : initialize → получаем mcp-session-id (lowercase!) → передаём во всех запросах

Критические нюансы (выяснены через отладку):
  1. mcp-session-id — строго lowercase, иначе "Invalid session ID"
  2. notifications/initialized — не вызывать (404)
  3. Content-Type ответа — application/json, SSE-парсинг не нужен
  4. Ошибки инструмента — HTTP 200 + поле "error" в теле, не HTTP 4xx
  5. httpx недоступен в sandbox — только urllib.request
  6. get_repository_content — параметр "filepath", не "path"
  7. Корень репо — filepath="/"
  8. Файл возвращается как plain utf-8 строка, не base64
  9. entries директории лежат в result["Result"]["entries"]

Использование:
    import sys; sys.path.insert(0, "/sandbox/reusable")
    from gitverse_mcp_client import GitVerseMCPClient

    client = GitVerseMCPClient()          # токен из env GITVERSE_TOKEN
    client.initialize()                   # открыть сессию
    info   = client.call("get_my_user_info")
    files  = client.call("get_repository_content", {
        "owner": "pascha_groznyj", "repo": "Seminar_app", "filepath": "/"
    })
    client.close()                        # явный сброс session_id
"""

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

MCP_ENDPOINT = "https://mcp.gitverse.ru"


class GitVerseMCPClient:
    """
    Минимальный клиент GitVerse MCP без внешних зависимостей.

    Жизненный цикл
    --------------
    client = GitVerseMCPClient()
    client.initialize()
    result = client.call("get_my_user_info")
    tools  = client.list_tools()
    client.close()

    Или через контекстный менеджер:
    with GitVerseMCPClient() as client:
        result = client.call("get_repository_content", {...})
    """

    def __init__(self, token: str | None = None, timeout: float = 30.0):
        self.token      = token or os.environ.get("GITVERSE_TOKEN", "")
        self.timeout    = timeout
        self.session_id: str | None = None
        self._rpc_id    = 0

        if not self.token:
            raise ValueError(
                "GITVERSE_TOKEN не задан. "
                "Передайте токен явно: GitVerseMCPClient(token='...') "
                "или через переменную окружения GITVERSE_TOKEN."
            )

    # ------------------------------------------------------------------
    # Контекстный менеджер
    # ------------------------------------------------------------------

    def __enter__(self) -> "GitVerseMCPClient":
        self.initialize()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Внутренние хелперы
    # ------------------------------------------------------------------

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _build_headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json, text/event-stream",
        }
        if self.session_id:
            h["mcp-session-id"] = self.session_id   # СТРОГО lowercase
        return h

    def _post(self, payload: dict) -> dict:
        """Низкоуровневый POST. Возвращает распарсенный JSON-ответ."""
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            MCP_ENDPOINT,
            data=data,
            headers=self._build_headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                # Фиксируем session_id (lowercase заголовок)
                sid = dict(resp.headers).get("mcp-session-id")
                if sid:
                    self.session_id = sid
                    logger.debug("session_id: %s", sid)
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            logger.error("HTTP %s: %s", e.code, body[:500])
            raise RuntimeError(f"HTTP {e.code}: {body[:300]}") from e

        result = json.loads(body)

        # Ошибка инструмента — HTTP 200, но поле "error" в теле
        if "error" in result:
            raise RuntimeError(f"MCP error: {result['error']}")

        return result

    @staticmethod
    def _unpack(raw: dict) -> Any:
        """
        Распаковать result.content[0].text → Python-объект.
        Если там JSON-строка — парсим. Если plain text — возвращаем как есть.
        """
        content = raw.get("result", {}).get("content", [])
        if content and content[0].get("type") == "text":
            text = content[0]["text"]
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return raw.get("result", raw)

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def initialize(self) -> "GitVerseMCPClient":
        """
        Шаг 1 — открыть MCP-сессию.
        После вызова self.session_id будет заполнен.
        notifications/initialized — НЕ вызывается (GitVerse MCP возвращает 404).
        """
        self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities":    {},
                "clientInfo":      {"name": "pm-knowledge-agent", "version": "1.0"},
            },
        })
        logger.info("MCP session opened: %s", self.session_id)
        return self

    def call(self, tool_name: str, arguments: dict | None = None) -> Any:
        """
        Вызвать инструмент GitVerse MCP.

        Parameters
        ----------
        tool_name  : str  — имя инструмента, например "get_my_user_info"
        arguments  : dict — аргументы инструмента (None для безаргументных)

        Returns
        -------
        Распарсенный Python-объект из result.content[0].text
        """
        if not self.session_id:
            raise RuntimeError("Сессия не открыта. Вызовите initialize() сначала.")
        raw = self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/call",
            "params":  {"name": tool_name, "arguments": arguments or {}},
        })
        return self._unpack(raw)

    def list_tools(self) -> list[dict]:
        """Получить список всех 41 инструмента MCP-сервера."""
        if not self.session_id:
            raise RuntimeError("Сессия не открыта. Вызовите initialize() сначала.")
        raw = self._post({
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  "tools/list",
            "params":  {},
        })
        return raw.get("result", {}).get("tools", [])

    def close(self) -> None:
        """Сбросить session_id (сессия stateless на сервере, просто чистим локально)."""
        self.session_id = None
        logger.info("MCP session cleared.")
