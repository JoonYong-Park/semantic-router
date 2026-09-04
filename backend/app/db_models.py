"""conversations / messages 테이블 정의 (요청 스키마 그대로)."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, func
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_messages_conv_created", "conversation_id", "created_at"),
    )
