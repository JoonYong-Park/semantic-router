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
    "DATABASE_URL", "postgresql+asyncpg://asap:asap@postgres:5432/asap"
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

    # 로그인이 붙기 전까지 쓰는 고정 사용자를 시드한다(이미 있으면 건너뜀). 기존
    # 데이터의 user_id가 이 id라서 users에 이 행이 있어야 FK가 성립한다.
    from sqlalchemy.dialects.postgresql import insert

    async with SessionLocal() as session:
        await session.execute(
            insert(db_models.User)
            .values(
                id=db_models.DEMO_USER_ID,
                email=db_models.DEMO_USER_EMAIL,
                name=db_models.DEMO_USER_NAME,
            )
            .on_conflict_do_nothing()
        )
        await session.commit()


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
