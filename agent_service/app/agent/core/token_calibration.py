"""Оценка токенов под активную модель с самокалибровкой по факту провайдера.

У каждой модели свой токенизатор. Базовый chars_per_token берётся из
models_config.yaml (замер на cloud.ru 19.11.2026). После каждого запроса
отношение «реальные input-токены / наша оценка» сглаживается EMA по тегу
модели, и следующий trim_messages считает уже с поправкой:

    effective_cpt = base_cpt / ratio_ema

Состояние живёт в памяти процесса и, если доступен Redis, в хеше
agent:tok_calib (переживает рестарт и общий для реплик). Redis fail-open.
"""
from __future__ import annotations

import json
import logging
from functools import partial
from typing import Any, Callable, Iterable

from langchain_core.messages.utils import count_tokens_approximately

logger = logging.getLogger(__name__)

REDIS_KEY = "agent:tok_calib"
ALPHA = 0.2              # вес нового замера в EMA
SAMPLE_MIN, SAMPLE_MAX = 0.4, 2.5   # замер вне диапазона считаем выбросом
RATIO_MIN, RATIO_MAX = 0.6, 1.8     # предел поправки к конфигу
MIN_ESTIMATE = 500       # на коротких промптах доля служебных токенов искажает отношение


class TokenCalibrator:
    def __init__(self) -> None:
        self._state: dict[str, dict[str, float]] = {}   # tag -> {"ratio": float, "n": int}
        self._loaded = False

    # ── чтение ─────────────────────────────────────────────────────────
    def ratio(self, tag: str) -> float:
        st = self._state.get(tag)
        return st["ratio"] if st else 1.0

    def samples(self, tag: str) -> int:
        st = self._state.get(tag)
        return int(st["n"]) if st else 0

    def chars_per_token(self, tag: str, base_cpt: float) -> float:
        return base_cpt / self.ratio(tag)

    def counter(self, tag: str, base_cpt: float) -> Callable[[Iterable[Any]], int]:
        """token_counter для trim_messages под активную модель."""
        return partial(count_tokens_approximately, chars_per_token=self.chars_per_token(tag, base_cpt))

    @staticmethod
    def estimate(messages: Iterable[Any], base_cpt: float, extra_chars: int = 0) -> int:
        """Оценка промпта по КОНФИГУ (без поправки): от неё считается отношение."""
        return count_tokens_approximately(messages, chars_per_token=base_cpt) + int(extra_chars / base_cpt)

    @staticmethod
    def calibratable(messages: Iterable[Any]) -> bool:
        """Только текстовые промпты: картинки count_tokens_approximately меряет repr'ом base64."""
        return all(isinstance(getattr(m, "content", ""), str) for m in messages)

    # ── запись ─────────────────────────────────────────────────────────
    def observe(self, tag: str, estimated: int, actual: int) -> float | None:
        """Учитывает замер. Возвращает новое ratio или None, если замер отброшен."""
        if not tag or not actual or estimated < MIN_ESTIMATE:
            return None
        sample = actual / estimated
        if not (SAMPLE_MIN <= sample <= SAMPLE_MAX):
            logger.info(f"[tokcal] {tag}: выброс est={estimated} real={actual} x{sample:.2f}, пропуск")
            return None
        st = self._state.get(tag)
        if st is None:
            ratio, n = sample, 1                  # первый замер берём как есть
        else:
            ratio, n = (1 - ALPHA) * st["ratio"] + ALPHA * sample, st["n"] + 1
        ratio = min(RATIO_MAX, max(RATIO_MIN, ratio))
        self._state[tag] = {"ratio": ratio, "n": n}
        return ratio

    # ── Redis ──────────────────────────────────────────────────────────
    async def load(self, redis) -> None:
        if self._loaded or redis is None:
            return
        self._loaded = True
        try:
            raw = await redis.hgetall(REDIS_KEY)
        except Exception as e:
            logger.debug(f"[tokcal] redis load: {e}")
            return
        for tag, val in (raw or {}).items():
            try:
                st = json.loads(val)
                if tag not in self._state:
                    self._state[tag] = {"ratio": float(st["ratio"]), "n": int(st["n"])}
            except Exception:
                continue

    async def save(self, redis, tag: str) -> None:
        if redis is None or tag not in self._state:
            return
        try:
            await redis.hset(REDIS_KEY, tag, json.dumps(self._state[tag]))
        except Exception as e:
            logger.debug(f"[tokcal] redis save: {e}")


CALIBRATOR = TokenCalibrator()
