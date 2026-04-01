from typing import Dict, Any, cast
from huggingface_hub import AsyncInferenceClient
from chromadb import Documents, EmbeddingFunction, Embeddings
from langchain_gigachat.embeddings import GigaChatEmbeddings

from app.core.config import GC_AUTH_URL, GC_MODEL


class GigaChatEmbedding:
    def __init__(self):
        self.gc = GigaChatEmbeddings(credentials='none',
                           auth_url=GC_AUTH_URL, 
                           verify_ssl_certs=False,
                           model=GC_MODEL)
    
    async def embed(self, request):
        response = await self.gc.aembed_documents([request])

        return response[0]

class AsyncStandaloneEmbedding:
    def __init__(self, model, api_key):
        self.model = model
        self.embedding_client = AsyncInferenceClient(
            provider='hf-inference',
            api_key=api_key
        )
    
    async def embed(self, input):
        embedding = await self.embedding_client.feature_extraction(input, model=self.model)
        return embedding

class DummyKBEmbedding(EmbeddingFunction):
    def __init__(self, model):
        self.model=model
    
    def __call__(self, input: Documents) -> Embeddings:
        return cast(Embeddings, [])
    
    @staticmethod
    def name() -> str:
        return "CK PM Knowledge Base Embedding Function"

    def get_config(self) -> Dict[str, Any]:
        return dict(model=self.model)

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "EmbeddingFunction":
        return DummyKBEmbedding(config['model'])