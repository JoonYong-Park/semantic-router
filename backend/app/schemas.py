from typing import Literal, Optional

from pydantic import BaseModel

Mode = Literal["gpt", "gemini", "claude", "auto"]


class ChatRequest(BaseModel):
    message: str
    mode: Mode


class ChatResponse(BaseModel):
    answer: str
    selected_model: str
    category: Optional[str] = None
