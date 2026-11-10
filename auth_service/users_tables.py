import asyncio
import asyncpg
import bcrypt
from config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME, FERNET_KEY
import logging
from cryptography.fernet import Fernet

fernet = Fernet(FERNET_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

BCRYPT_ROUNDS=10


class DataBaseUser:
    async def init_db_pool(self):
        """Инициализация пула подключений к базе данных."""
        max_retries = 5
        retry_delay = 2.0

        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    user=DB_USER,
                    password=DB_PASSWORD,
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    min_size=5,
                    max_size=50,
                    max_queries=50000,
                    max_inactive_connection_lifetime=300,
                    command_timeout=60,
                    timeout=60,
                    statement_cache_size=100
                )
                # Инициализация структуры базы данных
                async with self.pool.acquire() as conn:
                    await DataBaseUser.__init_db(conn)
                    return

            except Exception as e:
                print("Пока не удалось подключиться")
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                else:
                    raise

    @staticmethod
    async def __init_db(conn):
        # DDL-схема таблиц (users, app_updates, user_seen_updates,
        # user_onboarding, user_preferences) вынесена в postgres/sql/01_auth/
        await conn.execute("SET TIME ZONE '+4'")

    async def check_user_password(self, username: str, password: str = None, back_user_id=False):
        async with self.pool.acquire() as conn:
            if password:
                user_in_db = await conn.fetchrow("""
                    SELECT username, password_hash FROM users WHERE username = $1
                """, username)
                if not user_in_db:
                    return None

                stored_hash = user_in_db["password_hash"]
                # Внешние (authentik) юзеры и любые не-bcrypt значения не имеют
                # валидного пароля — bcrypt.checkpw на них бросает
                # ValueError: Invalid salt. Отбиваем это как обычный отказ входа.
                if not stored_hash or not stored_hash.startswith("$2"):
                    return None

                try:
                    is_valid = await asyncio.to_thread(
                        bcrypt.checkpw,
                        password.encode('utf-8'),
                        stored_hash.encode('utf-8')
                    )
                except ValueError:
                    return None
                if is_valid:
                    if back_user_id == True:
                        user_id = await conn.fetchrow("""
                                            SELECT id FROM users WHERE username = $1
                                                      """, username)
                        return user_id
                    return user_in_db
                return None
            else:
                # Просто проверка наличия юзера
                user = await conn.fetchrow("""
                                SELECT id, username FROM users WHERE username = $1
                            """, username)
                if user:
                    return {
                        "id": user["id"],
                        "username": user["username"]
                    }
                return None

    # async def add_user(self, username: str, password: str):
    #     # Хэшируем пароль (bcrypt)
    #     password_hash = await asyncio.to_thread(
    #         self._hash_password,
    #         password
    #     )
    #     try:
    #         async with self.pool.acquire() as conn:
    #             user_id = await conn.fetchval("""
    #                 INSERT INTO users (username, password_hash)
    #                 VALUES ($1, $2)
    #                 RETURNING id;
    #             """, username, password_hash)
    #             logging.info(f"Добавили {username} с id={user_id}")
    #             return user_id
    #     except asyncpg.UniqueViolationError:
    #         logging.error(f"Юзер {username} уже существует (unique violation)")
    #         return None

    async def add_user(self, username: str, password: str = None,
                       source: str = "catchPM", external: bool = False):
        """
        Создание пользователя.

        external=False (парольная регистрация через форму логина/регистрации):
            - хешируем РЕАЛЬНЫЙ пароль (bcrypt), чтобы потом можно было залогиниться;
            - при занятом username возвращаем None (регистрация под чужим ником
              и вход в его аккаунт невозможны).

        external=True (authentik, пароля нет):
            - пишем dummy-хеш (парольный вход по нему невозможен: bcrypt.checkpw
              на него вернёт False/ошибку, но такой юзер и не логинится паролем);
            - идемпотентно: при существующем username возвращаем его id
              (нужно для повторных входов через authentik).
        """
        DUMMY_PASSWORD_HASH = "EXTERNALLY_AUTHENTICATED_NO_LOGIN_POSSIBLE"

        if external:
            password_hash = DUMMY_PASSWORD_HASH
        else:
            if not password:
                raise ValueError("Пароль обязателен для парольной регистрации")
            password_hash = await asyncio.to_thread(self._hash_password, password)

        try:
            async with self.pool.acquire() as conn:
                user_id = await conn.fetchval("""
                    INSERT INTO users (username, password_hash, source)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id;
                """, username, password_hash, source)

                if user_id:
                    logging.info(f"Создан новый пользователь {username} (source={source}, external={external}) с id={user_id}")
                    return user_id

                # username уже занят
                if external:
                    # authentik: идемпотентный возврат существующего id
                    existing_id = await conn.fetchval(
                        "SELECT id FROM users WHERE username = $1", username
                    )
                    logging.info(f"authentik: пользователь {username} уже существует, id={existing_id}")
                    return existing_id

                # парольная регистрация: НЕ отдаём чужой id
                logging.warning(f"Регистрация отклонена: username {username} уже занят")
                return None

        except Exception as e:
            logging.error(f"Ошибка при работе с пользователем {username}: {e}")
            raise

    def _encrypt_token(self, token: str) -> str:
        return fernet.encrypt(token.encode()).decode()

    def _decrypt_token(self, encrypted: str) -> str:
        return fernet.decrypt(encrypted.encode()).decode()

    async def set_scholar_token(self, username: str, token: str) -> bool:
        encrypted = await asyncio.to_thread(self._encrypt_token, token)
        print(encrypted, len(encrypted))
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                                            UPDATE users
                                            SET scholar_token = $1
                                            WHERE username = $2;
                                            """, encrypted, username)
                return result.split()[-1] == "1"
        except Exception as e:
            logging.error(f"Ошибка при сохранении токена для {username}: {e}")
            return False

    async def get_scholar_token(self, username: str) -> str | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT scholar_token FROM users WHERE username = $1", username
            )
        if row and row["scholar_token"]:
            return await asyncio.to_thread(self._decrypt_token, row["scholar_token"])
        return None

    async def get_user_preferences(self, user_id: int) -> dict:
        """Персональные настройки юзера. Нет строки — дефолт (пустая палитра)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT palette, extra FROM user_preferences WHERE user_id = $1",
                user_id
            )
        if not row:
            return {"palette": {}, "extra": {}}
        import json
        palette = row["palette"]
        extra = row["extra"]
        # asyncpg отдаёт JSONB как str — приводим к dict.
        if isinstance(palette, str):
            palette = json.loads(palette)
        if isinstance(extra, str):
            extra = json.loads(extra)
        return {"palette": palette or {}, "extra": extra or {}}

    async def upsert_user_preferences(self, user_id: int,
                                      palette: dict | None = None,
                                      extra: dict | None = None) -> dict:
        """UPSERT палитры/extra по user_id. Переданные поля перезаписываются
        целиком, непереданные (None) — остаются как были."""
        import json
        set_parts = ["updated_at = CURRENT_TIMESTAMP"]
        args = [user_id]
        # Для INSERT нужны значения по умолчанию, для UPDATE — только присланные.
        ins_palette = json.dumps(palette or {})
        ins_extra = json.dumps(extra or {})
        idx = 2
        if palette is not None:
            set_parts.append(f"palette = ${idx}::jsonb")
            args.append(json.dumps(palette))
            idx += 1
        if extra is not None:
            set_parts.append(f"extra = ${idx}::jsonb")
            args.append(json.dumps(extra))
            idx += 1
        set_clause = ", ".join(set_parts)
        sql = f"""
            INSERT INTO user_preferences (user_id, palette, extra)
            VALUES ($1, ${idx}::jsonb, ${idx + 1}::jsonb)
            ON CONFLICT (user_id) DO UPDATE SET {set_clause}
            RETURNING palette, extra;
        """
        args.extend([ins_palette, ins_extra])
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
        p = row["palette"]
        e = row["extra"]
        if isinstance(p, str):
            p = json.loads(p)
        if isinstance(e, str):
            e = json.loads(e)
        return {"palette": p or {}, "extra": e or {}}

    def _hash_password(self, password: str) -> str:
        """Синхронная функция для выполнения в executor"""
        salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS) # Синхронная функция, которая все блокировала
        password_hash = bcrypt.hashpw(password.encode(), salt).decode()
        return password_hash

    async def add_guest_user(self):
        """Создаёт гостевого пользователя с рандомным username"""
        import secrets
        import string

        for _ in range(5):  # на случай коллизии — пробуем до 5 раз
            suffix = ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
            username = f"guest_{suffix}"
            # Рандомный хэш — гость не сможет залогиниться по паролю
            fake_password_hash = secrets.token_hex(32)

            try:
                async with self.pool.acquire() as conn:
                    user_id = await conn.fetchval("""
                                                  INSERT INTO users (username, password_hash, is_guest)
                                                  VALUES ($1, $2, TRUE) RETURNING id;
                                                  """, username, fake_password_hash)
                    logging.info(f"Создан гостевой пользователь {username} с id={user_id}")
                    return user_id, username
            except asyncpg.UniqueViolationError:
                logging.warning(f"Коллизия гостевого username {username}, повторяем...")
                continue

        logging.error("Не удалось создать гостевого пользователя после 5 попыток")
        return None, None

    async def get_user_stats(self, username: str) -> dict:
        """Полная статистика пользователя по username"""
        async with self.pool.acquire() as conn:
            agent_requests = await conn.fetchval("""
                                                 SELECT COUNT(c.id)
                                                 FROM chats c
                                                          JOIN users u ON u.id = c.user_id
                                                 WHERE u.username = $1
                                                   AND c.is_from_bot = FALSE
                                                 """, username)

            search_requests = await conn.fetchval("""
                                                  SELECT COUNT(s_r.id)
                                                  FROM search_requests s_r
                                                           JOIN users u ON u.id = s_r.user_id
                                                  WHERE u.username = $1
                                                    AND s_r.user_query != 'default'
                                                  """, username)

            favorites_count = await conn.fetchval("""
                                                  SELECT COUNT(f.id)
                                                  FROM favorites f
                                                           JOIN users u ON u.id = f.user_id
                                                  WHERE u.username = $1
                                                  """, username)

            tool_rows = await conn.fetch("""
                                         SELECT tool_name, COUNT(*) AS cnt
                                         FROM chats
                                         WHERE chat_id IN (SELECT DISTINCT chat_id
                                                           FROM chats
                                                                    JOIN users ON chats.user_id = users.id
                                                           WHERE users.username = $1)
                                           AND tool_name IS NOT NULL
                                         GROUP BY tool_name
                                         ORDER BY cnt DESC
                                         """, username)

            joined_at = await conn.fetchval("""
                                            SELECT LEAST(
                                                           (SELECT MIN(c.created_at)
                                                            FROM chats c
                                                                     JOIN users u ON c.user_id = u.id
                                                            WHERE u.username = $1),
                                                           (SELECT MIN(s.created_at)
                                                            FROM search_requests s
                                                                     JOIN users u ON s.user_id = u.id
                                                            WHERE u.username = $1)
                                                   )
                                            """, username)

            token = await conn.fetchval("""SELECT scholar_token
                                                FROM users
                                                WHERE username = $1
                                             """, username)
            token_bool = None
            if token:
                token_bool = True
            # Форматируем для фронта — "янв 2026"
            MONTHS_RU = {
                1: "января", 2: "февраля", 3: "марта", 4: "апреля",
                5: "мая", 6: "июня", 7: "июля", 8: "августа",
                9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
            }

            if joined_at:
                joined = f"{MONTHS_RU[joined_at.month]} {joined_at.year}"
            else:
                from datetime import date
                today = date.today()
                joined = f"{MONTHS_RU[today.month]} {today.year}"

            return {
                "username": username,
                "joined": joined,
                "stats":{
                    "agent_requests": agent_requests or 0,
                    "search_requests": search_requests or 0,
                    "favorites_count": favorites_count or 0,
                    "tools": [{"tool": r["tool_name"], "count": r["cnt"]} for r in tool_rows],
                    "token_bool": token_bool
                }
            }

    # ─────────────────────────────────────────────────────────────────────────
    # What's New (обновления) + Onboarding (демо-модалка)
    # ─────────────────────────────────────────────────────────────────────────
    # Сколько свежих обновлений отдаём сразу с текстом в lazy-режиме:
    # текущее (последнее) + одно «заготовленное» для кнопки «назад».
    WHATSNEW_EAGER_BODIES = 2

    async def get_pending_whatsnew(self, user_id: int, lazy: bool = False) -> dict:
        """
        Что показать юзеру при входе:
          - demo: показать ли онбординг-демо (True, если нет отметки demo_seen);
          - updates: список published-обновлений, которые юзер ещё не закрыл
                     (по возрастанию created_at — показываем от старых к новым).

        lazy=True: body отдаётся только у WHATSNEW_EAGER_BODIES самых свежих,
        у остальных body=NULL и has_body=False — фронт догружает их по одному
        через get_app_update_body(), когда юзер листает назад.
        """
        eager = self.WHATSNEW_EAGER_BODIES if lazy else None
        # $1 — user_id (NULL = MOCK без фильтра просмотренных), $2 — лимит тел (NULL = все).
        sql = """
            SELECT id, version, title, created_at,
                   CASE WHEN $2::int IS NULL OR rn <= $2::int THEN body END AS body,
                   ($2::int IS NULL OR rn <= $2::int) AS has_body
            FROM (
                SELECT au.id, au.version, au.title, au.body, au.created_at,
                       ROW_NUMBER() OVER (ORDER BY au.created_at DESC, au.id DESC) AS rn
                FROM app_updates au
                WHERE au.is_published = TRUE
                  AND ($1::int IS NULL OR NOT EXISTS (
                      SELECT 1 FROM user_seen_updates su
                      WHERE su.update_id = au.id AND su.user_id = $1::int
                  ))
            ) t
            ORDER BY created_at ASC, id ASC
        """
        async with self.pool.acquire() as conn:
            # --- MOCK для тестового юзера 56: всегда демо + все published-обновления ---
            if user_id == 56:
                rows = await conn.fetch(sql, None, eager)
                return {"demo": True, "updates": self._whatsnew_rows(rows)}
            # --- конец MOCK ---
            demo_seen = await conn.fetchval("""
                SELECT demo_seen FROM user_onboarding WHERE user_id = $1
            """, user_id)
            # Нет строки -> демо ещё не видел -> показываем.
            demo = not bool(demo_seen)

            rows = await conn.fetch(sql, user_id, eager)
            return {"demo": demo, "updates": self._whatsnew_rows(rows)}

    @staticmethod
    def _whatsnew_rows(rows) -> list[dict]:
        updates = []
        for r in rows:
            item = dict(r)
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat()
            updates.append(item)
        return updates

    async def get_app_update_body(self, update_id: int) -> dict | None:
        """Текст одного published-обновления (ленивая догрузка в модалке What's New)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, body FROM app_updates
                WHERE id = $1 AND is_published = TRUE
            """, int(update_id))
            return dict(row) if row else None

    async def mark_demo_seen(self, user_id: int) -> bool:
        """Отметить, что юзер закрыл онбординг-демо (идемпотентно)."""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_onboarding (user_id, demo_seen, demo_seen_at)
                VALUES ($1, TRUE, NOW())
                ON CONFLICT (user_id)
                DO UPDATE SET demo_seen = TRUE,
                              demo_seen_at = COALESCE(user_onboarding.demo_seen_at, NOW())
            """, user_id)
            return True

    async def mark_update_seen(self, user_id: int, update_id: int) -> bool:
        """Отметить, что юзер закрыл конкретное обновление (идемпотентно)."""
        return await self.mark_updates_seen(user_id, [int(update_id)])

    async def mark_updates_seen(self, user_id: int, update_ids: list[int]) -> bool:
        """Отметить список обновлений просмотренными для пользователя (идемпотентно)."""
        clean_ids = sorted({int(x) for x in (update_ids or []) if x is not None})
        if not clean_ids:
            return True
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO user_seen_updates (user_id, update_id)
                SELECT $1, unnest($2::int[])
                ON CONFLICT (user_id, update_id) DO NOTHING
            """, int(user_id), clean_ids)
            return True

    async def add_app_update(self, title: str, body: str,
                             version: str = None, is_published: bool = True) -> int:
        """Завести новое обновление (наполнение каталога руками)."""
        async with self.pool.acquire() as conn:
            update_id = await conn.fetchval("""
                INSERT INTO app_updates (version, title, body, is_published)
                VALUES ($1, $2, $3, $4)
                RETURNING id
            """, version, title, body, is_published)
            logging.info(f"Создано обновление id={update_id} (version={version}, published={is_published})")
            return update_id

    async def get_user_id_by_username(self, username: str) -> int | None:
        """Достаём users.id по username (для эндпоинтов, где есть только sub из JWT)."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT id FROM users WHERE username = $1", username)
