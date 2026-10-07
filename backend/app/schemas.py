from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    project_id: Optional[UUID] = None
    pinned: bool = False
    sort_order: int = 0


class ConversationProjectUpdate(BaseModel):
    project_id: Optional[UUID] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    pinned: Optional[bool] = None
    sort_order: Optional[int] = None


# --- 첨부파일 ---


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    extraction_status: str
    source: Literal["upload", "generated"] = "upload"
    created_at: datetime


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
    # 이 메시지에 첨부된 파일 목록 - 전송 후에도 채팅 화면에 "무엇을 첨부했는지"
    # 계속 보여주기 위해 필요. Message ORM에는 관계(relationship)를 안 걸어뒀으므로
    # (다른 모델들과 동일한 컨벤션) get_messages()에서 수동으로 채워서 넘긴다.
    attachments: list[AttachmentOut] = []


class MessageCreate(BaseModel):
    content: str
    mode: Mode
    size: Optional[Size] = None
    attachment_ids: list[UUID] = []


# --- 사용자 ---


class UserUpdate(BaseModel):
    # 이메일은 변경 불가라 일부러 필드를 두지 않는다(보내도 무시됨).
    name: str = Field(min_length=1, max_length=100)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    role: str
    avatar_updated_at: datetime | None = None


# --- 개인 지침(personal instruction) 설정용 ---


class SettingsOut(BaseModel):
    personal_instruction: Optional[str] = None
    imported_memory: Optional[str] = None


class SettingsUpdate(BaseModel):
    personal_instruction: Optional[str] = None


class ImportMemoryCreate(BaseModel):
    text: str


class ImportedMemoryOut(BaseModel):
    imported_memory: Optional[str] = None


# --- 토큰 사용량 ---

UsagePeriod = Literal["today", "week", "month", "year"]
ProjectColor = Literal[
    "default", "red", "orange", "yellow", "green", "blue", "purple", "pink"
]


class UsageQuotaOut(BaseModel):
    limit: int
    used: int
    remaining: int
    percent: float
    cycle_start: str
    cycle_end: str
    days_left: int


class UsageChartPoint(BaseModel):
    label: str
    full_label: str
    input: int
    output: int


class UsageByModelOut(BaseModel):
    model: str
    calls: int
    input: int
    output: int
    total: int


class UsageSummaryOut(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class UsageStatsOut(BaseModel):
    period: UsagePeriod
    range_label: str
    summary: UsageSummaryOut
    chart: list[UsageChartPoint]
    by_model: list[UsageByModelOut]


# --- 대화 검색 ---


class SearchResultOut(BaseModel):
    conversation_id: UUID
    title: Optional[str] = None
    snippet: str
    matched_role: str


# --- 프로젝트 폴더 ---


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    instruction: Optional[str] = None
    max_capacity_bytes: int
    pinned: bool = False
    sort_order: int = 0
    color: ProjectColor = "default"
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str
    instruction: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    instruction: Optional[str] = None
    pinned: Optional[bool] = None
    sort_order: Optional[int] = None
    color: Optional[ProjectColor] = None


class ProjectFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    extraction_status: str
    created_at: datetime


class ProjectFilesOut(BaseModel):
    files: list[ProjectFileOut]
    used_bytes: int
    max_bytes: int
    percent: float


# --- 메모리 조회(전역: 카테고리 → 토픽, 프로젝트: 토픽) ---

MemoryCategory = Literal["나", "주제", "영역", "사람"]


class MemoryTopicOut(BaseModel):
    id: UUID
    name: str
    summary: str
    details: list[str]
    updated_at: datetime


class MemoryCategoryGroupOut(BaseModel):
    category: MemoryCategory
    topics: list[MemoryTopicOut]


class ProjectMemoryGroupOut(BaseModel):
    project_id: UUID
    project_name: str
    topics: list[MemoryTopicOut]


class MemoryOverviewOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # "global"은 파이썬 예약어라 속성명은 global_로 두고, JSON 필드명만 "global"로 맞춘다.
    global_: list[MemoryCategoryGroupOut] = Field(alias="global")
    projects: list[ProjectMemoryGroupOut]


class MemoryTopicUpdate(BaseModel):
    """메모리 화면에서 토픽 상세를 직접 편집할 때 쓰는 요청 바디.
    name/category는 자동 추출이 분류/명명을 책임지는 영역이라 편집 대상에서
    제외하고, 사용자가 직접 다듬을 수 있는 summary/details만 받는다."""

    summary: str
    details: list[str]
