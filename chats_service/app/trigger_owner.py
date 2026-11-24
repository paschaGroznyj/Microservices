"""Автор автотриггера: от него зависят права агента (админ-категории).

В *_triggers.added_by_user_id + added_by_source (web | tg | max): id автора
живут в разных пространствах (наш user_id из gateway, tg id, max id).
"""
import logging

log = logging.getLogger("trigger_owner")

# Правка этих полей меняет, ЧТО агент сделает с правами автора.
OWNER_CONTENT_FIELDS = frozenset({"prompt", "preset_key"})
OWNER_SOURCES = frozenset({"web", "tg", "max"})


def owner_fields_for_patch(fields: dict) -> dict:
    """Промпт меняют без автора -> автор неизвестен, сбрасываем (не админ).
    Автор передан -> пишем его вместе с источником. Время/вкл-выкл автора не трогают."""
    fields = dict(fields)
    if "added_by_user_id" in fields:
        src = fields.get("added_by_source")
        fields["added_by_source"] = src if src in OWNER_SOURCES else None
    elif OWNER_CONTENT_FIELDS & fields.keys():
        fields["added_by_user_id"] = None
        fields["added_by_source"] = None
    else:
        fields.pop("added_by_source", None)
    return fields


async def resolve_trigger_owner(db, trigger_id, deliver_chat_id) -> dict | None:
    """Автор триггера из БД (не из payload). Триггер должен быть из того же чата,
    куда уходит ответ. Возвращает {trigger_id, user_id, source, prompt} или None."""
    try:
        row = await db.get_trigger(int(trigger_id))
    except Exception as e:
        log.warning(f"trigger owner lookup fail tid={trigger_id}: {e}")
        return None
    if not row:
        log.warning(f"trigger owner: tid={trigger_id} not found")
        return None
    if str(row.get("chat_id")) != str(deliver_chat_id):
        log.warning(f"trigger owner: tid={trigger_id} chat={row.get('chat_id')} != {deliver_chat_id}")
        return None
    return {
        "trigger_id": row.get("id"),
        "user_id": row.get("added_by_user_id"),
        "source": row.get("added_by_source"),
        "prompt": row.get("prompt"),
    }
