import os
import faiss
import numpy as np
from transformers import AutoTokenizer, AutoModel
import torch
import PyPDF2


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


# def load_docx_text(path: str):
#     doc = Document(path)
#     return "\n".join(
#         p.text for p in doc.paragraphs if p.text.strip()
#     )

def chunk_text(text: str, chunk_size=800, overlap=200):
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

class LaBSEEncoder:
    def __init__(self):
        self.tokenizer = AutoTokenizer.from_pretrained("sentence-transformers/LaBSE")
        self.model = AutoModel.from_pretrained("sentence-transformers/LaBSE")
        self.model.eval()  # только инференс
        # CPU-only
        self.device = torch.device("cpu")
        self.model.to(self.device)

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
    def __init__(self, dim: int):
        self.index = faiss.IndexFlatIP(dim)
        self.texts = []

    def add(self, embeddings, texts):
        self.index.add(embeddings.astype("float32"))
        self.texts.extend(texts)

    def search(self, query_emb, k=5):
        scores, idxs = self.index.search(query_emb, k)
        return [
            self.texts[i] for i in idxs[0]
        ]


class SimpleRAG:
    def __init__(self, docs_path: str):
        self.encoder = LaBSEEncoder()

        # загрузка документов
        text = load_pdf_text(docs_path, start_page=4, end_page=20)
        chunks = chunk_text(text)
        print("Векторизация")
        embeddings = self.encoder.encode(chunks)
        self.index = FaissIndex(embeddings.shape[1])
        self.index.add(embeddings, chunks)

    def retrieve(self, query: str, k=5):
        query_emb = self.encoder.encode([query])
        return ".".join(self.index.search(query_emb, k=k))

