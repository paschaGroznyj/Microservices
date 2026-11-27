"""
Реакции на сообщения общего чата.

Хранение: group_messages.reactions JSONB = {"<user_id>": ["<emoji>", ...]}.
До трёх уникальных реакций пользователя на сообщение; пустой список снимает.
Старые строковые значения читаются как список из одного элемента.

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


def normalize_emojis(value) -> list[str]:
    """Canonical list; accept legacy emoji/empty string, reject invalid input.

    Deduplicate before enforcing the limit: the limit counts distinct reactions.
    """
    if isinstance(value, str):
        value = [value] if value else []
    if not isinstance(value, list):
        raise ValueError("emojis must be a list")
    result = []
    for emoji in value:
        if not isinstance(emoji, str) or emoji not in _ALLOWED:
            raise ValueError("unsupported reaction emoji")
        if emoji not in result:
            result.append(emoji)
    if len(result) > 3:
        raise ValueError("at most 3 reactions per user per message")
    return result


def reaction_emojis(op: dict) -> list[str]:
    """Explicit emojis (including []) takes precedence over legacy emoji."""
    if "emojis" in op:
        if not isinstance(op["emojis"], list):
            raise ValueError("emojis must be a list")
        return normalize_emojis(op["emojis"])
    return normalize_emojis(op.get("emoji", ""))


def load_map(raw) -> dict:
    """Read JSONB (string or dict), including legacy single-emoji entries.

    Corrupt stored entries are ignored, never truncated into a valid reaction.
    """
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    result = {}
    for uid, value in raw.items():
        try:
            emojis = normalize_emojis(value)
        except ValueError:
            continue
        if emojis:
            result[str(uid)] = emojis
    return result


def summarize(raw) -> list:
    """Count unique users per emoji, sorted by count then palette order."""
    groups: dict = {}
    for uid, emojis in load_map(raw).items():
        for emoji in emojis:
            groups.setdefault(emoji, []).append(int(uid) if uid.isdigit() else uid)
    out = [{"emoji": e, "count": len(u), "user_ids": sorted(u, key=str)}
           for e, u in groups.items()]
    out.sort(key=lambda r: (-r["count"], _ORDER.get(r["emoji"], 999)))
    return out
