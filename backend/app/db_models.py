"""conversations / messages 테이블 정의 (요청 스키마 그대로)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 로그인 기능이 없는 데모라, 모든 대화방을 이 고정 사용자 하나에 귀속시킨다.
# (진짜 로그인을 붙일 때 이 상수 자리만 실제 사용자 id로 바꾸면 됨)
DEMO_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 자동 메모리 추출이 "이 메시지 이후로 아직 요약 안 됨"을 판단하는 기준점.
    # FK는 일부러 안 건다 - 메시지 삭제/재정렬 시나리오가 없는 데모라 단순 포인터로 충분.
    last_extracted_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, default=None
    )

    # 관계(relationship)는 일부러 안 둔다 - 비동기 세션에서 지연 로딩(lazy load)
    # 하면 MissingGreenlet 에러가 나서, 메시지는 항상 Message를 직접 select한다.
    # 대화방 삭제 시 자식 메시지 정리는 FK의 ondelete="CASCADE"(DB 레벨)에 맡긴다.

    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", updated_at.desc()),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    complexity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 이번 턴에서 웹 검색 tool을 실제로 썼는지, 어떤 질의로 검색했는지.
    web_search_used: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True, default=False
    )
    web_search_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True
    )
    personal_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    imported_memory: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Attachment(Base):
    """채팅 첨부파일. 업로드 시점(전송 전)에 이미 저장+텍스트 추출까지 끝내고,
    message_id는 그 첨부를 참조한 메시지가 실제로 전송될 때만 채워진다
    (전송 전 이탈 시 message_id가 NULL인 채로 남는 게 "고아 첨부" - 정리 배치가 처리).
    """

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    # 텍스트 추출형(txt/md/csv/xlsx/docx/pptx/hwp)만 채워짐 - JSON 문자열로
    # [{"type":"text","text":...}, {"type":"image","path":...,"mime_type":...}]
    # 순서 보존 블록 목록을 담는다. PDF/IMG는 원본을 그대로 비전에 태우므로 NULL.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "pending" | "done" | "failed" - 프로젝트 컨벤션상 다른 status성 컬럼도
    # 네이티브 enum이 아니라 String로 두므로 그대로 따른다.
    extraction_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_attachments_chat_id", "chat_id"),
        Index("ix_attachments_message_id", "message_id"),
        # 고아 첨부(message_id IS NULL) 정리 배치가 created_at으로 스캔하므로.
        Index("ix_attachments_orphan_created", "message_id", "created_at"),
    )


class UserMemory(Base):
    """자동 메모리 추출로 쌓인 사실 한 줄 = row 하나.
    개인 지침/가져온 메모리(UserSettings)와 달리 여러 줄이라 별도 테이블로 둔다.
    """

    __tablename__ = "user_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_user_memory_user_id", "user_id"),)
