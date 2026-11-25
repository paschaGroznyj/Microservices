"""Пользовательские ключи внешних сервисов: шифрование и запись в vault.user_secrets.

Формат шифротекста общий с agent_service (app/agent/core/user_secrets.py):
Fernet-токен от JSON {"v": 1, "u": user_id, "p": provider, "n": name, "s": secret}.
u/p/n сверяются при расшифровке, поэтому шифротекст из чужой строки не читается.

Ключи шифрования: USER_SECRETS_KEYS — список Fernet-ключей через запятую, первый
шифрует, остальные только расшифровывают (ротация). Если не задан — FERNET_KEY.
Значение ключа наружу не отдаётся: профиль получает только last4 и даты.
"""
import json
import os
import re

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

# Провайдеры, ключи которых можно сохранить из профиля. Расширять здесь
# и в agent_service, где ключ используется.
PROVIDERS = {
    "routerai": {"label": "RouterAPI", "min_len": 16, "max_len": 256},
}

_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_SECRET_RE = re.compile(r"^[\x21-\x7e]+$")  # печатный ASCII без пробелов


class SecretError(ValueError):
    """Некорректный провайдер или значение ключа (текст безопасно показывать юзеру)."""


def _load_keys() -> MultiFernet:
    raw = os.getenv("USER_SECRETS_KEYS") or os.getenv("FERNET_KEY") or ""
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("USER_SECRETS_KEYS/FERNET_KEY не задан")
    return MultiFernet([Fernet(k) for k in keys])


_fernet: MultiFernet | None = None


def _f() -> MultiFernet:
    global _fernet
    if _fernet is None:
        _fernet = _load_keys()
    return _fernet


def validate(provider: str, secret: str, name: str = "default") -> str:
    spec = PROVIDERS.get(provider)
    if not spec:
        raise SecretError("Неизвестный провайдер")
    if not _NAME_RE.match(name or ""):
        raise SecretError("Некорректное имя ключа")
    secret = (secret or "").strip()
    if not (spec["min_len"] <= len(secret) <= spec["max_len"]):
        raise SecretError(f"Длина ключа {spec['label']}: {spec['min_len']}–{spec['max_len']} символов")
    if not _SECRET_RE.match(secret):
        raise SecretError("Ключ содержит пробелы или недопустимые символы")
    return secret


def encrypt(user_id: int, provider: str, name: str, secret: str) -> str:
    payload = json.dumps({"v": 1, "u": int(user_id), "p": provider, "n": name, "s": secret},
                         separators=(",", ":"))
    return _f().encrypt(payload.encode()).decode()


def decrypt(user_id: int, provider: str, name: str, token: str) -> str | None:
    try:
        data = json.loads(_f().decrypt(token.encode()))
    except (InvalidToken, ValueError):
        return None
    if data.get("u") != int(user_id) or data.get("p") != provider or data.get("n") != name:
        return None
    return data.get("s") or None


def last4(secret: str) -> str:
    return secret[-4:] if len(secret) >= 12 else ""
