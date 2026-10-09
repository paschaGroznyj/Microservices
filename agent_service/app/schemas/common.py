from pydantic import BaseModel, Field
from typing import Optional

class StopMessagePayload(BaseModel):
    request_id: str
    user_id: Optional[int] = None
    # САМОЦИКЛ: если пришёл chat_id — стоп-кнопка гасит активную петлю (is_active=false).
    chat_id: Optional[str] = None