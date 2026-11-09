"""
Реакции на сообщения общего чата.

Хранение: group_messages.reactions JSONB = {"<user_id>": "<emoji>"}.
Одна реакция на пользователя на сообщение (как в Telegram без Premium):
выбор другой заменяет прежнюю, пустая строка снимает.

Набор эмодзи фиксирован. Тот же список лежит в
api_gateway/static/js/groupInteraction.js (REACTIONS), их надо менять вместе.
"""
import json

# 8×8 = 64, первые 7 — быстрая панель в контекстном меню
REACTION_EMOJI = (
    "❤️", "🔥", "😁", "👎", "👍", "💯", "😢", "😨",
    "👌", "😱", "🤯", "🤩", "🤡", "🤔", "🥺", "🤝",
    "🥰", "👏", "🤬", "🎉", "🤮", "💩", "🙏", "🕊️",
    "🥱", "🥴", "😍", "🐳", "❤️‍🔥", "🌚", "🌭", "🤣",
    "⚡", "🍌", "🏆", "💔", "🤨", "😐", "🍓", "🍾",
    "💋", "🖕", "😈", "😴", "😭", "🤓", "👻", "👨‍💻",
    "👀", "🎃", "🙈", "😇", "✍️", "🤗", "🫡", "🎅",
    "🎄", "☃️", "💅", "🤪", "🗿", "🆒", "💘", "🦄",
)
_ALLOWED = frozenset(REACTION_EMOJI)
_ORDER = {e: i for i, e in enumerate(REACTION_EMOJI)}


def normalize_emoji(v) -> str | None:
    """'' (снять) или эмодзи из набора; всё остальное None."""
    if v is None or v == "":
        return ""
    return v if isinstance(v, str) and v in _ALLOWED else None


def load_map(raw) -> dict:
    """JSONB из asyncpg приходит строкой (кодек не настроен) или dict."""
    if raw is None:
        return {}
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str) and v in _ALLOWED}


def summarize(raw) -> list:
    """{"uid": emoji} -> [{"emoji", "count", "user_ids"}], больше голосов выше,
    при равенстве порядок набора."""
    groups: dict = {}
    for uid, emoji in load_map(raw).items():
        groups.setdefault(emoji, []).append(int(uid) if uid.isdigit() else uid)
    out = [{"emoji": e, "count": len(u), "user_ids": sorted(u, key=str)}
           for e, u in groups.items()]
    out.sort(key=lambda r: (-r["count"], _ORDER.get(r["emoji"], 999)))
    return out
