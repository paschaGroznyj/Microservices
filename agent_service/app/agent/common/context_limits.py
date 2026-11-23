TOKEN_SOFT_LIMIT = 150_000


def context_payload(tokens_usage):
    """Контекст последнего шага для UI: тот же формат, что context в /web/memory/{chat}/stats.

    Едет в финальном payload ответа (Kafka -> гейтвей -> фронт), чтобы строка памяти
    обновлялась сразу, без опроса БД. None, если context_tokens нет (старый формат)."""
    if not isinstance(tokens_usage, dict):
        return None
    try:
        tokens = int(tokens_usage.get("context_tokens") or 0) or None
    except (TypeError, ValueError):
        tokens = None
    if tokens is None:
        return None
    cache_read = tokens_usage.get("context_cache_read_tokens")
    try:
        cache_pct = round(int(cache_read) * 100 / tokens) if cache_read is not None else None
    except (TypeError, ValueError):
        cache_read, cache_pct = None, None
    return {
        "tokens": tokens,
        "max_tokens": TOKEN_SOFT_LIMIT,
        "limit_kind": "compaction_threshold",
        "model_window_tokens": None,
        "cache_pct": cache_pct,
        "context_breakdown": tokens_usage.get("context_breakdown"),
        "context_cache_read_tokens": cache_read,
    }
