#!/usr/bin/env python3
"""
Перенос чатов и привязанных медиа от одного пользователя к другому.

КАК РАБОТАЕТ:
    Владелец меняется UPDATE-ом user_id в одной транзакции. Строки не копируются:
    id сообщений, message_id, image_key и составной FK chats_forks -> chats(chat_id, id)
    остаются прежними, поэтому ссылки, превью, форки и ключи OBS не ломаются.

    chats              сообщения чата (и пользователя, и агента)
    chat_sidebar_meta  название, избранное, удалён ли чат в сайдбаре
    chat_types_agent   тип агента чата
    chat_files         вложения и файлы агента (file_thumbs висит на chat_files.id)
    generated_images   картинки generate_image (BYTEA)
    chats_forks        ветки чата (по child_chat_id)
    chat_poll_answers  ответы на опросы
    agent_journal      бортовой журнал чата
    agent_tasks        карточки фоновых задач
    agent_loop_tasks   самоцикл агента
    editor_workspaces  воркспейсы /editor, привязанные к этим чатам
    user_settings      у старого владельца сбрасывается active_chat_id, если чат ушёл

    Сами файлы двигать не нужно: ключи OBS (catchpm/{chat_id}/...), media_store в Redis
    (media:chat:{chat_id}:*) и generated_files привязаны к chat_id, а не к user_id.

ЧТО НЕ ПЕРЕНОСИТСЯ (скрипт только показывает, сколько этого остаётся у старого владельца):
    TG/MAX-чаты (added_by_user_id), групповые комнаты, ключи vault.user_secrets,
    воркспейсы без чата, выбранные скиллы, избранные статьи.

ЗАПУСК (внутри контейнера auth_service, env БД уже есть):
    # посмотреть, что будет перенесено: UPDATE выполняются и откатываются
    docker compose exec auth_service python scripts/migrate_user_chats.py \\
        --from 123 --to 456 --all --dry-run

    # перенести все чаты и сохранить список chat_id для отката
    docker compose exec auth_service python scripts/migrate_user_chats.py \\
        --from olduser --to newuser --all --save-list /tmp/moved_123_456.txt

    # перенести отдельные чаты (их ветки-форки уходят вместе с ними)
    docker compose exec auth_service python scripts/migrate_user_chats.py \\
        --from 123 --to 456 --chat-id <uuid> --chat-id <uuid2> --yes

    # откат: тот же список в обратную сторону
    docker compose exec auth_service python scripts/migrate_user_chats.py \\
        --from 456 --to 123 --chats-file /tmp/moved_123_456.txt --yes

    --from/--to принимают users.id или username. Файл из --save-list лежит внутри
    контейнера: забрать его можно через docker compose cp.
    Пока идёт перенос, пользователь не должен писать в эти чаты: сообщение, которое
    сохранится после COMMIT со старым user_id, останется у старого владельца.

КОДЫ ВОЗВРАТА:
    0 — успех (или dry-run)
    1 — пользователь или чат не найден
    2 — ошибка аргументов
    3 — ошибка БД/подключения
    4 — конфликт: у нового владельца уже есть строки с теми же ключами
    5 — отменено пользователем
"""

import argparse
import asyncio
import sys

import asyncpg

try:
    from config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
except Exception:  # запуск не из корня auth_service — пробуем прямые env
    import os
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_USER = os.getenv("DB_USER")
    DB_NAME = os.getenv("DB_NAME")


class MigrationError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _Rollback(Exception):
    """Откатить транзакцию и вернуть результат (dry-run)."""
    def __init__(self, result: dict):
        super().__init__("rollback")
        self.result = result


# (таблица, колонка с chat_id)
CHAT_TABLES = [
    ("chats", "chat_id"),
    ("chat_sidebar_meta", "chat_id"),
    ("chat_types_agent", "chat_id"),
    ("chat_files", "chat_id"),
    ("generated_images", "chat_id"),
    ("chats_forks", "child_chat_id"),
    ("chat_poll_answers", "chat_id"),
    ("agent_journal", "chat_id"),
    ("agent_tasks", "chat_id"),
    ("agent_loop_tasks", "chat_id"),
    ("editor_workspaces", "chat_id"),
]

# Уникальные ключи вида (key, user_id): у нового владельца не должно быть такой же строки.
CONFLICT_CHECKS = [
    ("chat_sidebar_meta", "chat_id"),
    ("chat_types_agent", "chat_id"),
    ("editor_workspaces", "workspace_id"),
]

# Что остаётся у старого владельца: только показываем.
NOT_MOVED = [
    ("telegram_chats", "added_by_user_id", "TG-чаты"),
    ("max_chats", "added_by_user_id", "MAX-чаты"),
    ("group_room_members", "user_id", "групповые комнаты"),
    ("vault.user_secrets", "user_id", "ключи API"),
    ("editor_workspaces", "user_id", "воркспейсы без чата"),
    ("agent_skill_selected", "user_id", "выбранные скиллы"),
    ("favorites", "user_id", "избранные статьи"),
]

