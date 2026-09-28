"""채팅방(대화방) CRUD + SSE 스트리밍 메시지 전송.

기존 /chat(app/main.py)과 완전히 별개 엔드포인트다. 분류/복잡도/모델선택
로직(classifier.py, complexity.py, router.py, models_config.py)은 그대로
가져다 쓰기만 하고 손대지 않는다.
"""

import asyncio
import json
import logging
import time
import uuid as uuid_module
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import attachments as attachments_module
from app import classifier, complexity, providers
from app.db import SessionLocal, get_session
from app.db_models import (
    DEMO_USER_ID,
    Attachment,
    Conversation,
    Message,
    UserMemory,
    UserSettings,
)
from app.models_config import DEFAULT_MANUAL_SIZE, get_model
from app.router import select_model
from app.schemas import AttachmentOut, ConversationOut, MessageCreate, MessageOut
from app.token_counter import count_tokens, estimate_content_tokens

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_CONTEXT_TOKENS = 4000

# 업로드 응답은 추출 완료를 기다리지 않고 바로 돌아오므로("pending" 상태로
# 전송 버튼을 즉시 누를 수 있게 하기 위함, attachments.py 참고), 실제 모델
# 호출 직전에는 그 추출이 끝나길 여기서 기다린다. LibreOffice 변환처럼 느린
# 케이스를 감안한 타임아웃 - 넘기면 그 시점 상태 그대로 진행한다(아래
# _content_with_attachments가 미완료/실패 첨부를 텍스트 안내로 대체함).
ATTACHMENT_WAIT_TIMEOUT_SECONDS = 30.0
ATTACHMENT_POLL_INTERVAL_SECONDS = 0.3


async def _wait_for_attachment_processing(
    session: AsyncSession, atts: list[Attachment]
) -> None:
    deadline = time.monotonic() + ATTACHMENT_WAIT_TIMEOUT_SECONDS
    pending = [a for a in atts if a.extraction_status == "pending"]
    while pending and time.monotonic() < deadline:
        await asyncio.sleep(ATTACHMENT_POLL_INTERVAL_SECONDS)
        for att in list(pending):
            await session.refresh(att)
            if att.extraction_status != "pending":
                pending.remove(att)
    if pending:
        logger.warning(
            "첨부파일 처리 대기 타임아웃(%s초) - 미완료 상태로 진행: %s",
            ATTACHMENT_WAIT_TIMEOUT_SECONDS,
            [str(a.id) for a in pending],
        )
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
    # ORM cascade 대신 단일 DELETE로 처리 - 자식 메시지/첨부파일 DB 행 정리는
    # FK의 ondelete="CASCADE"(DB 레벨)가 담당한다. 디스크에 남는 업로드
    # 파일(원본+추출 이미지)은 DB가 못 지워주니 커밋 성공 후 별도로 지운다.
    await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
    await session.commit()
    attachments_module.delete_chat_upload_dir(conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_messages(
    conversation_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[MessageOut]:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")

    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = list(result.scalars().all())

    # Message<->Attachment 사이에 relationship()을 안 걸어뒀으므로(다른 모델과
    # 동일한 컨벤션), 첨부 목록을 별도 조회해서 수동으로 붙인다.
    attachments_by_msg: dict[UUID, list[Attachment]] = {}
    msg_ids = [m.id for m in messages]
    if msg_ids:
        att_result = await session.execute(
            select(Attachment).where(Attachment.message_id.in_(msg_ids))
        )
        for att in att_result.scalars().all():
            attachments_by_msg.setdefault(att.message_id, []).append(att)

    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            model_used=m.model_used,
            task_category=m.task_category,
            complexity_score=m.complexity_score,
            created_at=m.created_at,
            attachments=[
                AttachmentOut.model_validate(a)
                for a in attachments_by_msg.get(m.id, [])
            ],
        )
        for m in messages
    ]


def _content_with_attachments(text: str, atts: list[Attachment]) -> str | list[dict]:
    """텍스트 + 첨부파일들을 provider 중립적인 내부 content 표현으로 합친다.
    첨부가 없으면 지금까지와 동일하게 순수 문자열을 그대로 반환 (기존 텍스트
    전용 턴은 조립 비용 없이 그대로 지나간다)."""
    if not atts:
        return text
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for att in atts:
        blocks.append({"type": "text", "text": f"[첨부파일: {att.original_name}]"})
        if att.extraction_status == "done":
            blocks.extend(attachments_module.blocks_for_model(att))
        else:
            # 처리 실패, 또는 대기 타임아웃까지 넘겨서 아직 pending인 경우 -
            # 원본 오피스 문서 mime_type을 그대로 "document" 블록으로 보내면
            # provider가 이해 못 해 400을 낼 수 있으므로, 안내 텍스트로 대체한다.
            blocks.append(
                {
                    "type": "text",
                    "text": "(이 첨부파일은 아직 처리되지 않았거나 처리에 실패해 내용이 포함되지 않았습니다.)",
                }
            )
    return blocks


