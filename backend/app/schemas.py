from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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


# --- 채팅방(대화방) + 스트리밍용, 위 /chat 스키마와는 별개 ---


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: Optional[str] = None
    updated_at: datetime


class MessageOut(BaseModel):
    # model_used가 Pydantic의 "model_" 예약 네임스페이스와 겹쳐서 나는 경고를 끈다.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: UUID
    role: str
    content: str
    model_used: Optional[str] = None
    task_category: Optional[str] = None
    complexity_score: Optional[float] = None
    created_at: datetime


class MessageCreate(BaseModel):
    content: str
    mode: Mode
    size: Optional[Size] = None
