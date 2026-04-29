import os
from typing import List

from .core.agent_provider import get_embedd_model
import random


gigachat_emb = get_embedd_model()

class EmbeddingProcessor:
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "Embeddings-2")

        self.client = gigachat_emb

    def _normalize_text(self, text: str) -> str:
        return " ".join((text or "").split())

    def build_article_text(self, title: str, abstract: str, category: str | None = None) -> str:
        parts = []

        title = self._normalize_text(title)
        abstract = self._normalize_text(abstract)
        category = self._normalize_text(category or "")

        if title:
            parts.append(f"Title: {title}")
        if category:
            parts.append(f"Category: {category}")
        if abstract:
            parts.append(f"Abstract: {abstract}")

        return "\n".join(parts)

    # def get_embedding(self, text: str) -> List[float]:
    #     text = self._normalize_text(text)
    #     if not text:
    #         raise ValueError("Text for embedding is empty")
        
    #     response = self.client._client.aembeddings(texts=text)
    #     return 
        
    # import random

    def get_embedding(self, text: str):
        random.seed(abs(hash(text)) % (10**8))
        return [random.random() for _ in range(1024)]