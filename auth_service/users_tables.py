import asyncio
import asyncpg
import bcrypt
from config import DB_PASSWORD, DB_HOST, DB_PORT, DB_USER, DB_NAME
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
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
                    min_size=1,
                    max_size=10
                )

                # Инициализация структуры базы данных
                async with self.pool.acquire() as conn:
                    await DataBaseUser.__init_db(conn)


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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);
            """
        )

    async def check_user_password(self, username: str, password: str = None, back_user_id=False):
        async with self.pool.acquire() as conn:
            if password:
                user_in_db = await conn.fetchrow("""
                    SELECT username, password_hash FROM users WHERE username = $1
                """, username)
                if not user_in_db:
                    return None

                if bcrypt.checkpw(password.encode(), user_in_db["password_hash"].encode()):
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
        salt = bcrypt.gensalt()
        password_hash = bcrypt.hashpw(password.encode(), salt).decode()

        flag_user = await self.check_user_password(username)
        if not flag_user:
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
        else:
            logging.error(f"Юзер {username} уже в домике (найден в БД)")
            return None
