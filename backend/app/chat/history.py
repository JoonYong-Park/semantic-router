"""모델에 보낼 대화 히스토리 조립 - 토큰 예산 슬라이딩 윈도우, 첨부/참고자료
블록, 모델용 파일 표식([첨부 파일]/[참고자료]/[생성된 파일])."""

import asyncio
import json
import logging
import time
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import service as attachment_service
from app.core.models import (
    Attachment,
    Message,
    ProjectFile,
)
from app.core.tokens import count_tokens

logger = logging.getLogger("uvicorn.error")


MAX_CONTEXT_TOKENS = 16000


# 업로드 응답은 추출 완료를 기다리지 않고 바로 돌아오므로("pending" 상태로
# 전송 버튼을 즉시 누를 수 있게 하기 위함, attachments/service.py 참고), 실제 모델
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
        # 모델이 수정 툴(inspect/edit_document)에 넘길 수 있게 id를 같이 적는다
        # (모델용 표식일 뿐 DB의 Message.content는 그대로).
        blocks.append({"type": "text", "text": f"[첨부 파일: {att.original_name} (id={att.id})]"})
        if att.extraction_status == "done":
            blocks.extend(attachment_service.blocks_for_model(att))
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
        blocks.append({"type": "text", "text": f"[참고자료: {pf.original_name} (id={pf.id})]"})
        blocks.extend(attachment_service.blocks_for_model(pf))
    return blocks


async def _has_files_in_context(session: AsyncSession, conversation_id: UUID, project_id: UUID | None) -> bool:
    """이 대화(업로드/생성) 또는 프로젝트 참고자료에 수정 가능한 형식의 파일이 있는지."""
    from app.documents.editor import supported_formats

    exts = [f".{f}" for f in supported_formats()]  # hwpx는 플래그가 켜졌을 때만 포함
    conditions = [Attachment.original_name.ilike(f"%{ext}") for ext in exts]
    found = await session.execute(
        select(Attachment.id).where(Attachment.chat_id == conversation_id, or_(*conditions)).limit(1)
    )
    if found.first() is not None:
        return True
    if project_id is None:
        return False
    pconds = [ProjectFile.original_name.ilike(f"%{ext}") for ext in exts]
    found = await session.execute(
        select(ProjectFile.id).where(ProjectFile.project_id == project_id, or_(*pconds)).limit(1)
    )
    return found.first() is not None


async def _build_history(
    session: AsyncSession,
    conversation_id: UUID,
    new_query: str,
    new_attachments: list[Attachment] | None = None,
    exclude_message_id: UUID | None = None,
) -> list[dict[str, str | list[dict]]]:
    """토큰 예산(MAX_CONTEXT_TOKENS) 안에서 최근 메시지부터 거꾸로 채우는
    슬라이딩 윈도우. 새 사용자 질문은 아직 DB에 없으므로 마지막에 직접 붙인다.

    과거 턴 중 첨부파일이 달려 있던 턴은 attachments 테이블을 조인해서
    멀티모달 content 배열로 재구성한다 (텍스트만 있던 턴은 그대로 문자열).
    """
    query = select(Message).where(Message.conversation_id == conversation_id)
    if exclude_message_id is not None:
        # 이번 질문은 응답 생성 전에 이미 저장돼 있다 - 아래에서 new_query로 직접
        # 붙이므로 조회에서는 빼야 같은 질문이 두 번 들어가지 않는다.
        query = query.where(Message.id != exclude_message_id)
    result = await session.execute(query.order_by(Message.created_at.desc()))
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
    generated_by_msg: dict[UUID, list[Attachment]] = {}
    msg_ids = [m.id for m in selected]
    if msg_ids:
        att_result = await session.execute(
            select(Attachment).where(Attachment.message_id.in_(msg_ids))
        )
        for att in att_result.scalars().all():
            # 모델이 만든 파일(assistant 메시지에 연결됨)은 content 블록으로 넣지
            # 않는다 - assistant 메시지에 문서/이미지 블록이 섞이면 provider가
            # 400을 낸다. 대신 아래에서 텍스트 표식만 붙인다.
            target = generated_by_msg if att.source == "generated" else attachments_by_msg
            target.setdefault(att.message_id, []).append(att)

    def _model_text(m: Message) -> str:
        # DB의 Message.content는 그대로 두고(UI/메모리 추출 영향 없음) 모델에
        # 보내는 텍스트 끝에만 생성 파일 표식을 붙여, 다음 턴에 모델이 직전에
        # 만든 파일을 언급할 수 있게 한다.
        generated = generated_by_msg.get(m.id, [])
        if not generated:
            return m.content
        markers = "\n".join(f"[생성된 파일: {a.original_name} (id={a.id})]" for a in generated)
        return f"{m.content}\n\n{markers}" if m.content else markers

    return [
        *[
            {
                "role": m.role,
                "content": _content_with_attachments(
                    _model_text(m), attachments_by_msg.get(m.id, [])
                ),
            }
            for m in selected
        ],
        {
            "role": "user",
            "content": _content_with_attachments(new_query, new_attachments or []),
        },
    ]
