import os
import logging
from typing import List, Dict, Any
from pathlib import Path
import asyncio

from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from core.config import EMBEDDING_MODEL
from .pm_parser.botocore_client import OBSWorker

logger = logging.getLogger(__name__)

class VectorDBManager:
    def __init__(self, digest_path: str, vector_db_path: str):
        base_dir = Path(__file__).resolve().parent# final_agent
        self.digest_path = base_dir / digest_path
        print(self.digest_path)
        self.vector_db_path = base_dir / vector_db_path
        
        self.embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={'device': 'cpu'},
            encode_kwargs={'normalize_embeddings': True}
        )
        
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=512,
            chunk_overlap=50,
            separators=["\n\n", "\n## ", "\n### ", "\n", ".", "!?", "?", ";", " ", ""],
        )
        
        self.vectorstore = self._load_vectorstore()
        self.obs_loader = OBSWorker()
    
    def _load_vectorstore(self):
        if self.vector_db_path.exists():
            try:
                return FAISS.load_local(
                    str(self.vector_db_path), 
                    self.embeddings,
                    allow_dangerous_deserialization=True
                )
            except Exception as e:
                logger.warning(f"Ошибка загрузки FAISS: {e}")
        return None
    
    def _load_markdown_files(self, file_paths: str = None) -> List[Document]:
        documents = []
        for md_file in self.digest_path.rglob("*.md"):
            if file_paths and md_file.name not in file_paths:
                continue
            try:
                with open(md_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                title = md_file.stem
                lines = content.split('\n')
                for line in lines[:5]:
                    if line.startswith('# '):
                        title = line.replace('# ', '').strip()
                        break
                
                doc = Document(
                    page_content=content,
                    metadata={
                        'source': str(md_file),
                        'filename': md_file.name,
                        'title': title
                    }
                )
                documents.append(doc)
            except Exception as e:
                logger.error(f"Ошибка чтения {md_file}: {e}")
        return documents
    
    def rebuild_index(self, file_paths: str = None):
        asyncio.run(self.obs_loader.run_download())
        documents = self._load_markdown_files(file_paths)
        #print("ДОКУМЕНТЫ ЗАГРУЖЕНЫ", documents)
        if not documents:
            return 0
        
        texts = self.text_splitter.split_documents(documents)
        self.vectorstore = FAISS.from_documents(texts, self.embeddings)
        os.makedirs(self.vector_db_path, exist_ok=True)
        self.vectorstore.save_local(str(self.vector_db_path))
        asyncio.run(self.obs_loader.upload_vectorstore())
        return self.vectorstore.index.ntotal
    
    def delete_documents(self, file_paths: List[str]):
        self.rebuild_index(file_paths)
    
    def similarity_search(self, query: str, k: int = 5) -> List[Dict[str, Any]]:
        if not self.vectorstore:
            return []
        docs_with_scores = self.vectorstore.similarity_search_with_score(query, k=k)
        return [{'content': d.page_content, 'metadata': d.metadata, 'score': float(s)} 
                for d, s in docs_with_scores]
    
    def get_stats(self) -> Dict[str, Any]:
        if not self.vectorstore:
            return {'total_vectors': 0}
        return {'total_vectors': self.vectorstore.index.ntotal}
