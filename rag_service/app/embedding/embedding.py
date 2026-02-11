from typing import Dict, Any, cast
from huggingface_hub import InferenceClient
from chromadb import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function


@register_embedding_function
class KBEmbedding(EmbeddingFunction):
    def __init__(self, model, api_key):
        self.model = model
        self.embedding_client = InferenceClient(
            provider='hf-inference',
            api_key=api_key
        )
    
    def __call__(self, input: Documents) -> Embeddings:
        embeddings = self.embedding_client.feature_extraction(input, model=self.model)
        return cast(Embeddings, embeddings)
    
    @staticmethod
    def name() -> str:
        return "CK PM Knowledge Base Embedding Function"

    def get_config(self) -> Dict[str, Any]:
        return dict(model=self.model)

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "EmbeddingFunction":
        return KBEmbedding(config['model'])