async def _build_history(
    session: AsyncSession,
    conversation_id: UUID,
    new_query: str,
    new_attachments: list[Attachment] | None = None,
) -> list[dict[str, str | list[dict]]]:
    """토큰 예산(MAX_CONTEXT_TOKENS) 안에서 최근 메시지부터 거꾸로 채우는
    슬라이딩 윈도우. 새 사용자 질문은 아직 DB에 없으므로 마지막에 직접 붙인다.

    과거 턴 중 첨부파일이 달려 있던 턴은 attachments 테이블을 조인해서
    멀티모달 content 배열로 재구성한다 (텍스트만 있던 턴은 그대로 문자열).
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
        # 토큰 예산 산정은 지금까지와 동일하게 DB에 저장된 순수 텍스트
        # 기준(token_count 컬럼 또는 tiktoken 근사)으로 계산한다 - 슬라이딩
        # 윈도우 크기 산정 로직 자체는 첨부파일 도입으로 바꾸지 않는다.
        tokens = msg.token_count or count_tokens(msg.content)
        if total + tokens > MAX_CONTEXT_TOKENS:
            break
        selected.append(msg)
        total += tokens

    selected.reverse()

    attachments_by_msg: dict[UUID, list[Attachment]] = {}
    msg_ids = [m.id for m in selected]
    if msg_ids:
        att_result = await session.execute(
            select(Attachment).where(Attachment.message_id.in_(msg_ids))
        )
        for att in att_result.scalars().all():
            attachments_by_msg.setdefault(att.message_id, []).append(att)

    return [
        *[
            {
                "role": m.role,
                "content": _content_with_attachments(
                    m.content, attachments_by_msg.get(m.id, [])
                ),
            }
            for m in selected
        ],
        {
            "role": "user",
            "content": _content_with_attachments(new_query, new_attachments or []),
        },
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

            # 이번 턴에 참조된 첨부파일 조회 - 이 채팅방 소유이면서 아직 다른
            # 메시지에 붙지 않은(message_id IS NULL) 것만 유효하다고 본다
            # (이미 전송된 첨부를 재사용하려는 요청은 조용히 무시).
            new_attachments: list[Attachment] = []
            if body.attachment_ids:
                att_result = await session.execute(
                    select(Attachment).where(
                        Attachment.id.in_(body.attachment_ids),
                        Attachment.chat_id == conversation_id,
                        Attachment.message_id.is_(None),
                    )
                )
                new_attachments = list(att_result.scalars().all())

            # 업로드 응답이 추출 완료를 기다리지 않고 바로 오므로(첨부 직후
            # 바로 전송 버튼을 누를 수 있게 하기 위함), 여기서 실제 모델
            # 호출 전에 백그라운드 추출이 끝나길 기다린다.
            if any(a.extraction_status == "pending" for a in new_attachments):
                yield _sse({"type": "processing", "message": "첨부파일 처리 중..."})
                await _wait_for_attachment_processing(session, new_attachments)

            # 3) 슬라이딩 윈도우로 이전 대화 이력 구성 - _build_history는 "이번
            #    질문은 아직 DB에 없다"는 전제로 마지막에 new_query를 직접 붙이므로,
            #    반드시 이번 사용자 메시지를 저장하기 *전에* 호출해야 한다
            #    (순서가 바뀌면 방금 커밋된 메시지가 조회 결과에도 잡히고 new_query로도
            #    또 붙어서, 모델에게 같은 사용자 메시지가 두 번 들어가는 버그가 생긴다).
            history = await _build_history(
                session, conversation_id, body.content, new_attachments
            )

            # 4) 사용자 메시지 저장 - id를 미리 만들어서 같은 트랜잭션에서
            #    첨부파일의 message_id를 바로 연결한다 (전송 완료 표시).
            user_message_id = uuid_module.uuid4()
            # 슬라이딩 윈도우 예산에 첨부파일(텍스트+이미지/문서 근사치)도
            # 반영되도록, 실제로 모델에 보낸 것과 같은 content(history의
            # 마지막 항목 - _content_with_attachments 결과)를 그대로 재사용해
            # 카운트한다. body.content(순수 텍스트)만 세면 큰 첨부가 달린
            # 턴이 "거의 공짜"로 취급돼 윈도우에 과도하게 오래 남는 문제가 있었다.
            user_tokens = estimate_content_tokens(history[-1]["content"])
            session.add(
                Message(
                    id=user_message_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=body.content,
                    token_count=user_tokens,
                )
            )
            # Attachment<->Message 사이에는(다른 모델들과 마찬가지로) ORM
            # relationship()을 일부러 안 걸어뒀기 때문에, SQLAlchemy의 flush
            # 순서 결정 로직이 이 둘의 FK 의존관계를 모른다. relationship()
            # 없이 순서를 보장하려면 메시지를 먼저 flush로 실제 INSERT시켜야
            # 한다 - 안 그러면 첨부파일 UPDATE가 먼저 나가서 아직 존재하지
            # 않는 message_id를 참조하다 FK 위반 에러가 난다.
            await session.flush()
            for att in new_attachments:
                att.message_id = user_message_id
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
                input_tokens = sum(
                    estimate_content_tokens(m["content"]) for m in history
                )
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
