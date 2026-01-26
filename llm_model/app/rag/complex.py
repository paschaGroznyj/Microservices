import os
import faiss
import numpy as np
from transformers import AutoTokenizer, AutoModel
import torch
import PyPDF2
from typing import List, Optional
from pathlib import Path
import json
from rank_bm25 import BM25Okapi
import re

def load_pdf_text(pdf_path: str, start_page: int, end_page: int) -> str:
    """
    Извлекает текст из PDF-документа между start_page и stop_page (включительно).

    Параметры:
        pdf_path (str): Путь к PDF-файлу.
        start_page (int): Номер начальной страницы (считается с 0).
        stop_page (int): Номер конечной страницы (включительно, считается с 0).

    Возвращает:
        str: Объединённый текст указанных страниц.
    """
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        total_pages = len(reader.pages)

        # Проверка корректности диапазона страниц
        if start_page < 0 or end_page >= total_pages or start_page > end_page:
            raise ValueError(f"Некорректный диапазон страниц. Доступно страниц: {total_pages}")

        text = ""
        for page_num in range(start_page, end_page + 1):
            page = reader.pages[page_num]
            text += page.extract_text() + "\n"

        return text

def chunk_text(text: str, chunk_size=800, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

class LaBSEEncoder:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/LaBSE")
        self.model = AutoModel.from_pretrained("sentence-transformers/LaBSE")
        self.model.eval()
        self.device = torch.device("cpu")
        self.model.to(self.device)
        self._initialized = True

    def encode(self, texts):
        tokens = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            return_tensors="pt"
        )
        tokens = {k: v.to(self.device) for k, v in tokens.items()}
        with torch.no_grad():
            output = self.model(**tokens).last_hidden_state.mean(dim=1)
        # нормализуем эмбеддинги
        embeddings = torch.nn.functional.normalize(output, p=2, dim=1)
        return embeddings.cpu().numpy()



class FaissIndex:
    def __init__(self, dim: int = None, index_path: str = None, meta_path: str = None):
        """
        Инициализация:
        - либо указываем dim → создаём новый индекс
        - либо указываем пути → загружаем существующий
        """
        if index_path and meta_path:
            self.load(index_path, meta_path)
        elif dim is not None:
            self.index = faiss.IndexFlatIP(dim)
            self.texts = []
        else:
            raise ValueError("Укажите либо dim, либо пути для загрузки")

    def add(self, embeddings, texts):
        self.index.add(embeddings.astype("float32"))
        self.texts.extend(texts)

    def search(self, query_emb, k=5):
        scores, idxs = self.index.search(query_emb.astype("float32"), k)
        return [self.texts[i] for i in idxs[0] if i < len(self.texts)]

    def save(self, index_path: str, meta_path: str):
        """Сохраняет индекс и метаданные"""
        faiss.write_index(self.index, index_path)
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.texts, f, ensure_ascii=False, indent=2)

    def load(self, index_path: str, meta_path: str):
        """Загружает индекс и метаданные"""
        if not (os.path.exists(index_path) and os.path.exists(meta_path)):
            raise FileNotFoundError(f"Индекс или метаданные не найдены: {index_path}, {meta_path}")
        self.index = faiss.read_index(index_path)
        with open(meta_path, 'r', encoding='utf-8') as f:
            self.texts = json.load(f)

