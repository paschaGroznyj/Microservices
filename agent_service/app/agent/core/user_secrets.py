"""Ключи внешних сервисов пользователя (vault.user_secrets) для инструментов агента.

Пишет auth_service (профиль), здесь только чтение основным пулом агента (DB_USER).
Роли SQL-инструмента (knowledge_manager, my_journal) к схеме vault доступа не имеют.

Формат шифротекста общий с auth_service/user_secrets.py: Fernet-токен от JSON
{"v": 1, "u": user_id, "p": provider, "n": name, "s": secret}; u/p/n сверяются.
Ключи шифрования: USER_SECRETS_KEYS (через запятую, ротация) или FERNET_KEY.

Значение ключа живёт только в памяти вызова инструмента: не логируется,
не возвращается модели и не кладётся в контекст запроса.
"""
import json
import logging
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import ROUTER_API_KEY

log = logging.getLogger(__name__)

ROUTERAI = "routerai"

NO_KEY_MSG = (
    "RouterAPI key is not set for this user. Tell the user to add their own "
    "RouterAPI key in Profile → «Ключи API» (generation is billed to that key), "
    "then retry."
)
NO_OWNER_MSG = (
    "RouterAPI generation here needs a personal key, but this chat has no "
    "linked web account to take it from. Ask the user to run it from the web chat."
)

_fernet: MultiFernet | None = None


def _f() -> MultiFernet | None:
    global _fernet
    if _fernet is None:
        raw = os.getenv("USER_SECRETS_KEYS") or os.getenv("FERNET_KEY") or ""
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            return None
        _fernet = MultiFernet([Fernet(k) for k in keys])
    return _fernet


def decrypt(user_id: int, provider: str, name: str, token: str) -> str | None:
    f = _f()
    if f is None or not token:
        return None
    try:
        data = json.loads(f.decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return None
    if data.get("u") != int(user_id) or data.get("p") != provider or data.get("n") != name:
        return None
    return data.get("s") or None


async def get_user_secret(pool, user_id: int, provider: str, name: str = "default") -> str | None:
    """Расшифрованный ключ юзера или None. Отмечает last_used_at."""
    if pool is None or not user_id:
        return None
    try:
        row = await pool.fetchrow(
            "UPDATE vault.user_secrets SET last_used_at = now() "
            "WHERE user_id = $1 AND provider = $2 AND name = $3 RETURNING secret_enc",
            int(user_id), provider, name)
    except Exception as e:  # схема ещё не применена, нет прав и т.п.
        log.warning("user_secret read failed provider=%s: %s", provider, type(e).__name__)
        return None
    if not row:
        return None
    secret = decrypt(int(user_id), provider, name, row["secret_enc"])
    if secret is None:
        log.warning("user_secret decrypt failed user_id=%s provider=%s", user_id, provider)
    return secret


async def resolve_router_api_key(agent) -> tuple[str | None, str | None]:
    """(api_key, error) для RouterAPI в текущем запросе.

    Владелец ключа кладётся в контекст запроса (media_key_owner) в process_query:
    web-юзер, автор группового сообщения или автор автотриггера с web-аккаунтом.
    Сначала личный ключ владельца. Общий ROUTER_API_KEY из env — только админу.
    """
    owner = agent._ctx_get("media_key_owner") if hasattr(agent, "_ctx_get") else None
    owner = owner if isinstance(owner, dict) else {}
    uid = owner.get("user_id")
    if uid:
        pool = getattr(getattr(agent, "db", None), "pool", None)
        key = await get_user_secret(pool, uid, ROUTERAI)
        if key:
            return key, None
    if owner.get("is_admin") and ROUTER_API_KEY:
        return ROUTER_API_KEY, None
    return None, (NO_KEY_MSG if uid else NO_OWNER_MSG)
