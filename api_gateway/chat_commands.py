# chat_commands.py
"""
Универсальный диспетчер слэш-команд чата (игровые и сервисные режимы).

Идея: некоторые сообщения в поле ввода — это не текст для агента, а команды
включения/выключения режима (напр. /chess — шахматы). Gateway перехватывает
их ДО отправки в chats_service/агента: ставит флаг режима в Redis и шлёт
фронту сигнал рендерить/убрать игровой интерфейс.

Архитектура (вариант А):
  — gateway тонкий: только флаг режима в Redis + сигнал фронту.
  — сами ходы внутри режима — обычные сообщения с подготовленным JSON
    (фигура + шаг + движок), летят в агента штатно (gateway не трогает).
  — состояние партии = история чата (память langchain_agent.py).

Модель данных в Redis:
  chat:mode:{chat_id}  STRING(json) = {mode, engine, player_color, started_at}
                       TTL MODE_TTL — самоочистка под стать воркспейсу игры.

Расширяемость: новая игра = новая запись в COMMANDS.
Все вызовы безопасны: Redis недоступен — не роняем запрос, логируем.
"""
import os
import json
import time
import random
import logging

logger = logging.getLogger(__name__)

MODE_TTL = int(os.getenv("CHAT_MODE_TTL", "36000"))  # 10 часов под стать воркспейсу
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

try:
    import redis.asyncio as aioredis
    _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
except Exception as e:  # noqa
    logger.warning("chat_commands: redis init failed: %s", e)
    _redis = None


# ---------------------------------------------------------------------------
# Реестр команд. Ключ — базовая команда (первое слово).
# Для игр mode — идентификатор режима, engines — допустимые движки.
# ---------------------------------------------------------------------------
COMMANDS = {
    "/chess": {
        "mode": "chess",
        "engines": ["agent", "best"],  # /chess -> agent (по умолч), /chess best -> stockfish
        "default_engine": "agent",
        "colors": ["white", "black"],  # цвет игрока выбирается случайно
    },
    # будущие игры: "/checkers": {...}, "/2048": {...}
}

STOP_COMMAND = "/stop"


def _key(chat_id) -> str:
    return f"chat:mode:{chat_id}"


def parse_command(text: str):
    """Распознаёт слэш-команду в тексте сообщения.

    Возвращает dict:
      {"kind": "start", "command": "/chess", "mode": "chess",
       "engine": "agent|best", "spec": {...}}
      {"kind": "stop"}
      None — это не команда (обычное сообщение/ход).

    Регистронезависимо, лишние пробелы игнорируются.
    Форматы: "/chess", "/chess best", "/chess play", "/stop".
    """
    if not text or not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split()
    base = parts[0].lower()
    arg = parts[1].lower() if len(parts) > 1 else None

    if base == STOP_COMMAND:
        return {"kind": "stop"}

    spec = COMMANDS.get(base)
    if not spec:
        return None  # неизвестная команда — пусть улетает как обычный текст

    # определяем движок по аргументу
    engine = spec["default_engine"]
    if arg == "best":
        engine = "best"
    elif arg in ("play", "agent"):
        engine = "agent"

    return {
        "kind": "start",
        "command": base,
        "mode": spec["mode"],
        "engine": engine,
        "spec": spec,
    }


async def set_mode(chat_id, mode: str, engine: str, player_color: str) -> dict:
    """Ставит флаг режима в Redis. Возвращает сохранённый payload."""
    payload = {
        "mode": mode,
        "engine": engine,
        "player_color": player_color,
        "started_at": time.time(),
    }
    if _redis is None or chat_id is None:
        return payload
    try:
        await _redis.set(_key(chat_id), json.dumps(payload), ex=MODE_TTL)
    except Exception as e:
        logger.debug("chat_commands.set_mode failed: %s", e)
    return payload


async def set_engine(chat_id, engine: str) -> dict | None:
    """Обновляет только движок в уже активном режиме (переключатель до
    первого хода). Возвращает обновлённый payload или None, если режим
    не активен. Не сбрасывает started_at/player_color."""
    if _redis is None or chat_id is None:
        return None
    try:
        raw = await _redis.get(_key(chat_id))
        if not raw:
            return None
        payload = json.loads(raw)
        payload["engine"] = "best" if engine == "best" else "agent"
        # TTL сохраняем прежним диапазоном режима.
        await _redis.set(_key(chat_id), json.dumps(payload), ex=MODE_TTL)
        return payload
    except Exception as e:
        logger.debug("chat_commands.set_engine failed: %s", e)
        return None


async def clear_mode(chat_id) -> None:
    """Снимает флаг режима (/stop). Идемпотентно."""
    if _redis is None or chat_id is None:
        return
    try:
        await _redis.delete(_key(chat_id))
    except Exception as e:
        logger.debug("chat_commands.clear_mode failed: %s", e)


async def get_mode(chat_id) -> dict | None:
    """Текущий режим чата или None. Фронт дёргает при загрузке/переключении."""
    if _redis is None or chat_id is None:
        return None
    try:
        raw = await _redis.get(_key(chat_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.debug("chat_commands.get_mode failed: %s", e)
        return None


def pick_color(spec: dict) -> str:
    """Случайный цвет игрока после /chess."""
    colors = spec.get("colors") or ["white"]
    return random.choice(colors)