class MessageRAG:
    def __init__(self, chat_id: int, data_dir: str = "./data"):
        self.chat_id = chat_id
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.encoder = LaBSEEncoder()
        self.index = None
        self.bm25 = None
        self.texts = []  # будем хранить явно для BM25

        # Пути к файлам
        self.index_path = self.data_dir / f"faiss_{chat_id}.index"
        self.meta_path = self.data_dir / f"meta_{chat_id}.json"

    def _tokenize(self, text: str) -> List[str]:
        """Простая токенизация для BM25"""
        return re.findall(r'\w+', text.lower())

    def create_index(self, messages: List[str]):
        # Векторный индекс
        embeddings = self.encoder.encode(messages)
        self.index = FaissIndex(dim=embeddings.shape[1])
        self.index.add(embeddings, messages)

        # BM25 индекс
        self.texts = messages
        tokenized_corpus = [self._tokenize(doc) for doc in messages]
        self.bm25 = BM25Okapi(tokenized_corpus)

    def save_index(self):
        """Сохраняет индекс на диск"""
        if self.index is None:
            raise ValueError("Индекс не создан")
        self.index.save(str(self.index_path), str(self.meta_path))
        print(f"Индекс сохранён: {self.index_path}")

    def load_index(self):
        try:
            self.index = FaissIndex(index_path=str(self.index_path), meta_path=str(self.meta_path))
            # Перестраиваем BM25 из загруженных текстов
            self.texts = self.index.texts
            tokenized_corpus = [self._tokenize(doc) for doc in self.texts]
            self.bm25 = BM25Okapi(tokenized_corpus)
            print(f"Индекс загружен: {self.index_path}")
        except FileNotFoundError:
            print("Индекс не найден")
            self.index = None
            self.bm25 = None

    def retrieve(self, query: str, k=5):
        if self.index is None:
            raise ValueError("Индекс не загружен и не создан")
        query_emb = self.encoder.encode([query])
        return "\n".join(self.index.search(query_emb, k=k))

    def hybrid_search(self, query: str, k: int = 5, alpha: float = 0.5) -> str:
        """
        Гибридный поиск: alpha * вектор + (1-alpha) * BM25

        alpha=1.0 → только векторный
        alpha=0.0 → только BM25
        """
        if self.index is None or self.bm25 is None:
            raise ValueError("Индекс не загружен")

        # 1. Векторный поиск
        query_emb = self.encoder.encode([query])
        vec_scores, vec_indices = self.index.index.search(
            query_emb.astype("float32"), k * 2  # берём больше кандидатов
        )
        vec_scores = vec_scores[0]
        vec_indices = vec_indices[0]

        # 2. BM25 поиск
        tokenized_query = self._tokenize(query)
        bm25_scores = self.bm25.get_scores(tokenized_query)

        # 3. Нормализуем скоры
        def normalize(scores):
            if len(scores) == 0 or max(scores) == min(scores):
                return [1.0] * len(scores)
            return [(s - min(scores)) / (max(scores) - min(scores)) for s in scores]

        norm_vec = [0.0] * len(self.texts)
        for score, idx in zip(vec_scores, vec_indices):
            if idx < len(norm_vec):
                norm_vec[idx] = score

        norm_vec = normalize(norm_vec)
        norm_bm25 = normalize(bm25_scores.tolist())

        # 4. Гибридный скор
        hybrid_scores = [
            alpha * v + (1 - alpha) * b
            for v, b in zip(norm_vec, norm_bm25)
        ]

        # 5. Топ-k результатов
        top_indices = sorted(
            range(len(hybrid_scores)),
            key=lambda i: hybrid_scores[i],
            reverse=True
        )[:k]

        results = [self.texts[i] for i in top_indices if i < len(self.texts)]
        return "\n".join(results)


class RAGManager:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self._cache = {}  # chat_id → MessageRAG

    def get_rag(self, chat_id: int) -> Optional[MessageRAG]:
        if chat_id not in self._cache:
            rag = MessageRAG(chat_id=chat_id, data_dir=str(self.data_dir))
            try:
                rag.load_index()
                self._cache[chat_id] = rag
            except Exception as e:
                print(f"Не удалось загрузить RAG для chat_id={chat_id}: {e}")
                return None
        return self._cache[chat_id]
class SimpleRAG:
    def __init__(self):
        self.encoder = LaBSEEncoder()
        self.index = None

    def create_index(self, docs_path: str):
        text = load_pdf_text(docs_path, start_page=4, end_page=40)
        chunks = chunk_text(text)
        embeddings = self.encoder.encode(chunks)
        self.index = FaissIndex(embeddings.shape[1])
        self.index.add(embeddings, chunks)

    def retrieve(self, query: str, k=5):
        query_emb = self.encoder.encode([query])
        return ".".join(self.index.search(query_emb, k=k))
