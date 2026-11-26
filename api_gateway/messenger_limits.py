"""
Лимиты на встраивание Telegram- и MAX-чатов по уровню пользователя.

Уровень считается так же, как в static/js/userLevel.js: по числу запросов к агенту
(stats.agent_requests из auth_service /profile/me). Пороги держим синхронно с LEVELS там.

  L1            — 1 личный + 1 группа (на каждый мессенджер отдельно)
  L2 и выше     — 2 личных + 2 группы
  админ / привилегированный пользователь — без ограничений на чаты

Чаты, встроенные до введения лимитов, не трогаем: UI помечает лишние жёлтым «!».

Маршрут:
  GET /api/messengers/limits → {is_admin, level, limits, usage: {tg, max}}
Проверка для POST /api/tg/chats и /api/max/chats: ensure_can_add(user, kind, chat_id).
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Пороги уровней — копия LEVELS из static/js/userLevel.js
LEVEL_MINS = (0, 50, 200, 1000, 5000)
LIMITS_BY_LEVEL = {1: {"private": 1, "group": 1}}
LIMITS_DEFAULT = {"private": 2, "group": 2}
KINDS = {"tg": "/tg/chats", "max": "/max/chats"}
TYPE_LABEL = {"private": "личных", "group": "групповых"}
NAME = {"tg": "Telegram", "max": "MAX"}

_http = None
_chats_url = None
_auth_url = None
_get_current_user = None
_admin_usernames = frozenset()


def bind(http_client, chats_url, auth_url, get_current_user, admin_usernames):
    global _http, _chats_url, _auth_url, _get_current_user, _admin_usernames
    _http = http_client
    _chats_url = chats_url
    _auth_url = auth_url
    _get_current_user = get_current_user
    _admin_usernames = frozenset(admin_usernames or ())


def level_of(agent_requests) -> int:
    try:
        n = max(0, int(agent_requests or 0))
    except (TypeError, ValueError):
        n = 0
    lvl = 1
    for i, m in enumerate(LEVEL_MINS):
        if n >= m:
            lvl = i + 1
    return lvl


def limits_for(level: int) -> dict:
    return dict(LIMITS_BY_LEVEL.get(level, LIMITS_DEFAULT))


def chat_type_of(chat_id: str) -> str:
    """Как в telegram_db/max_db.upsert_chat: отрицательный id — группа."""
    try:
        return "group" if int(str(chat_id).strip()) < 0 else "private"
    except ValueError:
        return "group"


def is_admin(user) -> bool:
    return bool(user) and user.get("username") in _admin_usernames


# Категории tools-panel с adminOnly: true (TOOL_CATEGORIES в static/js/tools-panel.js)
ADMIN_ONLY_TOOLS = frozenset({"media_gen", "admin"})


def sanitize_tools(user, tools):
    """Не-админ не может включить чату админские категории инструментов."""
    if tools is None or is_admin(user):
        return tools
    return [t for t in tools if t not in ADMIN_ONLY_TOOLS]


async def _is_privileged(user) -> bool:
    """Только auth_service/БД, не поле из браузера или JWT. Без кэша: отзыв сразу.

    Любая ошибка/неожиданный ответ НЕ даёт привилегий. Остаются лимиты уровня.
    """
    try:
        r = await _http.post(f"{_auth_url}/profile/privileges/check",
                             json={"user_id": user["user_id"]}, timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            return isinstance(data, dict) and data.get("privileged") is True
    except (httpx.RequestError, ValueError, TypeError) as e:
        logger.warning("[msgr-limits] privilege lookup failed: %s", e)
    return False


async def _user_level(user) -> int:
    """Уровень по auth_service. Если сервис недоступен — L1 (строже, но не ломает добавление первого чата)."""
    try:
        r = await _http.post(f"{_auth_url}/profile/me",
                             json={"username": user["username"]}, timeout=10.0)
        if r.status_code == 200:
            return level_of((r.json() or {}).get("stats", {}).get("agent_requests"))
    except (httpx.RequestError, ValueError) as e:
        logger.warning("[msgr-limits] level lookup failed: %s", e)
    return 1


async def _user_chats(user, kind: str) -> list:
    try:
        r = await _http.get(f"{_chats_url}{KINDS[kind]}",
                            params={"added_by_user_id": user["user_id"]}, timeout=10.0)
        if r.status_code == 200:
            return (r.json() or {}).get("chats") or []
    except (httpx.RequestError, ValueError) as e:
        logger.warning("[msgr-limits] %s chats lookup failed: %s", kind, e)
        raise HTTPException(status_code=502, detail="Chats service dead")
    raise HTTPException(status_code=502, detail="Chats service error")


def _usage(chats: list) -> dict:
    u = {"private": 0, "group": 0}
    for c in chats:
        t = c.get("chat_type") or chat_type_of(c.get("chat_id", ""))
        if t in u:
            u[t] += 1
    return u


async def ensure_can_add(user, kind: str, chat_id: str) -> None:
    """403, если новый чат превысит лимит уровня. Повторное встраивание своего чата — не новый чат."""
    if is_admin(user) or await _is_privileged(user):
        return
    chats = await _user_chats(user, kind)
    cid = str(chat_id).strip()
    if any(str(c.get("chat_id")) == cid for c in chats):
        return
    t = chat_type_of(cid)
    level = await _user_level(user)
    lim = limits_for(level)[t]
    used = _usage(chats)[t]
    if used >= lim:
        raise HTTPException(
            status_code=403,
            detail=f"Лимит уровня L{level}: {lim} {TYPE_LABEL[t]} {NAME[kind]}-чат(а). "
                   f"Удалите один из чатов или повысьте уровень.",
        )


@router.get("/api/messengers/limits")
async def api_messenger_limits(request: Request):
    user = await _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    admin = is_admin(user)
    privileged = False if admin else await _is_privileged(user)
    level = await _user_level(user)
    usage = {}
    for kind in KINDS:
        try:
            usage[kind] = _usage(await _user_chats(user, kind))
        except HTTPException:
            usage[kind] = None
    return JSONResponse(
        content={"is_admin": admin, "is_privileged": privileged, "level": level,
                 "limits": None if admin or privileged else limits_for(level), "usage": usage},
        headers={"Cache-Control": "no-store"},
    )
