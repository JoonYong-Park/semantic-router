"""자동 메모리 수동 추출 트리거(/memory/extract-all). 실행 로직은 extract.py."""

import asyncio
import logging

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.models import DEMO_USER_ID, Conversation
from app.memory.extract import (
    _extract_all_sequentially,
)

logger = logging.getLogger("uvicorn.error")


router = APIRouter(prefix="/memory", tags=["memory"])


@router.post("/extract-all")
async def extract_all() -> dict:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.user_id == DEMO_USER_ID)
        )
        conversations = result.scalars().all()

    asyncio.create_task(_extract_all_sequentially([conv.id for conv in conversations]))

    return {"status": "started", "count": len(conversations)}
