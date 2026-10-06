from pydantic import BaseModel, Field
from typing import Optional

class StopMessagePayload(BaseModel):
    request_id: str
    user_id: Optional[int] = None