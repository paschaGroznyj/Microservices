from pydantic import BaseModel
from typing import Optional

class PutDocument(BaseModel):
    id: str
    name: str
    text: str

class GetChunk(BaseModel):
    text: str
    top_n: int

class DelChunk(BaseModel):
    db_id: Optional[int] = None
    original_id: Optional[str] = None