# Все chat_id пользователя.
OWNED_CHATS_SQL = """
    SELECT chat_id FROM chats WHERE user_id = $1
    UNION SELECT chat_id FROM chat_sidebar_meta WHERE user_id = $1
    UNION SELECT chat_id FROM chat_types_agent WHERE user_id = $1
    UNION SELECT child_chat_id FROM chats_forks WHERE user_id = $1
"""

# Выбранные чаты плюс их ветки (любой глубины), которыми владеет тот же пользователь.
FORK_DESCENDANTS_SQL = """
    WITH RECURSIVE d(chat_id, depth) AS (
        SELECT unnest($2::text[]), 0
        UNION
        SELECT f.child_chat_id, d.depth + 1
        FROM chats_forks f JOIN d ON f.parent_chat_id = d.chat_id
        WHERE f.user_id = $1 AND d.depth < 64
    )
    SELECT DISTINCT chat_id FROM d
"""


async def _table_exists(conn, table: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table)


async def resolve_user(conn, ref: str) -> dict:
    """users.id (число) или username -> {'id', 'username'}."""
    ref = ref.strip()
    if ref.isdigit():
        row = await conn.fetchrow("SELECT id, username FROM users WHERE id = $1", int(ref))
    else:
        row = await conn.fetchrow("SELECT id, username FROM users WHERE username = $1", ref)
    if not row:
        raise MigrationError(1, f"Пользователь '{ref}' не найден в таблице users.")
    return {"id": row["id"], "username": row["username"]}


async def collect_chats(conn, src_id: int, chat_ids: list[str] | None) -> tuple[list[str], list[str]]:
    """Чаты к переносу и предупреждения."""
    owned = {r["chat_id"] for r in await conn.fetch(OWNED_CHATS_SQL, src_id)}
    warnings: list[str] = []
    if chat_ids is None:
        selected = owned
    else:
        missing = [c for c in chat_ids if c not in owned]
        if missing:
            raise MigrationError(1, "Эти чаты не принадлежат исходному пользователю: " + ", ".join(missing))
        selected = {r["chat_id"] for r in await conn.fetch(FORK_DESCENDANTS_SQL, src_id, chat_ids)}
        extra = sorted(selected - set(chat_ids))
        if extra:
            warnings.append(f"Вместе с чатами уходят их ветки: {', '.join(extra)}")

    orphans = await conn.fetch(
        "SELECT child_chat_id, parent_chat_id FROM chats_forks "
        "WHERE child_chat_id = ANY($1::text[]) AND NOT (parent_chat_id = ANY($1::text[]))",
        sorted(selected),
    )
    for r in orphans:
        warnings.append(
            f"Ветка {r['child_chat_id']} уходит без родителя {r['parent_chat_id']}: "
            "история до точки форка останется в чате другого владельца."
        )
    return sorted(selected), warnings


async def find_conflicts(conn, src_id: int, dst_id: int, chats: list[str]) -> list[str]:
    out = []
    for table, key in CONFLICT_CHECKS:
        if not await _table_exists(conn, table):
            continue
        rows = await conn.fetch(
            f"SELECT s.{key} AS k FROM {table} s "
            f"WHERE s.user_id = $1 AND s.chat_id = ANY($3::text[]) "
            f"AND EXISTS (SELECT 1 FROM {table} t WHERE t.user_id = $2 AND t.{key} = s.{key})",
            src_id, dst_id, chats,
        )
        out += [f"{table}.{key}={r['k']}" for r in rows]
    return out


async def move_chats(conn, src_id: int, dst_id: int, chats: list[str]) -> dict[str, int]:
    """UPDATE user_id по всем таблицам. Вызывать внутри транзакции."""
    counts: dict[str, int] = {}
    for table, col in CHAT_TABLES:
        if not await _table_exists(conn, table):
            counts[table] = -1
            continue
        res = await conn.execute(
            f"UPDATE {table} SET user_id = $2 WHERE user_id = $1 AND {col} = ANY($3::text[])",
            src_id, dst_id, chats,
        )
        counts[table] = int(res.split()[-1])
    if await _table_exists(conn, "user_settings"):
        res = await conn.execute(
            "UPDATE user_settings SET active_chat_id = NULL "
            "WHERE user_id = $1 AND active_chat_id = ANY($2::text[])",
            src_id, chats,
        )
        counts["user_settings.active_chat_id"] = int(res.split()[-1])
    return counts


async def leftovers(conn, src_id: int) -> list[tuple[str, int]]:
    out = []
    for table, col, label in NOT_MOVED:
        if not await _table_exists(conn, table):
            continue
        n = await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {col} = $1", src_id)
        if n:
            out.append((f"{label} ({table})", n))
    return out


