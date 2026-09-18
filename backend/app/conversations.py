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
from app.db_models import DEMO_USER_ID, Conversation, Message, UserMemory, UserSettings
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

        # 개인 지침은 대화방마다가 아니라 사용자 하나에 귀속되므로 시작 시점에 한 번만 조회.
        settings_result = await check_session.execute(
            select(UserSettings).where(UserSettings.user_id == DEMO_USER_ID)
        )
        settings = settings_result.scalar_one_or_none()
        personal_instruction = settings.personal_instruction if settings else None
        imported_memory = settings.imported_memory if settings else None

        memory_result = await check_session.execute(
            select(UserMemory).where(UserMemory.user_id == DEMO_USER_ID)
        )
        memory_facts = [m.content for m in memory_result.scalars().all()]

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

            # 3) 슬라이딩 윈도우로 이전 대화 이력 구성 - _build_history는 "이번
            #    질문은 아직 DB에 없다"는 전제로 마지막에 new_query를 직접 붙이므로,
            #    반드시 이번 사용자 메시지를 저장하기 *전에* 호출해야 한다
            #    (순서가 바뀌면 방금 커밋된 메시지가 조회 결과에도 잡히고 new_query로도
            #    또 붙어서, 모델에게 같은 사용자 메시지가 두 번 들어가는 버그가 생긴다).
            history = await _build_history(session, conversation_id, body.content)

            # 4) 사용자 메시지 저장
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

            system_parts = []
            if personal_instruction and personal_instruction.strip():
                system_parts.append(f"[개인 지침]\n{personal_instruction.strip()}")
            if imported_memory and imported_memory.strip():
                system_parts.append(f"[가져온 정보]\n{imported_memory.strip()}")
            if memory_facts:
                facts_block = "\n".join(f"- {fact}" for fact in memory_facts)
                system_parts.append(f"[기억]\n{facts_block}")

            if len(system_parts) > 1:
                priority_note = (
                    "아래는 사용자에 대한 참고 정보다. 서로 내용이 충돌하면 "
                    "[개인 지침] → [가져온 정보] → [기억] 순으로 우선 적용해."
                )
                system_parts = [priority_note] + system_parts

            if system_parts:
                history = [
                    {"role": "system", "content": "\n\n".join(system_parts)}
                ] + history

            # 5) 1차 호출 - 라우팅된 spec 모델 자신이 웹 검색 필요 여부를 판단한다
            # (ChatGPT/Claude가 실제로 쓰는 방식과 동일 - 판단 전용 별도 모델 없음).
            # 비스트리밍으로 tools를 실어 보내고, 결과에 따라 갈린다:
            #   - 도구 호출 없이 바로 텍스트 → 이미 완성된 답이므로 재호출 없이
            #     그 텍스트를 그대로 하나의 chunk로 스트리밍 형태만 맞춰 전달.
            #   - 검색 요청 → Tavily로 검색 → 결과를 history에 얹어 같은 spec으로
            #     2차 호출(이번엔 stream_model()로 실제 스트리밍).
            full_response = ""
            usage: dict[str, int] = {}
            web_search_used = False
            web_search_query: str | None = None
            try:
                tool_result = await providers.call_model_with_tools(spec, history)

                if tool_result.wants_search and tool_result.search_query:
                    web_search_used = True
                    web_search_query = tool_result.search_query
                    yield _sse({"type": "searching", "query": tool_result.search_query})

                    search_results = await providers.tavily_search(
                        tool_result.search_query,
                        max_results=5,
                        time_range=tool_result.time_range,
                        site_hint=tool_result.site_hint,
                    )
                    # 검색 실패(빈 결과)해도 에러 내지 않고 그냥 검색 결과 없이 계속 진행.
                    if search_results:
                        results_text = "\n\n".join(
                            f"[{i + 1}] {r.get('title', '')}\n{r.get('url', '')}\n"
                            f"{r.get('content', '')[:500]}"
                            for i, r in enumerate(search_results)
                        )
                        history = history + [
                            {
                                "role": "system",
                                "content": (
                                    f"[웹 검색 결과: {tool_result.search_query}]\n{results_text}\n\n"
                                    "위 검색 결과를 참고해서 답변해. 답변 마지막에 실제로 "
                                    "참고한 출처만 아래 형식으로 표시해 (참고 안 한 결과는 빼고, "
                                    "위 검색 결과에 없는 URL은 지어내지 마):\n"
                                    "---\n출처:\n- [번호] 제목 (URL)"
                                ),
                            }
                        ]

                    async for chunk in providers.stream_model(spec, history, usage):
                        full_response += chunk
                        yield _sse({"type": "chunk", "content": chunk})

                    # 이번 턴은 API 호출이 두 번(판단 호출 + 검색 후 답변 호출)이라,
                    # 실제 청구 토큰도 둘을 합쳐야 이번 턴의 진짜 사용량이 된다.
                    usage["input_tokens"] = (usage.get("input_tokens") or 0) + (
                        tool_result.usage.get("input_tokens") or 0
                    )
                    usage["output_tokens"] = (usage.get("output_tokens") or 0) + (
                        tool_result.usage.get("output_tokens") or 0
                    )
                else:
                    full_response = tool_result.direct_text or ""
                    yield _sse({"type": "chunk", "content": full_response})
                    usage = dict(tool_result.usage)
            except Exception as exc:  # noqa: BLE001
                # 실패한 턴은 assistant 메시지를 저장하지 않고 그냥 끝나기 때문에,
                # 나중에 DB만 봐서는 왜 응답이 없는지 알 수 없다 - 여기서 로그로
                # 남겨야 원인(타임아웃/401/429 등)을 사후에 확인할 수 있다.
                logger.error(
                    "모델 호출 실패 (conversation_id=%s, model=%s): %s",
                    conversation_id,
                    spec.display_name,
                    exc,
                    exc_info=True,
                )
                yield _sse({"type": "error", "message": f"모델 호출 실패: {exc}"})
                return

            # 6) 완료 신호
            yield _sse({"type": "done"})

            # 7) 완성된 응답 저장 (중간 저장 없이 끝난 뒤 한 번에)
            # 토큰 사용량은 공급사가 응답에 실어 보내는 실제 값을 우선 쓰고,
            # (드물게) 응답에 안 실려 왔을 때만 tiktoken 근사치로 대체한다.
            input_tokens = usage.get("input_tokens")
            if input_tokens is None:
                input_tokens = sum(count_tokens(m["content"]) for m in history)
            output_tokens = usage.get("output_tokens")
            if output_tokens is None:
                output_tokens = count_tokens(full_response)
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_response,
                    model_used=spec.display_name,
                    task_category=task_category,
                    complexity_score=complexity_score,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    web_search_used=web_search_used,
                    web_search_query=web_search_query,
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
