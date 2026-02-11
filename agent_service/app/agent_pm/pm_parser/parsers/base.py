from abc import ABC, abstractmethod
from typing import List

from .models import Article


class BaseParser(ABC):
    """
    Базовый класс для всех парсеров.

    Контракт:
        name: str           — уникальное имя парсера (атрибут класса)
        arun() -> list[Article] — асинхронная точка входа
    """

    name: str

    @abstractmethod
    async def arun(self) -> List[Article]:
        ...
