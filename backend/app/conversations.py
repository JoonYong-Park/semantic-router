"""채팅방(대화방) CRUD + SSE 스트리밍 메시지 전송.

기존 /chat(app/main.py)과 완전히 별개 엔드포인트다. 분류/복잡도/모델선택
로직(classifier.py, complexity.py, router.py, models_config.py)은 그대로
가져다 쓰기만 하고 손대지 않는다.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import classifier, complexity, providers
from app.db import SessionLocal, get_session
from app.db_models import DEMO_USER_ID, Conversation, Message
from app.models_config import DEFAULT_MANUAL_SIZE, get_model
from app.router import select_model
from app.schemas import ConversationOut, MessageCreate, MessageOut
from app.token_counter import count_tokens

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_CONTEXT_TOKENS = 4000
# 제목 생성은 품질보다 속도/비용이 중요해서 가장 작은 모델을 쓴다.
TITLE_MODEL = get_model("openai", "small")


@router.post("", response_model=ConversationOut)
async def create_conversation(
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    conv = Conversation(user_id=DEMO_USER_ID)
    session.add(conv)
    await session.commit()
    await session.refresh(conv)
    return conv


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    session: AsyncSession = Depends(get_session),
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == DEMO_USER_ID)
        .order_by(Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: UUID, session: AsyncSession = Depends(get_session)
) -> None:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    # ORM cascade 대신 단일 DELETE로 처리 - 자식 메시지 정리는 FK의
    # ondelete="CASCADE"(DB 레벨)가 담당한다.
    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await session.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[Message]:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


async def _build_history(
    session: AsyncSession, conversation_id: UUID, new_query: str
) -> list[dict[str, str]]:
    """토큰 예산(MAX_CONTEXT_TOKENS) 안에서 최근 메시지부터 거꾸로 채우는
    슬라이딩 윈도우. 새 사용자 질문은 아직 DB에 없으므로 마지막에 직접 붙인다.
    """
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
    )
    history = result.scalars().all()

    selected: list[Message] = []
    total = 0
    for msg in history:
        tokens = msg.token_count or count_tokens(msg.content)
        if total + tokens > MAX_CONTEXT_TOKENS:
            break
        selected.append(msg)
        total += tokens

    selected.reverse()

    return [
        *[{"role": m.role, "content": m.content} for m in selected],
        {"role": "user", "content": new_query},
    ]


async def _generate_and_save_title(conversation_id: UUID, first_message: str) -> None:
    """스트리밍 응답이 끝난 뒤 백그라운드에서 실행 - 클라이언트를 기다리게 하지 않는다.
    (요청하신 대로 Celery/Redis 없이 asyncio.create_task로 충분한 규모라 이렇게 처리)
    """
    try:
        prompt = (
            "아래 질문을 10자 이내의 짧은 제목으로 만들어줘. "
            f"설명 없이 제목만 답해.\n\n{first_message}"
        )
        title = await providers.call_model(TITLE_MODEL, prompt)
        title = title.strip().strip('"').strip("'")[:200]
    except Exception as exc:  # noqa: BLE001
        logger.error("제목 생성 실패 (conversation_id=%s): %s", conversation_id, exc)
        return

    async with SessionLocal() as session:
        conv = await session.get(Conversation, conversation_id)
        if conv is not None:
            conv.title = title
            await session.commit()


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: UUID, body: MessageCreate):
    # 존재 확인은 스트림 시작 전에 끝내서, 없는 채팅방이면 평범한 404를 낸다
    # (SSE 스트림 안에서 에러 이벤트로 알리는 것보다 이쪽이 클라이언트가 다루기 쉬움).
    async with SessionLocal() as check_session:
        conv = await check_session.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")

        if body.mode == "auto" and not (classifier.is_ready() and complexity.is_ready()):
            raise HTTPException(
                status_code=503, detail="분류 모델이 아직 준비되지 않았습니다."
            )

        count_result = await check_session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        is_first_message = count_result.scalar_one() == 0

    async def event_stream():
        # StreamingResponse의 제너레이터는 응답이 실제로 스트리밍되는 동안 실행되므로,
        # FastAPI Depends로 주입된 세션(요청 처리 직후 정리됨)을 쓰면 그 사이 세션이
        # 닫혀버릴 수 있다. 그래서 여기서 새 세션을 직접 연다.
        async with SessionLocal() as session:
            # 1) 라우팅 결정 (기존 classifier/complexity/router 그대로 사용)
            task_category: str | None = None
            complexity_score: float | None = None
            if body.mode == "auto":
                task_category = await asyncio.to_thread(classifier.classify, body.content)
                complexity_score = await asyncio.to_thread(
                    complexity.complexity_score, body.content
                )
                spec = select_model(task_category, complexity_score)
            else:
                spec = get_model(body.mode, body.size or DEFAULT_MANUAL_SIZE)

            # 2) meta 이벤트 - 스트리밍 시작 전에 먼저 보내서 프론트가 배지를 바로 그림
            yield _sse(
                {
                    "type": "meta",
                    "model": spec.display_name,
                    "category": task_category,
                    "complexity": complexity_score,
                }
            )

            # 3) 사용자 메시지 저장
            user_tokens = count_tokens(body.content)
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="user",
                    content=body.content,
                    token_count=user_tokens,
                )
            )
            await session.execute(
                Conversation.__table__.update()
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await session.commit()

            # 4) 슬라이딩 윈도우로 이전 대화 이력 구성
            history = await _build_history(session, conversation_id, body.content)

            # 5) LLM 스트리밍 호출
            full_response = ""
            try:
                async for chunk in providers.stream_model(spec, history):
                    full_response += chunk
                    yield _sse({"type": "chunk", "content": chunk})
            except Exception as exc:  # noqa: BLE001
                yield _sse({"type": "error", "message": f"모델 호출 실패: {exc}"})
                return

            # 6) 완료 신호
            yield _sse({"type": "done"})

            # 7) 완성된 응답 저장 (중간 저장 없이 끝난 뒤 한 번에)
            input_tokens = sum(count_tokens(m["content"]) for m in history)
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_response,
                    model_used=spec.display_name,
                    task_category=task_category,
                    complexity_score=complexity_score,
                    input_tokens=input_tokens,
                    output_tokens=count_tokens(full_response),
                )
            )
            await session.execute(
                Conversation.__table__.update()
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await session.commit()

        # 8) 첫 메시지면 제목 생성 - 백그라운드로 던지고 스트림은 바로 닫는다
        if is_first_message:
            asyncio.create_task(_generate_and_save_title(conversation_id, body.content))

    return StreamingResponse(event_stream(), media_type="text/event-stream")
