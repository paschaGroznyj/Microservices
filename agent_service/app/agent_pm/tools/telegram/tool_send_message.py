# tools/telegram/tool_push_telegram.py
# Тула для явной отправки сообщения в Telegram по chat_id
# Используется для "догоняющих" сообщений и ручного восстановления диалога

import logging
import os
import base64
import httpx
from agent_pm.core.config import TG_BOT_PUSH_URL

from langchain.tools import tool

log = logging.getLogger(__name__)


def make_push_telegram_tool(agent):

    @tool
    async def push_message_to_telegram(chat_id: int, text: str) -> dict:
        """
        Отправляет текстовое сообщение в Telegram напрямую по chat_id.
        Используй когда нужно ответить пользователю вне текущей сессии,
        например восстановить пропущенный ответ или отправить уведомление.

        Аргументы:
            chat_id: числовой ID чата в Telegram
            text: текст сообщения (HTML-разметка поддерживается)
        """
        if not TG_BOT_PUSH_URL:
            return {"ok": False, "error": "TG_BOT_PUSH_URL не задан"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    TG_BOT_PUSH_URL,
                    json={"chat_id": chat_id, "text": text, "edit_thinking": False},
                )
                if resp.status_code == 200:
                    return {"ok": True, "chat_id": chat_id}
                else:
                    return {"ok": False, "error": f"TG вернул {resp.status_code}: {resp.text[:300]}"}
        except Exception as e:
            log.error(f"push_message_to_telegram failed: {e}")
            return {"ok": False, "error": str(e)}

    return push_message_to_telegram
