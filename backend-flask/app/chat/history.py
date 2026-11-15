"""모델에 보낼 대화 히스토리 조립 (기존 chat/history.py).

토큰 예산 슬라이딩 윈도우, 첨부/참고자료 블록, 모델용 파일 표식
([첨부 파일]/[참고자료]/[생성된 파일])은 기존과 같다. 메시지와 첨부는
대화 파티션 Query 한 번(get_conversation_page)으로 같이 읽는다.
"""

import logging
import time

from app.core.tokens import count_tokens
from app.db.repo import conversations as conv_repo
from app.db.repo import projects as project_repo
from app.services import files

logger = logging.getLogger("uvicorn.error")

MAX_CONTEXT_TOKENS = 16000
ATTACHMENT_WAIT_TIMEOUT_SECONDS = 30.0
ATTACHMENT_POLL_INTERVAL_SECONDS = 0.3


def wait_for_attachment_processing(cid, atts: list[dict]) -> list[dict]:
    """업로드 응답은 추출 완료를 기다리지 않으므로, 모델 호출 직전에 여기서 기다린다.
    타임아웃이 지나면 그 시점 상태로 진행한다. 최신 상태의 첨부 목록을 돌려준다."""
    deadline = time.monotonic() + ATTACHMENT_WAIT_TIMEOUT_SECONDS
    latest = {a["id"]: a for a in atts}
    pending = [a["id"] for a in atts if a["extraction_status"] == "pending"]
    while pending and time.monotonic() < deadline:
        time.sleep(ATTACHMENT_POLL_INTERVAL_SECONDS)
        for aid in list(pending):
            fresh = conv_repo.get_attachment_in_conv(cid, aid)
            if fresh is None or fresh["extraction_status"] != "pending":
                pending.remove(aid)
                if fresh is not None:
                    latest[aid] = fresh
    if pending:
        logger.warning("첨부파일 처리 대기 타임아웃(%s초) - 미완료 상태로 진행: %s",
                       ATTACHMENT_WAIT_TIMEOUT_SECONDS, pending)
    return [latest[a["id"]] for a in atts]


def content_with_attachments(text: str, atts: list[dict]) -> str | list[dict]:
    """텍스트 + 첨부를 provider 중립 content로. 첨부가 없으면 문자열 그대로."""
    if not atts:
        return text
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for att in atts:
        blocks.append({"type": "text", "text": f"[첨부 파일: {att['original_name']} (id={att['id']})]"})
        if att["extraction_status"] == "done":
            blocks.extend(files.blocks_for_model(att))
        else:
            blocks.append({
                "type": "text",
                "text": "(이 첨부파일은 아직 처리되지 않았거나 처리에 실패해 내용이 포함되지 않았습니다.)",
            })
    return blocks


def project_reference_blocks(project_files: list[dict]) -> list[dict]:
    """프로젝트 참고자료를 "요약 목록 → 파일별 본문" 순서로. 추출 완료 파일만 넘긴다."""
    if not project_files:
        return []
    summary_chars = 100
    lines = []
    for pf in project_files:
        stored = files.load_blocks(pf) or []
        preview = " ".join(b["text"] for b in stored if b.get("type") == "text").strip()[:summary_chars]
        lines.append(f"- {pf['original_name']}: {preview}" if preview else f"- {pf['original_name']}")
    blocks: list[dict] = [{"type": "text", "text": "[프로젝트 참고자료 목록]\n" + "\n".join(lines)}]
    for pf in project_files:
        blocks.append({"type": "text", "text": f"[참고자료: {pf['original_name']} (id={pf['id']})]"})
        blocks.extend(files.blocks_for_model(pf))
    return blocks


def has_files_in_context(cid, pid) -> bool:
    """이 대화(업로드/생성) 또는 프로젝트 참고자료에 수정 가능한 형식의 파일이 있는지."""
    from app.documents.editor import supported_formats

    exts = tuple(f".{f}" for f in supported_formats())
    if any(a["original_name"].lower().endswith(exts) for a in conv_repo.list_attachments(cid)):
        return True
    if pid is None:
        return False
    return any(f["original_name"].lower().endswith(exts) for f in project_repo.list_project_files(pid))


def build_history(cid, new_query: str, new_attachments: list[dict] | None = None,
                  exclude_message_id: str | None = None) -> list[dict]:
    """토큰 예산 안에서 최근 메시지부터 거꾸로 채운다. 이번 질문은 마지막에 직접 붙인다."""
    messages = [m for m in conv_repo.get_conversation_page(cid) if m["id"] != exclude_message_id]

    selected: list[dict] = []
    total = 0
    for msg in reversed(messages):
        tokens = msg.get("token_count") or count_tokens(msg["content"])
        # 바로 직전 메시지 하나는 예산을 넘어도 넣는다 (방금 대화를 잊지 않게).
        if selected and total + tokens > MAX_CONTEXT_TOKENS:
            break
        selected.append(msg)
        total += tokens
    selected.reverse()

    def model_text(m: dict) -> str:
        # 모델이 만든 파일은 content 블록으로 넣지 않고(assistant 메시지에 문서/이미지 블록이
        # 섞이면 400) 텍스트 표식만 붙인다. 저장된 메시지 본문은 그대로다.
        generated = [a for a in m.get("attachments", []) if a.get("source") == "generated"]
        if not generated:
            return m["content"]
        markers = "\n".join(f"[생성된 파일: {a['original_name']} (id={a['id']})]" for a in generated)
        return f"{m['content']}\n\n{markers}" if m["content"] else markers

    def uploads(m: dict) -> list[dict]:
        return [a for a in m.get("attachments", []) if a.get("source", "upload") != "generated"]

    return [
        *[{"role": m["role"], "content": content_with_attachments(model_text(m), uploads(m))} for m in selected],
        {"role": "user", "content": content_with_attachments(new_query, new_attachments or [])},
    ]
