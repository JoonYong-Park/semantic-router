"""자동 메모리 추출 - 대화에서 사용자에 대한 사실을 뽑아 UserMemory로 쌓는다.

conversations.py/settings.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID
하나에 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지/개인 지침/가져오기
로직은 건드리지 않는다.

# TODO: 프로덕션에서는 오전 8시, 오후 1시처럼 정해진 시각에 자동 실행되어야 함
# (참고: 다른 AI 서비스들은 대화가 쌓일 때마다 주기적으로 메모리를 자동 합성한다고 함
#  https://lumichats.com/blog/chatgpt-memory-vs-claude-memory-vs-gemini-personal-intelligence-2026-which-ai-actually-knows-you)
# 지금은 Celery/Redis 없이 데모 범위라 프론트 버튼으로 수동 트리거만 구현.
"""

import asyncio
import json
import logging
import re
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import delete, select

from app import providers
from app.db import SessionLocal
from app.db_models import DEMO_USER_ID, Conversation, Message, UserMemory
from app.models_config import ModelSpec

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/memory", tags=["memory"])

# 9개 모델 카탈로그에 없는 모델이라 ModelSpec을 즉석에서 만들어 쓴다
# (요약 작업이라 품질보다 비용/속도가 중요해서 작은 모델을 고정으로 지정).
MEMORY_MODEL = ModelSpec(
    model_id="gpt-4o-mini",
    company="openai",
    size="small",
    display_name="GPT-4o mini",
)


async def _extract_memory_for_conversation(user_id: UUID, conv_id: UUID) -> None:
    """대화 하나에서 이전에 요약한 지점 이후의 새 메시지만 뽑아 메모리를 갱신한다.
    스트리밍 완료 후 백그라운드로 도는 _generate_and_save_title과 동일한 패턴
    (Celery/Redis 없이 asyncio.create_task로 충분한 규모라 이렇게 처리).
    """
    async with SessionLocal() as session:
        conv = await session.get(Conversation, conv_id)
        if conv is None:
            return

        query = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
        )
        if conv.last_extracted_message_id is not None:
            last_msg = await session.get(Message, conv.last_extracted_message_id)
            if last_msg is not None:
                query = query.where(Message.created_at > last_msg.created_at)

        result = await session.execute(query)
        new_messages = result.scalars().all()
        if not new_messages:
            return

        mem_result = await session.execute(
            select(UserMemory).where(UserMemory.user_id == user_id)
        )
        existing_list = [m.content for m in mem_result.scalars().all()]

        messages_text = "\n".join(f"{m.role}: {m.content}" for m in new_messages)
        prompt = (
            f"[기존 메모리]\n{existing_list if existing_list else '없음'}\n\n"
            f"[새 대화]\n{messages_text}\n\n"
            "아래 규칙으로 메모리를 업데이트해줘.\n"
            "- 새로운 사실 (직업, 소속, 선호도, 담당 업무 등) → 추가\n"
            "- 기존과 중복 → 유지\n"
            "- 기존과 모순 → 최신으로 교체\n"
            '- 일반 지식 질문 ("~가 뭐야?" 형태) → 무시\n'
            "- 1인칭(나, 내, 저) 및 2인칭(너, 당신) 표현을 쓰지 말고 "
            "'사용자'라고 표현해 (이 메모리는 나중에 다른 대화의 system 메시지로 "
            "다시 들어가므로, 대화체를 그대로 옮기면 안 됨)\n"
            "- 최종 메모리 전체를 JSON으로만 반환\n"
            "- 아래 반환 형식의 \"사실1\", \"사실2\"는 형식만 보여주는 자리표시자다. "
            "[새 대화]에 실제로 나오지 않은 내용이면 절대 포함하지 마라\n\n"
            '반환 형식: {"memories": ["사실1", "사실2"]}'
        )

        try:
            raw = await providers.call_model(MEMORY_MODEL, prompt)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            memories = data.get("memories", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("메모리 추출 실패 (conv_id=%s): %s", conv_id, exc)
            return

        # 기존 메모리 전체를 새로 뽑은 최종본으로 교체 (모델이 추가/유지/교체를 다 반영해서 줌)
        await session.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
        for content in memories:
            session.add(UserMemory(user_id=user_id, content=content))

        conv.last_extracted_message_id = new_messages[-1].id
        await session.commit()


async def _extract_all_sequentially(conv_ids: list[UUID]) -> None:
    # 채팅방마다 "기존 메모리 전체 삭제 → 새로 계산한 결과 삽입"을 하기 때문에,
    # 여러 채팅방을 asyncio.create_task로 동시에 돌리면 나중에 커밋되는 쪽이
    # 앞서 처리된 채팅방의 결과까지 지워버리는 경쟁 상태가 생긴다. 그래서 하나의
    # 백그라운드 태스크 안에서 순서대로(await) 처리해 서로 겹치지 않게 한다.
    for conv_id in conv_ids:
        await _extract_memory_for_conversation(DEMO_USER_ID, conv_id)


@router.post("/extract-all")
async def extract_all() -> dict:
    async with SessionLocal() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.user_id == DEMO_USER_ID)
        )
        conversations = result.scalars().all()

    asyncio.create_task(_extract_all_sequentially([conv.id for conv in conversations]))

    return {"status": "started", "count": len(conversations)}


