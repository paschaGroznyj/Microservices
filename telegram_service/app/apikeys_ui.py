"""Раздел «Ключи API» в /start Telegram-бота.

Маршрут сохранения тот же, что у профиля CatchPM: ключ пишет auth_service в
vault.user_secrets под web user_id. Бот обращается к chats_service
(/tg/secrets*), тот берёт владельца личного чата (telegram_chats.added_by_user_id)
и вызывает auth_service /profile/secrets/*. Значение ключа назад не приходит.
"""
from __future__ import annotations

import html
import logging

import httpx

log = logging.getLogger(__name__)

PROVIDER = "routerai"
PROVIDER_LABEL = "RouterAPI"
AWAIT_TTL = 300  # сколько ждём сообщение с ключом после «Добавить/Заменить»


def fmt_date(iso: str | None) -> str:
    """'2026-11-25T10:00:00+00:00' -> '25.11.2026'."""
    if not iso or len(iso) < 10:
        return ""
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def keys_text(item: dict | None, error: str | None = None) -> str:
    lines = ["<b>🔑 Ключи API</b>", "",
             f"<b>{PROVIDER_LABEL}</b> нужен для категории «Видео и аудио»: генерация видео, "
             "синтез речи и музыки. Ключ общий с профилем CatchPM.", ""]
    if error:
        lines.append(f"⚠️ {html.escape(error)}")
    elif item:
        meta = [f"••••{html.escape(item.get('last4') or '')}"]
        if fmt_date(item.get("updated_at")):
            meta.append(f"обновлён {fmt_date(item.get('updated_at'))}")
        meta.append(f"использован {fmt_date(item.get('last_used_at'))}" if item.get("last_used_at")
                    else "ещё не использовался")
        lines.append("Ключ задан: " + " · ".join(meta))
    else:
        lines.append("Ключ не задан. Без него инструменты «Видео и аудио» не работают.")
    return "\n".join(lines)


def await_text() -> str:
    return (f"Отправь ключ {PROVIDER_LABEL} одним сообщением.\n\n"
            "Сообщение с ключом я сразу удалю из чата. Ключ хранится зашифрованным, "
            "назад он не показывается.")


async def fetch_status(chats_url: str, chat_id) -> tuple[dict | None, str | None]:
    """(item | None, error | None) для личного чата."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{chats_url}/tg/secrets", params={"chat_id": str(chat_id)})
        data = r.json()
    except Exception as e:
        log.warning(f"[apikeys] status fail chat={chat_id}: {type(e).__name__}")
        return None, "Сервис ключей недоступен, попробуй позже."
    if r.status_code != 200:
        return None, str(data.get("detail") or f"Ошибка {r.status_code}")
    item = next((x for x in (data.get("items") or []) if x.get("provider") == PROVIDER), None)
    return item, None


async def save(chats_url: str, chat_id, secret: str) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{chats_url}/tg/secrets",
                                  json={"chat_id": str(chat_id), "provider": PROVIDER, "secret": secret})
        data = r.json()
    except Exception as e:
        # без текста исключения: в нём может оказаться тело запроса
        log.warning(f"[apikeys] save fail chat={chat_id}: {type(e).__name__}")
        return False, "Сервис ключей недоступен, попробуй позже."
    if r.status_code != 200:
        return False, str(data.get("detail") or f"Ошибка {r.status_code}")
    return True, None


async def delete(chats_url: str, chat_id) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{chats_url}/tg/secrets/delete",
                                  json={"chat_id": str(chat_id), "provider": PROVIDER})
        return r.status_code == 200
    except Exception as e:
        log.warning(f"[apikeys] delete fail chat={chat_id}: {type(e).__name__}")
        return False
