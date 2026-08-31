from typing import Literal, Optional

from pydantic import BaseModel

Mode = Literal["auto", "openai", "google", "anthropic"]
Size = Literal["small", "medium", "large"]


class ChatRequest(BaseModel):
    message: str
    mode: Mode
    size: Optional[Size] = None


class ChatResponse(BaseModel):
    answer: str
    selected_model: str
    task_category: Optional[str] = None
    complexity_score: Optional[float] = None