async def migrate(conn, src_id: int, dst_id: int, chat_ids: list[str] | None, apply: bool) -> dict:
    """Одна транзакция: сбор чатов, проверка конфликтов, UPDATE. apply=False — откат."""
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout = '10s'")
            chats, warnings = await collect_chats(conn, src_id, chat_ids)
            if not chats:
                raise MigrationError(1, "У исходного пользователя нет чатов для переноса.")
            conflicts = await find_conflicts(conn, src_id, dst_id, chats)
            if conflicts:
                raise MigrationError(4, "У целевого пользователя уже есть строки с теми же ключами: "
                                        + "; ".join(conflicts[:20]))
            counts = await move_chats(conn, src_id, dst_id, chats)
            result = {"chats": chats, "warnings": warnings, "counts": counts,
                      "leftovers": await leftovers(conn, src_id)}
            if not apply:
                raise _Rollback(result)
            return result
    except _Rollback as rb:
        return rb.result


def _print_result(res: dict) -> None:
    print(f"[INFO] Чатов: {len(res['chats'])}")
    for w in res["warnings"]:
        print(f"[WARN] {w}")
    for table, n in res["counts"].items():
        print(f"    {table:<30} {'нет таблицы' if n < 0 else n}")
    if res["leftovers"]:
        print("[INFO] Остаётся у старого владельца (скрипт это не переносит):")
        for label, n in res["leftovers"]:
            print(f"    {label}: {n}")


def _read_chats_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


async def run(args) -> int:
    if not all([DB_HOST, DB_PORT, DB_USER, DB_NAME]):
        print("[ERROR] Не заданы переменные окружения БД (DB_HOST/DB_PORT/DB_USER/DB_NAME). "
              "Запускай внутри контейнера auth_service.", file=sys.stderr)
        return 3

    chat_ids = None
    if args.chat_id or args.chats_file:
        try:
            listed = _read_chats_file(args.chats_file) if args.chats_file else []
        except OSError as e:
            print(f"[ERROR] Не удалось прочитать {args.chats_file}: {e}", file=sys.stderr)
            return 2
        chat_ids = list(dict.fromkeys((args.chat_id or []) + listed))
        if not chat_ids:
            print("[ERROR] Список чатов пуст.", file=sys.stderr)
            return 2

    try:
        conn = await asyncpg.connect(
            user=DB_USER, password=DB_PASSWORD,
            host=DB_HOST, port=DB_PORT, database=DB_NAME,
        )
    except Exception as e:
        print(f"[ERROR] Не удалось подключиться к БД: {e}", file=sys.stderr)
        return 3

    try:
        src = await resolve_user(conn, args.src)
        dst = await resolve_user(conn, args.dst)
        if src["id"] == dst["id"]:
            raise MigrationError(2, "Исходный и целевой пользователь совпадают.")
        print(f"[INFO] Откуда: id={src['id']} '{src['username']}'")
        print(f"[INFO] Куда:   id={dst['id']} '{dst['username']}'")

        if args.dry_run or not args.yes:
            plan = await migrate(conn, src["id"], dst["id"], chat_ids, apply=False)
            _print_result(plan)
            if args.dry_run:
                print("[DRY-RUN] Изменения откатаны, в БД ничего не записано.")
                return 0
            answer = input(f"Перенести {len(plan['chats'])} чатов от id={src['id']} "
                           f"к id={dst['id']}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes", "д", "да"):
                print("[CANCEL] Отменено, в БД ничего не записано.")
                return 5

        res = await migrate(conn, src["id"], dst["id"], chat_ids, apply=True)
        print("[OK] Записано:")
        _print_result(res)
        if args.save_list:
            try:
                with open(args.save_list, "w", encoding="utf-8") as f:
                    f.write(f"# from={src['id']} to={dst['id']}\n" + "\n".join(res["chats"]) + "\n")
                print(f"[OK] Список chat_id для отката: {args.save_list}")
            except OSError as e:
                print(f"[WARN] Перенос записан, но список не сохранён ({e}). Чаты:", file=sys.stderr)
                print("\n".join(res["chats"]), file=sys.stderr)
        return 0
    except MigrationError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return e.code
    except Exception as e:
        print(f"[ERROR] Ошибка БД: {e}", file=sys.stderr)
        return 3
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Перенос чатов и медиа между пользователями (UPDATE user_id в одной транзакции).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from", dest="src", required=True, help="users.id или username старого владельца")
    parser.add_argument("--to", dest="dst", required=True, help="users.id или username нового владельца")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true", help="перенести все чаты пользователя")
    scope.add_argument("--chat-id", action="append", help="chat_id для переноса (можно несколько раз)")
    scope.add_argument("--chats-file", help="файл со списком chat_id, по одному в строке")
    parser.add_argument("--dry-run", action="store_true", help="выполнить и откатить, показать счётчики")
    parser.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение")
    parser.add_argument("--save-list", help="куда записать перенесённые chat_id (для отката)")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
