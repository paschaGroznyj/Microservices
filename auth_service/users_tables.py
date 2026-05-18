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
        await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                scholar_token VARCHAR(255)
            );
            ALTER TABLE users ADD COLUMN IF NOT EXISTS scholar_token VARCHAR(255);
            
            CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);
            """
        )

        # Миграция для существующих БД
        await conn.execute("""
                           ALTER TABLE users
                               ADD COLUMN IF NOT EXISTS is_guest BOOLEAN DEFAULT FALSE;
                           """)

    async def check_user_password(self, username: str, password: str = None, back_user_id=False):
        async with self.pool.acquire() as conn:
            if password:
                user_in_db = await conn.fetchrow("""
                    SELECT username, password_hash FROM users WHERE username = $1
                """, username)
                if not user_in_db:
                    return None

                is_valid = await asyncio.to_thread(
                    bcrypt.checkpw,
                    password.encode('utf-8'),
                    user_in_db["password_hash"].encode('utf-8')
                )
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

    async def add_user(self, username: str, password: str):
        # Хэшируем пароль (bcrypt)
        password_hash = await asyncio.to_thread(
            self._hash_password,
            password
        )
        try:
            async with self.pool.acquire() as conn:
                user_id = await conn.fetchval("""
                    INSERT INTO users (username, password_hash)
                    VALUES ($1, $2)
                    RETURNING id;
                """, username, password_hash)
                logging.info(f"Добавили {username} с id={user_id}")
                return user_id
        except asyncpg.UniqueViolationError:
            logging.error(f"Юзер {username} уже существует (unique violation)")
            return None

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
                                                          JOIN users u ON u.id = c.username
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
                                                                    JOIN users ON chats.username = users.id
                                                           WHERE users.username = $1)
                                           AND tool_name IS NOT NULL
                                         GROUP BY tool_name
                                         ORDER BY cnt DESC
                                         """, username)

            joined_at = await conn.fetchval("""
                                            SELECT LEAST(
                                                           (SELECT MIN(c.created_at)
                                                            FROM chats c
                                                                     JOIN users u ON c.username = u.id
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