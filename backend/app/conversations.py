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
    Project,
    ProjectFile,
    ProjectMemory,
    UserMemory,
    UserSettings,
)
from app.models_config import DEFAULT_MANUAL_SIZE, get_model
from app.router import select_model
from app.schemas import (
    AttachmentOut,
    ConversationOut,
    ConversationProjectUpdate,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from app.token_counter import count_tokens, estimate_content_tokens

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_CONTEXT_TOKENS = 16000

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
        .order_by(Conversation.pinned.desc(), Conversation.updated_at.desc())
    )
    return list(result.scalars().all())


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: UUID,
    body: ConversationUpdate,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    """제목 변경(이름 변경) / 고정 토글 - 사이드바 채팅 항목의 "⋮" 메뉴에서 사용.
    프로젝트 편입은 별도 엔드포인트(/{conversation_id}/project)로 분리돼 있음."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(conv, key, value)
    await session.commit()
    await session.refresh(conv)
    return conv


@router.patch("/{conversation_id}/project", response_model=ConversationOut)
async def update_conversation_project(
    conversation_id: UUID,
    body: ConversationProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> Conversation:
    """채팅을 프로젝트로 편입(project_id 설정)하거나 해제(null)한다. 이
    시점 이후의 새 메시지부터만 프로젝트 지침/참고자료/메모리가 적용된다 -
    이미 저장된 과거 메시지 내용 자체는 안 바뀌므로 자연히 그렇게 동작한다."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    if body.project_id is not None:
        project = await session.get(Project, body.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    conv.project_id = body.project_id
    await session.commit()
    await session.refresh(conv)
    return conv


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


def _project_reference_blocks(project_files: list[ProjectFile]) -> list[dict]:
    """프로젝트 참고자료를 "요약 목록 → 파일별 본문(텍스트+이미지, 문서 내
    순서 유지)" 순서로 조립한다. 호출 전에 extraction_status == "done"인
    파일만 골라서 넘겨야 한다(대기/실패 파일은 여기서 다루지 않음).

    ProjectFile은 attachments.blocks_for_model()이 기대하는 필드
    (extracted_text/stored_path/mime_type)를 Attachment와 동일하게 갖고
    있어서, 별도 어댑터 없이 그대로 재사용한다."""
    if not project_files:
        return []

    SUMMARY_CHARS = 100
    summary_lines = []
    for pf in project_files:
        preview = ""
        if pf.extracted_text:
            try:
                stored_blocks = json.loads(pf.extracted_text)
                text_parts = [b["text"] for b in stored_blocks if b.get("type") == "text"]
                preview = " ".join(text_parts).strip()[:SUMMARY_CHARS]
            except json.JSONDecodeError:
                preview = ""
        summary_lines.append(
            f"- {pf.original_name}: {preview}" if preview else f"- {pf.original_name}"
        )

    blocks: list[dict] = [
        {"type": "text", "text": "[프로젝트 참고자료 목록]\n" + "\n".join(summary_lines)}
    ]
    for pf in project_files:
        blocks.append({"type": "text", "text": f"[참고자료: {pf.original_name}]"})
        blocks.extend(attachments_module.blocks_for_model(pf))
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
        # 가장 최근 메시지(이 반복문이 역순이라 첫 번째로 보는 것) 혼자서도
        # MAX_CONTEXT_TOKENS를 넘는 경우(길게 답변한 직후 턴 등) 여기서
        # 바로 break해버리면 selected가 통째로 비어서 "방금 나눈 대화"를
        # 전혀 기억 못 하는 현상이 난다. 바로 직전 턴 하나는 예산을 넘든 말든
        # 무조건 포함시키고, 그보다 더 과거 메시지들에 대해서만 예산을 적용한다.
        if selected and total + tokens > MAX_CONTEXT_TOKENS:
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

        # 이 채팅이 프로젝트에 편입돼 있으면 프로젝트 지침 + 프로젝트 전용
        # 메모리를 추가로 조회한다(전역 정보를 대체하는 게 아니라 누적).
        # conv는 위에서 이미 로드해뒀으므로 project_id 확인에 추가 쿼리가
        # 필요 없다.
        project_id = conv.project_id
        project_instruction: str | None = None
        project_memory_facts: list[str] = []
        if project_id is not None:
            project = await check_session.get(Project, project_id)
            project_instruction = project.instruction if project else None
            project_memory_result = await check_session.execute(
                select(ProjectMemory).where(ProjectMemory.project_id == project_id)
            )
            project_memory_facts = [
                m.fact for m in project_memory_result.scalars().all()
            ]

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

            # 프로젝트 참고자료 삽입 - _build_history() 내부는 안 건드리고,
            # 반환된 history의 "첫 번째 메시지" content 맨 앞에 끼워넣는다
            # (system 메시지를 나중에 prepend하는 것과 동일한 후처리 패턴).
            # extraction_status == "done"인 파일만 대상 - pending/failed는
            # 원본 mime_type이 그대로 노출돼 provider가 이해 못 할 수 있어 제외.
            reference_block_count: int | None = None
            if project_id is not None:
                project_files_result = await session.execute(
                    select(ProjectFile)
                    .where(
                        ProjectFile.project_id == project_id,
                        ProjectFile.extraction_status == "done",
                    )
                    .order_by(ProjectFile.created_at)
                )
                reference_blocks = _project_reference_blocks(
                    list(project_files_result.scalars().all())
                )
                if reference_blocks:
                    first_content = history[0]["content"]
                    if isinstance(first_content, str):
                        first_content = (
                            [{"type": "text", "text": first_content}]
                            if first_content
                            else []
                        )
                    history[0]["content"] = reference_blocks + first_content
                    reference_block_count = len(reference_blocks)

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
            # 프로젝트 지침/메모리는 전역 정보를 대체하지 않고 그 뒤에 누적된다 -
            # 이 채팅이 프로젝트에 편입된 시점 이후 턴에만 자연히 적용됨
            # (project_id는 그 시점에만 설정되므로 과거 메시지 자체엔 영향 없음).
            if project_instruction and project_instruction.strip():
                system_parts.append(f"[프로젝트 지침]\n{project_instruction.strip()}")
            if project_memory_facts:
                project_facts_block = "\n".join(f"- {fact}" for fact in project_memory_facts)
                system_parts.append(f"[프로젝트 기억]\n{project_facts_block}")

            if len(system_parts) > 1:
                priority_note = (
                    "아래는 사용자에 대한 참고 정보다. 서로 내용이 충돌하면 "
                    "[개인 지침] → [가져온 정보] → [기억] → [프로젝트 지침] → "
                    "[프로젝트 기억] 순으로 우선 적용해."
                )
                system_parts = [priority_note] + system_parts

            if system_parts:
                history = [
                    {"role": "system", "content": "\n\n".join(system_parts)}
                ] + history

            # 5) 1차 호출 - 라우팅된 spec 모델 자신이 웹 검색 필요 여부를 판단한다
            # (ChatGPT/Claude가 실제로 쓰는 방식과 동일 - 판단 전용 별도 모델 없음).
            # tools를 실어도 스트리밍으로 받아서, 검색 없이 바로 답하는 가장 흔한
            # 경우에도 토큰 단위로 실시간 전달된다(예전엔 이 1차 호출이
            # 비스트리밍이라 완성된 텍스트가 한 번에 "툭" 나타났었음). 결과에
            # 따라 갈린다:
            #   - 텍스트 조각이 옴 → 그대로 실시간 전달(진짜 스트리밍).
            #   - 검색 요청이 옴(텍스트를 하나도 안 보낸 채로) → Tavily로 검색 →
            #     결과를 history에 얹어 같은 spec으로 2차 호출(stream_model()).
            #   - 드물게 텍스트를 일부 보낸 뒤에 검색 요청이 온 경우(모델이
            #     "검색해볼게요" 식으로 운을 뗀 뒤 도구를 호출하는 경우) - 이미
            #     사용자에게 보여준 텍스트를 2차 호출의 독립된 답변과 이어붙이면
            #     서로 다른 두 답이 뒤섞여 보이므로, 이 경우는 검색을 포기하고
            #     지금까지 나온 텍스트를 그대로 최종 답으로 둔다.
            full_response = ""
            usage: dict[str, int] = {}
            web_search_used = False
            web_search_query: str | None = None
            try:
                tool_call_info: dict | None = None
                async for event in providers.stream_model_with_tools(
                    spec, history, usage, reference_block_count
                ):
                    if event["type"] == "text":
                        full_response += event["content"]
                        yield _sse({"type": "chunk", "content": event["content"]})
                    elif event["type"] == "tool_call":
                        tool_call_info = event

                if tool_call_info and not full_response:
                    web_search_used = True
                    web_search_query = tool_call_info["query"]
                    yield _sse({"type": "searching", "query": tool_call_info["query"]})

                    search_results = await providers.tavily_search(
                        tool_call_info["query"],
                        max_results=5,
                        time_range=tool_call_info["time_range"],
                        site_hint=tool_call_info["site_hint"],
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
                                    f"[웹 검색 결과: {tool_call_info['query']}]\n{results_text}\n\n"
                                    "위 검색 결과를 참고해서 답변해. 답변 마지막에 실제로 "
                                    "참고한 출처만 아래 형식으로 표시해 (참고 안 한 결과는 빼고, "
                                    "위 검색 결과에 없는 URL은 지어내지 마):\n"
                                    "---\n출처:\n- [번호] 제목 (URL)"
                                ),
                            }
                        ]

                    # 1차 호출(판단) 토큰은 이미 usage에 쌓여 있으니, 2차 호출은
                    # 같은 dict에 이어서 쌓이지 않도록 별도로 받아서 합산한다.
                    second_call_usage: dict[str, int] = {}
                    async for chunk in providers.stream_model(
                        spec, history, second_call_usage, reference_block_count
                    ):
                        full_response += chunk
                        yield _sse({"type": "chunk", "content": chunk})

                    # 이번 턴은 API 호출이 두 번(판단 호출 + 검색 후 답변 호출)이라,
                    # 실제 청구 토큰도 둘을 합쳐야 이번 턴의 진짜 사용량이 된다.
                    usage["input_tokens"] = (usage.get("input_tokens") or 0) + (
                        second_call_usage.get("input_tokens") or 0
                    )
                    usage["output_tokens"] = (usage.get("output_tokens") or 0) + (
                        second_call_usage.get("output_tokens") or 0
                    )
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
