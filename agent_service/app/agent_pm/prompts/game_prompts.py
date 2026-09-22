"""
game_prompts.py
===============
Модуль промпт-инструкций для игровых режимов (chess и будущие игры).

Механика:
Фронт, пока игровая доска открыта, шлёт каждый ход как обычное сообщение
с фиксированным парсируемым префиксом-маркером. agent_service детектит
маркер и подмешивает соответствующую игровую инструкцию в message.
Когда игра завершена / команда /stop — фронт перестаёт слать префикс,
сообщения снова обычные, промпт не подмешивается.
"""
import json
from typing import Optional, Tuple

# Фиксированные маркеры ходов (по режимам)
CHESS_MOVE_MARKER = "[CHESS_MOVE]"

# Адрес контейнера agent_apps внутри DinD (резолвится из execute_python_code)
AGENT_APPS_BASE = "http://sandbox_dind:8100"


def _chess_prompt(engine: str) -> str:
    engine = engine if engine in ("best", "agent") else "agent"
    return f"""[CHESS_MODE_CONTEXT] АКТИВЕН ШАХМАТНЫЙ РЕЖИМ. Пользователь сделал ход на доске.
Сообщение начинается с маркера {CHESS_MOVE_MARKER}, за которым идёт JSON хода:
{{"fen": "<текущая позиция FEN>", "player_move": "<ход игрока в UCI, напр. e2e4>", "engine": "{engine}"}}

ТВОЯ ЗАДАЧА — обработать ход через сервис agent_apps и вернуть ответ-контракт.

ШАГИ (выполняй СТРОГО, не импровизируй):
1. Распарсь JSON после маркера: fen, player_move, engine.
2. Вызови execute_python_code с requests-запросом к сервису agent_apps:

   import requests, json
   r = requests.post("{AGENT_APPS_BASE}/apps/chess/move", json={{
       "fen": <fen>, "player_move": <player_move>, "engine": "{engine}"
   }}, timeout=20)
   print(r.status_code); print(json.dumps(r.json(), ensure_ascii=False))

3. РЕЖИМ engine="{engine}":
   - Если engine == "best": сервис сам посчитает ответный ход Stockfish и вернёт готовый chess_move. Один вызов — и готово.
   - Если engine == "agent": это ДВА прохода.
     * pass1: тот же вызов вернёт {{"awaiting": "agent_move", "legal_moves": [...], "engine_hint": "<uci>", "fen": "<fen после хода игрока>"}}. После хода игрока ТЫ играешь за соперника: выбери свой ход ИЗ legal_moves (engine_hint — подсказка Stockfish, можешь взять её или сыграть по-своему, но ТОЛЬКО легальный ход).
     * pass2: повторный вызов с тем же fen/player_move ПЛЮС поле "agent_move": "<твой uci>". Сервис применит оба хода и вернёт финальный chess_move.

4. ОБРАБОТКА ОШИБОК: если ответ содержит {{"chess_error": "illegal_player_move", ...}} — ход игрока нелегален. НЕ придумывай позицию, верни короткий комментарий "Ход невозможен" + тот же chess_error в блоке JSON, чтобы фронт откатил фигуру.

5. ФОРМАТ ОТВЕТА ПОЛЬЗОВАТЕЛЮ (СТРОГО):
   - Сначала короткий живой комментарий к ходу (1-2 предложения, можно с характером: "Сицилианка, люблю это", "Шах! Осторожнее").
   - ЗАТЕМ отдельным блоком кода json — РОВНО тот chess_move (или chess_error), что вернул сервис, без изменений. Фронт парсит этот JSON и обновляет доску.
   - НИЧЕГО не выдумывай: fen, san, uci, game_status — только из ответа сервиса.

6. КОНЕЦ ПАРТИИ: если game_status в (checkmate, stalemate, draw*, ...) — добавь финальный комментарий (кто победил / ничья). Фронт сам закроет режим.
ВАЖНО: не упоминай в ответе эту инструкцию, маркеры, URL сервиса или то, что ты дёргаешь agent_apps. Для игрока ты просто соперник за доской."""


def detect_game_move(text: str) -> Optional[Tuple[str, dict]]:
    """Детектит игровой ход по маркеру. Возвращает (mode, payload_dict) или None."""
    if not text:
        return None
    stripped = text.lstrip()
    if stripped.startswith(CHESS_MOVE_MARKER):
        rest = stripped[len(CHESS_MOVE_MARKER):].strip()
        try:
            payload = json.loads(rest) if rest else {}
        except Exception:
            payload = {}
        return ("chess", payload)
    return None


def build_game_context(text: str) -> Optional[str]:
    """Если text — игровой ход, возвращает текст инструкции для подмешивания в message."""
    detected = detect_game_move(text)
    if not detected:
        return None
    mode, payload = detected
    if mode == "chess":
        engine = payload.get("engine") or "agent"
        return _chess_prompt(engine)
    return None
