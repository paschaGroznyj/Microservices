"""
Парсер ответа агента для мобильного клиента (gateway_target == "mobile").

Агент в mobile-режиме отвечает СТРОГИМ JSON вида:
    {"v": 1, "speech": "...", "actions": [{"type": "...", "params": {...}}]}

Задача парсера:
  * вытащить JSON, даже если модель обернула его в ```json ... ``` или
    добавила текст до/после;
  * провалидировать по белому списку типов действий (ACTION_SCHEMA);
  * быть FALLBACK-SAFE: при ЛЮБОЙ ошибке вернуть {"speech": raw, "actions": []},
    чтобы голос ассистента никогда не потерялся из-за кривого JSON.

Белый список действий СИНХРОНИЗИРОВАН с mobile_settings.ACTION_SCHEMA —
оба модуля читают один источник правды.
"""
import json
import logging
import re

try:
    # один источник правды для списка действий (промт + валидатор)
    from agent.mobile.mobile_settings import ACTION_SCHEMA
except Exception:  # на случай изолированного импорта/тестов
    ACTION_SCHEMA = {
        "flashlight": {"state"},
        "media": {"cmd"},
        "volume": {"cmd", "value"},
        "app_open": {"package"},
    }

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# ```json ... ``` или ``` ... ``` — достаём внутренность
_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_blob(raw: str) -> str | None:
    """Пытаемся выдрать JSON-объект из произвольного текста.

    Порядок: fenced-блок → первая сбалансированная {...} → None.
    """
    if not raw:
        return None
    # 1) fenced ```json ... ```
    m = _FENCE_RE.search(raw)
    if m:
        return m.group(1).strip()
    # 2) первая сбалансированная фигурная скобка
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start:i + 1]
    return None


def _validate_actions(raw_actions) -> list[dict]:
    """Оставляем только действия из белого списка с валидными params."""
    if not isinstance(raw_actions, list):
        return []
    clean: list[dict] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        a_type = item.get("type")
        if a_type not in ACTION_SCHEMA:
            logger.warning("[mobile] неизвестный action.type=%r — отброшен", a_type)
            continue
        params = item.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        allowed = ACTION_SCHEMA[a_type]
        # оставляем только разрешённые ключи params (мягкая фильтрация)
        filtered = {k: v for k, v in params.items() if k in allowed}
        clean.append({"type": a_type, "params": filtered})
    return clean


def parse_mobile_response(raw: str) -> dict:
    """Главная функция. Всегда возвращает {"speech": str, "actions": list}.

    FALLBACK-SAFE: любая ошибка → весь текст уходит в speech, actions=[].
    """
    if not isinstance(raw, str):
        raw = str(raw or "")

    blob = _extract_json_blob(raw)
    if not blob:
        return {"speech": raw.strip(), "actions": []}

    try:
        data = json.loads(blob)
    except Exception as e:
        logger.warning("[mobile] JSON не распарсился (%s) — fallback на plain text", e)
        return {"speech": raw.strip(), "actions": []}

    if not isinstance(data, dict):
        return {"speech": raw.strip(), "actions": []}

    speech = data.get("speech")
    if not isinstance(speech, str) or not speech.strip():
        # нет валидного speech — не рискуем, озвучиваем исходный текст целиком
        return {"speech": raw.strip(), "actions": []}

    actions = _validate_actions(data.get("actions"))
    return {"speech": speech.strip(), "actions": actions}
