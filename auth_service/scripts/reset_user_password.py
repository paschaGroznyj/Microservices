#!/usr/bin/env python3
"""
Утилита сброса/смены пароля пользователя (таблица users в auth_service).

ЗАЧЕМ:
    Пароли в users.password_hash хранятся в виде bcrypt-хеша ($2b$...).
    При аутентификации через sigma/login выполняется bcrypt.checkpw(введённый,
    stored_hash) — поэтому НЕЛЬЗЯ просто записать в БД plain-пароль обычным
    UPDATE: логин перестанет проходить (checkpw не сойдётся). Нужно захешировать
    новый пароль тем же bcrypt с теми же раундами, что использует сервис
    (BCRYPT_ROUNDS = 10), и записать именно хеш.

    Этот скрипт принимает username и новый пароль в аргументах, хеширует пароль
    так же, как auth_service._hash_password, и обновляет password_hash в БД.
    Задел под будущую login page (форма "восстановить/сменить пароль").

ЗАПУСК (внутри контейнера auth_service, где доступны env БД и пакет bcrypt):
    # интерактивный ввод пароля (не светится в истории/логах) — РЕКОМЕНДУЕТСЯ:
    docker compose exec auth_service python scripts/reset_user_password.py --username alice

    # пароль аргументом (удобно для автоматизации, но попадает в историю shell):
    docker compose exec auth_service python scripts/reset_user_password.py \\
        --username alice --password 'NewStr0ng!Pass'

    # dry-run: показать что будет сделано, без записи в БД:
    docker compose exec auth_service python scripts/reset_user_password.py \\
        --username alice --password 'x' --dry-run

    # сгенерировать случайный пароль и вывести его (для сброса "забыл пароль"):
    docker compose exec auth_service python scripts/reset_user_password.py \\
        --username alice --generate

КОДЫ ВОЗВРАТА:
    0 — успех (или dry-run)
    1 — пользователь не найден
    2 — ошибка валидации аргументов/пароля
    3 — ошибка БД/подключения
"""

import argparse
import asyncio
import getpass
import secrets
import string
import sys

import asyncpg
import bcrypt

# Конфиг подключения берём из того же config.py, что и сервис,
# чтобы гарантированно совпадали DB_* переменные окружения контейнера.
try:
    from config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
except Exception:  # запуск не из корня auth_service — пробуем прямые env
    import os
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_USER = os.getenv("DB_USER")
    DB_NAME = os.getenv("DB_NAME")

# ВАЖНО: держим в синхроне с auth_service/users_tables.py -> BCRYPT_ROUNDS
BCRYPT_ROUNDS = 10

MIN_PASSWORD_LEN = 6


def hash_password(password: str) -> str:
    """Хеширование пароля bcrypt — 1:1 с DataBaseUser._hash_password."""
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode(), salt).decode()


def generate_password(length: int = 16) -> str:
    """Криптостойкий случайный пароль (буквы+цифры+спецсимволы)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        # гарантируем разнообразие символов
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd


async def reset_password(username: str, new_password: str, dry_run: bool = False) -> int:
    """Обновляет password_hash пользователя. Возвращает код выхода."""
    if not all([DB_HOST, DB_PORT, DB_USER, DB_NAME]):
        print("[ERROR] Не заданы переменные окружения БД (DB_HOST/DB_PORT/DB_USER/DB_NAME). "
              "Запускай внутри контейнера auth_service.", file=sys.stderr)
        return 3

    password_hash = hash_password(new_password)

    try:
        conn = await asyncpg.connect(
            user=DB_USER, password=DB_PASSWORD,
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
        )
    except Exception as e:
        print(f"[ERROR] Не удалось подключиться к БД: {e}", file=sys.stderr)
        return 3

    try:
        row = await conn.fetchrow(
            "SELECT id, username, source FROM users WHERE username = $1", username
        )
        if not row:
            print(f"[NOT FOUND] Пользователь '{username}' не найден в таблице users.",
                  file=sys.stderr)
            return 1

        user_id = row["id"]
        source = row["source"]
        print(f"[INFO] Найден пользователь: id={user_id}, username='{username}', source='{source}'")

        if dry_run:
            print(f"[DRY-RUN] Пароль НЕ изменён. Был бы записан bcrypt-хеш "
                  f"(rounds={BCRYPT_ROUNDS}, префикс {password_hash[:7]}...).")
            return 0

        result = await conn.execute(
            "UPDATE users SET password_hash = $1 WHERE username = $2",
            password_hash, username,
        )
        # asyncpg возвращает строку вида "UPDATE 1"
        updated = result.split()[-1] == "1"
        if updated:
            print(f"[OK] Пароль пользователя '{username}' (id={user_id}) успешно обновлён.")
            print("[OK] Теперь вход через sigma/login сойдётся по bcrypt.checkpw.")
            return 0
        print(f"[ERROR] UPDATE не затронул ни одной строки ('{result}').", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[ERROR] Ошибка при обновлении пароля: {e}", file=sys.stderr)
        return 3
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Сброс/смена пароля пользователя (таблица users, auth_service).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--username", "-u", required=True,
                        help="username пользователя в таблице users")
    parser.add_argument("--password", "-p", default=None,
                        help="новый пароль (если не задан и нет --generate — спросит интерактивно)")
    parser.add_argument("--generate", "-g", action="store_true",
                        help="сгенерировать случайный пароль и вывести его")
    parser.add_argument("--length", type=int, default=16,
                        help="длина генерируемого пароля (по умолчанию 16)")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать что будет сделано, без записи в БД")
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        print("[ERROR] username пуст.", file=sys.stderr)
        return 2

    # Определяем новый пароль по приоритету: --generate > --password > интерактив
    if args.generate:
        new_password = generate_password(args.length)
        print(f"[GENERATED] Сгенерирован пароль для '{username}':\n    {new_password}")
        print("[GENERATED] Сохрани его — повторно не покажется.")
    elif args.password is not None:
        new_password = args.password
    else:
        # Интерактивный ввод — пароль не попадёт в историю shell
        new_password = getpass.getpass("Новый пароль: ")
        confirm = getpass.getpass("Повторите пароль: ")
        if new_password != confirm:
            print("[ERROR] Пароли не совпадают.", file=sys.stderr)
            return 2

    if len(new_password) < MIN_PASSWORD_LEN:
        print(f"[ERROR] Пароль слишком короткий (мин. {MIN_PASSWORD_LEN} символов).",
              file=sys.stderr)
        return 2

    return asyncio.run(reset_password(username, new_password, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
