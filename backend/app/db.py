"""SQLAlchemy 비동기 엔진/세션 (대화방·메시지 영속화).

데모 단계라 Alembic 마이그레이션 대신 시작 시 create_all()로 테이블을 만든다.
스키마를 바꿔야 하면 이 방식으로는 기존 테이블이 자동 갱신되지 않으니,
운영으로 넘어갈 때는 Alembic으로 바꿔야 한다.
"""

import os
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://npia:npia@postgres:5432/npia"
)

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    # 여기서 import해야 db_models가 Base에 테이블을 등록한 뒤 create_all이 돈다.
    from app import db_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
