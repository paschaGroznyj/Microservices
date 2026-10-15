"""
MAX Bot API v2 — тонкий HTTP-клиент поверх httpx.

Base URL: https://platform-api2.max.ru (botapi v2; platform-api.max.ru и
botapi.max.ru помечены @deprecated в официальном TS-клиенте @maxhub/max-bot-api).

Auth: заголовок Authorization: <token> — сырой токен бота (не Bearer, не в URL).
Токен из business.max.ru через @MasterBot, берётся из env MAX_TOKEN_API.

Контракт сверен с официальным npm-пакетом @maxhub/max-bot-api@1.0.2:
  GET  /me                            — инфо о боте
  PATCH/me/commands                   — команды бота
  GET  /updates?marker&timeout&types  — long-polling (marker-based)
  POST /messages?chat_id|user_id      — отправка (NewMessageBody в теле)
  PUT  /messages?message_id           — редактирование
  DELETE /messages?message_id         — удаление
  POST /answers?callback_id           — ответ на callback кнопки
  POST /uploads?type=image|video|audio|file — URL для 2-шаговой загрузки
"""
from __future__ import annotations

import logging
import os
import ssl
from typing import Any

import httpx


def _build_ssl_context() -> ssl.SSLContext | str | bool:
    """
    В корпоративной сети TLS к platform-api2.max.ru перехватывается MITM-прокси,
    который подписывает свой внутренний Root CA. Он отсутствует в certifi/системе,
    отсюда CERTIFICATE_VERIFY_FAILED. Разрешаем подсунуть корпоративный CA через env:
      MAX_CA_BUNDLE   — путь к .pem/.crt (один файл или бандл)
      MAX_TLS_INSECURE=1 — крайний случай: отключить верификацию (НЕ для прода)
    По умолчанию — обычная верификация (certifi).
    """
    if os.getenv("MAX_TLS_INSECURE", "").lower() in ("1", "true", "yes"):
        log_init = logging.getLogger("max_client")
        log_init.warning("MAX_TLS_INSECURE=1 — верификация TLS ОТКЛЮЧЕНА (небезопасно)")
        return False
    ca = os.getenv("MAX_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")
    if ca and os.path.exists(ca):
        return ca
    return True

log = logging.getLogger("max_client")

MAX_API_BASE = "https://platform-api2.max.ru"


class MaxApiError(Exception):
    def __init__(self, status: int, data: Any):
        self.status = status
        self.data = data
        super().__init__(f"MAX API {status}: {data}")


class MaxClient:
    def __init__(self, token: str, base_url: str = MAX_API_BASE, timeout: float = 35.0):
        if not token:
            raise ValueError("MAX token is empty (env MAX_TOKEN_API)")
        self._token = token
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._verify = _build_ssl_context()
        self._client: httpx.AsyncClient | None = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                headers={"Authorization": self._token},
                timeout=self._timeout,
                verify=self._verify,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self, method: str, path: str,
                       params: dict | None = None,
                       json: dict | None = None) -> Any:
        client = await self._http()
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = await client.request(method, "/" + path.lstrip("/"),
                                    params=clean or None, json=json)
        if resp.status_code != 200:
            try:
                data = resp.json()
            except Exception:
                data = resp.text
            raise MaxApiError(resp.status_code, data)
        if not resp.content:
            return {}
        return resp.json()

    # ── Bots ──────────────────────────────────────────────────────────────
    async def get_me(self) -> dict:
        return await self._request("GET", "me")

    async def set_commands(self, commands: list[dict]) -> dict:
        return await self._request("PATCH", "me/commands", json={"commands": commands})

    # ── Updates (long-polling, marker-based) ──────────────────────────────
    async def get_updates(self, marker: int | None = None,
                          timeout: int = 30,
                          limit: int = 100,
                          types: list[str] | None = None) -> dict:
        params: dict[str, Any] = {"marker": marker, "timeout": timeout, "limit": limit}
        if types:
            params["types"] = ",".join(types)
        return await self._request("GET", "updates", params=params)

    # ── Messages ──────────────────────────────────────────────────────────
    async def send_message(self, text: str,
                           chat_id: int | None = None,
                           user_id: int | None = None,
                           attachments: list[dict] | None = None,
                           fmt: str | None = "markdown",
                           notify: bool = True,
                           disable_link_preview: bool | None = None) -> dict:
        body: dict[str, Any] = {"text": text, "notify": notify}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request(
            "POST", "messages",
            params={"chat_id": chat_id, "user_id": user_id,
                    "disable_link_preview": disable_link_preview},
            json=body,
        )

    async def edit_message(self, message_id: str, text: str,
                          attachments: list[dict] | None = None,
                          fmt: str | None = "markdown") -> dict:
        body: dict[str, Any] = {"text": text}
        if attachments is not None:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("PUT", "messages",
                                   params={"message_id": message_id}, json=body)

    async def delete_message(self, message_id: str) -> dict:
        return await self._request("DELETE", "messages",
                                   params={"message_id": message_id})

    async def answer_callback(self, callback_id: str,
                             text: str | None = None,
                             notification: str | None = None) -> dict:
        body: dict[str, Any] = {}
        if text is not None:
            body["message"] = {"text": text}
        if notification is not None:
            body["notification"] = notification
        return await self._request("POST", "answers",
                                   params={"callback_id": callback_id}, json=body)

    # ── Uploads (2-шаговая загрузка) ──────────────────────────────────────
    async def get_upload_url(self, upload_type: str = "file") -> dict:
        return await self._request("POST", "uploads", params={"type": upload_type})

    async def upload_file(self, upload_type: str, filename: str,
                         content: bytes, content_type: str | None = None) -> dict:
        info = await self.get_upload_url(upload_type)
        url = info.get("url")
        if not url:
            raise MaxApiError(500, {"message": "no upload url", "data": info})
        async with httpx.AsyncClient(timeout=120.0, verify=self._verify) as up:
            files = {"data": (filename, content, content_type or "application/octet-stream")}
            r = await up.post(url, files=files)
            if r.status_code not in (200, 201):
                raise MaxApiError(r.status_code, r.text)
            try:
                return r.json()
            except Exception:
                return {"token": info.get("token")}
