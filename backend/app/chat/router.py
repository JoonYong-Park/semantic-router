"""채팅방(대화방) CRUD + SSE 스트리밍 메시지 전송.

분류/복잡도/모델선택 로직(routing/)은 가져다 쓰기만 한다. 응답 생성은
브라우저 연결과 분리된 백그라운드 태스크(run_turn)가 수행하고, SSE는
chat/stream.py의 TurnStream을 구독만 한다."""

import asyncio
import logging
import uuid as uuid_module
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import service as attachment_service
from app.llm.client import stream_model_with_tools
from app.routing import classifier, complexity
from app.tools.base import ToolResult
from app.tools.registry import get_tool_specs, run_tool
from app.core.db import SessionLocal, get_session
from app.core.models import (
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
from app.routing.catalog import DEFAULT_MANUAL_SIZE, get_model
from app.routing.intent import needs_edit_tools, needs_file_tools, needs_image_tools
from app.routing.selector import bump_size, select_model
from app.schemas import (
    AttachmentOut,
    ConversationOut,
    ConversationProjectUpdate,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from app.core.tokens import count_tokens, estimate_content_tokens
from app.chat.context import DOC_TOOLS_GUIDE, _memory_topic_to_text
from app.chat.history import _build_history, _has_files_in_context, _project_reference_blocks, _wait_for_attachment_processing
from app.chat.stream import TurnStream, _active_turns
from app.chat.title import _generate_and_save_title

logger = logging.getLogger("uvicorn.error")


router = APIRouter(prefix="/conversations", tags=["conversations"])


# 한 턴 안에서 "모델 호출 → 툴 실행"을 반복하는 최대 횟수. 마지막 회차는
# tools 없이 호출해서 최종 답변을 강제한다(툴만 계속 부르는 무한 루프 방지).
MAX_TOOL_ROUNDS = 5


# 문서 툴이 노출된 턴의 회차별 출력 상한 - 문서 본문 전체가 툴 인자 JSON으로
# 나오므로 넉넉하게 명시한다. 기본 상한(Anthropic MAX_OUTPUT_TOKENS 16384)보다 커야
# 의미가 있고, 추론 모델은 추론 토큰도 여기 포함된다. 현재 9개 모델의 최대 출력
# (Gemini Flash 65K, Claude 5.5/GPT-6 128K) 안쪽인 32000으로 둔다.
DOC_MAX_OUTPUT_TOKENS = 32000


# 수정 의도(inspect/edit_document) 턴의 최대 회차 - inspect → edit → 답변에
# 검증 실패 재시도나 수정본 재수정이 끼면 5회로는 부족할 수 있다.
EDIT_MAX_TOOL_ROUNDS = 8


# 루프가 끝났는데 텍스트가 비어 있을 때 저장/표시할 고정 문구.
EMPTY_ANSWER = "응답을 완성하지 못했어요. 다시 시도해 주세요."


FILES_ONLY_ANSWER = "파일을 생성했습니다."


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
        # 다른 사용자의 프로젝트에는 편입할 수 없다(없는 것과 똑같이 404)
        if project is None or project.user_id != conv.user_id:
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
    attachment_service.delete_chat_upload_dir(conversation_id)


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


@router.get("/{conversation_id}/stream")
async def resume_stream(conversation_id: UUID):
    """다른 채팅에 갔다 오거나 새로고침한 화면이 진행 중인 응답에 다시 붙는다.
    지금까지의 이벤트를 처음부터 다시 보내고 이어서 실시간으로 보낸다.
    진행 중인 응답이 없으면 204(화면은 DB에 저장된 메시지를 그대로 보여주면 됨)."""
    turn = _active_turns.get(conversation_id)
    if turn is None or turn.done:
        return Response(status_code=204)
    return StreamingResponse(turn.subscribe(), media_type="text/event-stream")


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
        memory_facts = [_memory_topic_to_text(m) for m in memory_result.scalars().all()]

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
                _memory_topic_to_text(m) for m in project_memory_result.scalars().all()
            ]

    # 사용자 질문은 응답 생성을 시작하기 전에 먼저 저장한다 - 예전엔 라우팅/이력
    # 조립 뒤에 저장해서, 그 사이 사용자가 채팅을 옮기면 질문까지 사라졌다.
    # 이번 턴에 참조된 첨부파일도 여기서 이 메시지에 연결한다(이 채팅방 소유이면서
    # 아직 다른 메시지에 붙지 않은 사용자 업로드만 유효 - 이미 전송된 첨부나 모델이
    # 만든 파일을 재사용하려는 요청은 조용히 무시).
    user_message_id = uuid_module.uuid4()
    async with SessionLocal() as save_session:
        valid_attachment_ids: list[UUID] = []
        if body.attachment_ids:
            att_result = await save_session.execute(
                select(Attachment.id).where(
                    Attachment.id.in_(body.attachment_ids),
                    Attachment.chat_id == conversation_id,
                    Attachment.message_id.is_(None),
                    Attachment.source == "upload",
                )
            )
            valid_attachment_ids = list(att_result.scalars().all())
        # token_count는 첨부 추출이 끝난 뒤 실제로 모델에 보낸 content 기준으로
        # 다시 계산해 갱신한다(아래 run_turn) - 여기선 우선 텍스트 기준 값.
        save_session.add(
            Message(
                id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=body.content,
                token_count=count_tokens(body.content),
            )
        )
        # Attachment<->Message 사이에는(다른 모델들과 마찬가지로) ORM
        # relationship()을 일부러 안 걸어뒀기 때문에, SQLAlchemy의 flush
        # 순서 결정 로직이 이 둘의 FK 의존관계를 모른다. relationship()
        # 없이 순서를 보장하려면 메시지를 먼저 flush로 실제 INSERT시켜야
        # 한다 - 안 그러면 첨부파일 UPDATE가 먼저 나가서 아직 존재하지
        # 않는 message_id를 참조하다 FK 위반 에러가 난다.
        await save_session.flush()
        if valid_attachment_ids:
            await save_session.execute(
                Attachment.__table__.update()
                .where(Attachment.id.in_(valid_attachment_ids))
                .values(message_id=user_message_id)
            )
        await save_session.execute(
            Conversation.__table__.update()
            .where(Conversation.id == conversation_id)
            .values(updated_at=datetime.now(timezone.utc))
        )
        await save_session.commit()

    async def run_turn(turn: TurnStream):
        # 브라우저 연결과 무관하게 끝까지 도는 백그라운드 태스크라 세션을 직접 연다.
        async with SessionLocal() as session:
            # 이번 턴 첨부(위에서 질문에 연결함) - 파일 의도 감지(첨부 형식 참고)가
            # 라우팅보다 먼저 필요해서 맨 앞에서 조회한다.
            new_attachments: list[Attachment] = []
            if valid_attachment_ids:
                att_result = await session.execute(
                    select(Attachment).where(Attachment.id.in_(valid_attachment_ids))
                )
                new_attachments = list(att_result.scalars().all())

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

            # 파일 생성/변환 의도가 보이면 문서 툴을 노출하고, auto 모드에서만
            # medium 이상으로 올린다(manual 모드는 사용자가 고른 크기 유지).
            # 로그에는 걸린 키워드만 남긴다(메시지 본문은 안 남김).
            doc_tools_enabled, intent_reason = needs_file_tools(body.content, new_attachments)
            if doc_tools_enabled:
                bumped = bump_size(spec) if body.mode == "auto" else spec
                logger.info(
                    "파일 의도 감지 (conversation_id=%s, reason=%s, model=%s→%s)",
                    conversation_id, intent_reason, spec.display_name, bumped.display_name,
                )
                spec = bumped

            # 이미지 생성 의도는 문서 의도와 독립적으로 본다. 작은 모델로 충분해서
            # 크기 상향은 하지 않는다.
            image_tools_enabled, image_reason = needs_image_tools(body.content)
            if image_tools_enabled:
                logger.info(
                    "이미지 의도 감지 (conversation_id=%s, reason=%s)", conversation_id, image_reason
                )

            # 기존 파일 부분 수정 의도 - 수정 대상이 될 파일이 이번 턴 첨부나 이
            # 대화(업로드/생성)/프로젝트 참고자료에 있을 때만 본다.
            from app.documents.editor import supported_formats

            editable_exts = tuple(f".{f}" for f in supported_formats())
            has_files_in_context = any(
                a.original_name.lower().endswith(editable_exts) for a in new_attachments
            ) or await _has_files_in_context(session, conversation_id, project_id)
            edit_tools_enabled, edit_reason = needs_edit_tools(
                body.content, new_attachments, has_files_in_context
            )
            if edit_tools_enabled:
                bumped = bump_size(spec) if body.mode == "auto" else spec
                logger.info(
                    "수정 의도 감지 (conversation_id=%s, reason=%s, model=%s→%s)",
                    conversation_id, edit_reason, spec.display_name, bumped.display_name,
                )
                spec = bumped

            # 2) meta 이벤트 - 스트리밍 시작 전에 먼저 보내서 프론트가 배지를 바로 그림
            await turn.emit(
                {
                    "type": "meta",
                    "model": spec.display_name,
                    "category": task_category,
                    "complexity": complexity_score,
                }
            )

            # 업로드 응답이 추출 완료를 기다리지 않고 바로 오므로(첨부 직후
            # 바로 전송 버튼을 누를 수 있게 하기 위함), 여기서 실제 모델
            # 호출 전에 백그라운드 추출이 끝나길 기다린다.
            if any(a.extraction_status == "pending" for a in new_attachments):
                await turn.emit({"type": "processing", "message": "첨부파일 처리 중..."})
                await _wait_for_attachment_processing(session, new_attachments)

            # 3) 슬라이딩 윈도우로 이전 대화 이력 구성 - _build_history는 마지막에
            #    new_query(이번 질문)를 직접 붙이므로, 이미 저장된 이번 질문은
            #    exclude_message_id로 조회에서 뺀다(안 빼면 같은 질문이 두 번 들어감).
            history = await _build_history(
                session, conversation_id, body.content, new_attachments,
                exclude_message_id=user_message_id,
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

            # 4) 질문의 token_count 갱신 - 슬라이딩 윈도우 예산에 첨부파일(텍스트+
            #    이미지/문서 근사치)도 반영되도록, 실제로 모델에 보낸 것과 같은
            #    content(history의 마지막 항목)로 다시 센다. body.content(순수
            #    텍스트)만 세면 큰 첨부가 달린 턴이 "거의 공짜"로 취급돼 윈도우에
            #    과도하게 오래 남는 문제가 있었다.
            await session.execute(
                Message.__table__.update()
                .where(Message.id == user_message_id)
                .values(token_count=estimate_content_tokens(history[-1]["content"]))
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

            # 문서 툴이 노출되는 턴에만 사용 지침을 맨 끝에 덧붙인다(우선순위
            # 문구 판단 뒤에 붙여야 지침 하나만 있을 때 우선순위 문구가 안 생김).
            if doc_tools_enabled or edit_tools_enabled:
                system_parts.append(DOC_TOOLS_GUIDE)

            if system_parts:
                history = [
                    {"role": "system", "content": "\n\n".join(system_parts)}
                ] + history

            # 5) 툴 루프 - 라우팅된 spec 모델 자신이 툴(웹 검색 등) 사용 여부를
            # 판단한다(판단 전용 별도 모델 없음). 회차마다 스트리밍으로 받아서
            # 텍스트는 바로 chunk로 보내고, 툴 호출이 오면 전부 실행해 결과를
            # 작업용 messages 사본에 붙인 뒤 다시 호출한다. 툴 호출이 없는 응답이
            # 오면 끝. MAX_TOOL_ROUNDS번째 회차는 툴 호출 없이 최종 답변을
            # 강제한다(force_final - 벤더별 처리는 providers 참고). 툴 호출/결과는
            # 이번 요청 안에서만 쓰고 DB에는 최종 텍스트만 저장한다(다음 턴
            # 히스토리에 툴 메시지는 안 들어감). 문서 툴이 만든 파일은
            # generated_ids로 모아 assistant 메시지 저장 시 연결한다.
            full_response = ""
            usage: dict[str, int] = {}
            web_search_used = False
            web_search_query: str | None = None
            generated_ids: list[UUID] = []
            working = list(history)
            history_len = len(working)
            tool_specs = get_tool_specs(
                include_doc_tools=doc_tools_enabled,
                include_image_tools=image_tools_enabled,
                include_edit_tools=edit_tools_enabled,
            )
            # 수정 흐름은 inspect → edit → 답변이 기본이고, 검증 실패 시 다시
            # inspect → edit, 수정본을 이어서 고치면 inspect/edit가 한 번 더 들어가서
            # 기본 5회로는 마지막 회차(툴 금지)에 막힐 수 있다 → 수정 의도 턴만 늘린다.
            max_rounds = EDIT_MAX_TOOL_ROUNDS if edit_tools_enabled else MAX_TOOL_ROUNDS
            # 문서 툴 인자(본문 전체 JSON)는 길어서, 노출된 턴은 모든 회차의
            # 출력 상한을 올린다. 그 외 턴은 None(기존 동작 그대로).
            max_output_tokens = DOC_MAX_OUTPUT_TOKENS if doc_tools_enabled else None
            tool_ctx_base = {
                "conversation_id": conversation_id,
                "project_id": project_id,
                "user_id": DEMO_USER_ID,
                "session_factory": SessionLocal,
                "turn_state": {},
            }
            try:
                for round_index in range(max_rounds):
                    is_last_round = round_index == max_rounds - 1
                    round_text = ""
                    round_calls: list[dict] = []
                    round_usage: dict[str, int] = {}
                    async for event in stream_model_with_tools(
                        spec,
                        working,
                        tool_specs,
                        round_usage,
                        reference_block_count,
                        history_len,
                        max_output_tokens=max_output_tokens,
                        force_final=is_last_round,
                    ):
                        if event["type"] == "text":
                            piece = event["content"]
                            # 앞 회차에서 이미 텍스트를 보냈으면 회차 사이를 띄운다
                            # ("파일 만들어 드릴게요" → 툴 실행 → 최종 답변 같은 흐름).
                            if not round_text and full_response:
                                piece = "\n\n" + piece
                            round_text += event["content"]
                            full_response += piece
                            await turn.emit({"type": "chunk", "content": piece})
                        elif event["type"] == "tool_call":
                            round_calls.append(event)

                    # 회차마다 API 호출이 따로 청구되므로 실제 사용량은 전부 합산.
                    for key in ("input_tokens", "output_tokens"):
                        if round_usage.get(key) is not None:
                            usage[key] = (usage.get(key) or 0) + round_usage[key]

                    if round_calls:
                        logger.info(
                            "툴 호출 (conversation_id=%s, round=%d, tools=%s)",
                            conversation_id, round_index + 1, [c["name"] for c in round_calls],
                        )
                    if not round_calls or is_last_round:
                        break

                    working.append(
                        {
                            "role": "assistant",
                            "content": round_text,
                            "tool_calls": [
                                {
                                    "id": c["id"],
                                    "name": c["name"],
                                    "arguments": c["arguments"] or {},
                                    "provider_extra": c["provider_extra"],
                                }
                                for c in round_calls
                            ],
                        }
                    )
                    # 병렬 호출을 꺼뒀어도 여러 개가 올 수 있다 - 모든 호출에 결과를
                    # 돌려줘야 다음 요청이 400이 안 나므로 순서대로 전부 실행한다.
                    for call in round_calls:
                        result: ToolResult | None = None
                        async for kind, payload in run_tool(call, tool_ctx_base):
                            if kind == "event":
                                await turn.emit(payload)
                            else:
                                result = payload
                        if result.metadata.get("web_search_query") and not web_search_used:
                            web_search_used = True
                            web_search_query = result.metadata["web_search_query"]
                        if result.metadata.get("generated_attachment_id"):
                            generated_ids.append(result.metadata["generated_attachment_id"])
                        working.append(
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "name": call["name"],
                                "content": result.content,
                                "is_error": result.is_error,
                            }
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
                if not generated_ids:
                    await turn.emit({"type": "error", "message": f"모델 호출 실패: {exc}"})
                    return
                # 이번 턴에 이미 만든 파일이 있으면 카드가 사라지지 않게 에러 대신
                # 지금까지의 텍스트(없으면 고정 문구)로 정상 마무리하고 저장/연결한다.

            # 빈 최종 답변 방어 - 벤더 불문 빈 답변이 저장되지 않게 고정 문구로 대체.
            if not full_response.strip():
                fallback = FILES_ONLY_ANSWER if generated_ids else EMPTY_ANSWER
                full_response = fallback
                await turn.emit({"type": "chunk", "content": fallback})

            # 7) 완성된 응답 저장 (중간 저장 없이 끝난 뒤 한 번에)
            # 토큰 사용량은 공급사가 응답에 실어 보내는 실제 값을 우선 쓰고,
            # (드물게) 응답에 안 실려 왔을 때만 tiktoken 근사치로 대체한다.
            input_tokens = usage.get("input_tokens")
            if input_tokens is None:
                input_tokens = sum(
                    estimate_content_tokens(m["content"]) for m in working
                )
            output_tokens = usage.get("output_tokens")
            if output_tokens is None:
                output_tokens = count_tokens(full_response)
            # 생성 파일을 이 메시지에 연결해야 해서 id를 미리 만든다 - 사용자
            # 메시지 + 첨부 연결과 같은 패턴(relationship이 없어 flush로 INSERT
            # 순서를 먼저 보장한 뒤 첨부의 message_id를 채움).
            assistant_message_id = uuid_module.uuid4()
            session.add(
                Message(
                    id=assistant_message_id,
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
            if generated_ids:
                await session.flush()
                await session.execute(
                    Attachment.__table__.update()
                    .where(
                        Attachment.id.in_(generated_ids),
                        Attachment.chat_id == conversation_id,
                    )
                    .values(message_id=assistant_message_id)
                )
            await session.execute(
                Conversation.__table__.update()
                .where(Conversation.id == conversation_id)
                .values(updated_at=datetime.now(timezone.utc))
            )
            await session.commit()

            # 6) 완료 신호 - 답변이 DB에 저장된 뒤에 보낸다(화면이 done을 받고
            #    목록/메시지를 다시 불러와도 방금 답변이 보이도록).
            await turn.emit({"type": "done"})

        # 8) 첫 메시지면 제목 생성 - 백그라운드로 던지고 스트림은 바로 닫는다
        if is_first_message:
            asyncio.create_task(_generate_and_save_title(conversation_id, body.content))

    async def drive(turn: TurnStream):
        try:
            await run_turn(turn)
        except Exception as exc:  # noqa: BLE001
            logger.error("응답 생성 태스크 실패 (conversation_id=%s): %s", conversation_id, exc, exc_info=True)
            await turn.emit({"type": "error", "message": f"응답 생성 실패: {exc}"})
        finally:
            await turn.finish()
            if _active_turns.get(conversation_id) is turn:
                del _active_turns[conversation_id]

    turn = TurnStream()
    _active_turns[conversation_id] = turn
    turn.task = asyncio.create_task(drive(turn))
    return StreamingResponse(turn.subscribe(), media_type="text/event-stream")
