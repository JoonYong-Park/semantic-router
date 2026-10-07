"""채팅 첨부파일 저장/추출/미리보기 변환 + 고아 첨부 정리 배치 (엔드포인트는 router.py).
projects와 tools도 여기 헬퍼를 같이 쓴다.

2단계 업로드 방식: 드래그 즉시 이 라우터로 업로드+파싱까지 끝내고
attachment_id를 반환한다. 실제 메시지 전송(chat/router.py)은 이
attachment_id만 참조한다."""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import openpyxl
from fastapi import HTTPException
from fastapi.responses import FileResponse
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents import extractor
from app.core.db import SessionLocal
from app.core.models import Attachment, Conversation

logger = logging.getLogger("uvicorn.error")


UPLOAD_ROOT = Path(os.environ.get("UPLOAD_DIR", "/app/uploads"))


CHATS_ROOT = UPLOAD_ROOT / "chats"


PROJECTS_ROOT = UPLOAD_ROOT / "projects"  # 프로젝트 참고자료 기능 - 아직 미사용, 폴더도 미리 안 만듦


ORPHAN_MAX_AGE = timedelta(hours=24)


# 모델이 만든 파일은 연결 중단 등으로 message_id를 못 받았어도 사용자가 이미
# 다운로드 카드를 봤을 수 있어서 업로드 고아보다 넉넉하게 둔다.
GENERATED_ORPHAN_MAX_AGE = timedelta(days=7)


MAX_GENERATED_BYTES = 25 * 1024 * 1024  # 생성 파일당 상한 (업로드 상한과 동일)


ORPHAN_SWEEP_INTERVAL_SECONDS = 60 * 60  # 1시간마다 스캔


MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25MB - 데모 규모 기준 상한


# 확장자 -> 카테고리. 카테고리별로 추출/전달 방식이 갈린다.
TEXT_PLAIN_EXTS = {"txt", "md"}


CSV_EXTS = {"csv"}


XLSX_EXTS = {"xlsx"}


DOCX_EXTS = {"docx"}


PPTX_EXTS = {"pptx"}


HWP_EXTS = {"hwp", "hwpx"}


PDF_EXTS = {"pdf"}


IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}


ALL_ALLOWED_EXTS = (
    TEXT_PLAIN_EXTS
    | CSV_EXTS
    | XLSX_EXTS
    | DOCX_EXTS
    | PPTX_EXTS
    | HWP_EXTS
    | PDF_EXTS
    | IMAGE_EXTS
)


MIME_BY_EXT = {
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "hwp": "application/x-hwp",
    "hwpx": "application/hwp+zip",
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _chat_upload_dir(chat_id: uuid.UUID) -> Path:
    return CHATS_ROOT / str(chat_id)


def _preview_cache_path(root_dir: Path, item_id: uuid.UUID) -> Path:
    return root_dir / "previews" / f"{item_id}.pdf"


def _save_uploaded_file(root_dir: Path, ext: str, content: bytes) -> tuple[uuid.UUID, Path]:
    """루트 디렉터리(uploads/chats/{chat_id} 또는 향후 uploads/projects/{project_id})
    밑에 UUID 파일명으로 원본을 저장한다. 원본 파일명은 절대 경로에 안 쓴다
    (path traversal 방지) - 채팅 첨부/프로젝트 참고자료가 공유해서 쓸 수 있게
    특정 chat_id에 결합하지 않은 범용 형태."""
    file_uuid = uuid.uuid4()
    root_dir.mkdir(parents=True, exist_ok=True)
    stored_path = root_dir / f"{file_uuid}.{ext}"
    stored_path.write_bytes(content)
    return file_uuid, stored_path


def _delete_stored_files(
    root_dir: Path,
    item_id: uuid.UUID,
    original_name: str,
    stored_path: str,
    extracted_text: str | None,
) -> None:
    """디스크에 저장된 파일을 전부 정리한다 - 원본/변환본, 스캔본 폴백 시 남는
    원본, 추출된 임베드 이미지, 미리보기 캐시까지. 채팅 첨부(_delete_attachment_files)와
    향후 프로젝트 참고자료가 공유해서 쓸 수 있게 chat_id 대신 root_dir을 받는
    범용 형태로 뒀다."""
    Path(stored_path).unlink(missing_ok=True)

    # 스캔본 폴백(텍스트 0건)으로 stored_path가 변환된 PDF로 교체된 경우,
    # 원래 업로드된 원본(docx/pptx/hwp)은 업로드 시점 파일명 규칙
    # ({item_id}.{원본 확장자})으로 같은 디렉터리에 그대로 남아있다.
    # stored_path만 지우면 이 원본이 고아로 남으므로 같이 정리한다.
    original_ext = _ext_of(original_name)
    stored_ext = Path(stored_path).suffix.lstrip(".").lower()
    if original_ext and stored_ext != original_ext:
        original_path = root_dir / f"{item_id}.{original_ext}"
        original_path.unlink(missing_ok=True)

    if extracted_text:
        try:
            blocks = json.loads(extracted_text)
        except json.JSONDecodeError:
            blocks = []
        for block in blocks:
            if block.get("type") == "image" and block.get("path"):
                Path(block["path"]).unlink(missing_ok=True)

    _preview_cache_path(root_dir, item_id).unlink(missing_ok=True)


async def _get_attachment_or_404(
    conversation_id: uuid.UUID, attachment_id: uuid.UUID, session: AsyncSession
) -> Attachment:
    """뷰어 엔드포인트 공통 검증 - 기존 업로드/취소 엔드포인트와 동일한 컨벤션
    (로그인 없는 데모 구조라 conversation 존재 + chat_id 일치 이상은 검증하지 않음)."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None or attachment.chat_id != conversation_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    return attachment


def _resolve_under_upload_root(path: Path) -> Path:
    """stored_path가 UPLOAD_ROOT 밖을 가리키면(잘못된 DB 값 등) 차단하는 방어 코드."""
    resolved = path.resolve()
    if not resolved.is_relative_to(UPLOAD_ROOT.resolve()):
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    return resolved


def _extract_sync(
    ext: str, stored_path: Path, upload_dir: Path, mime_type: str, filename: str
) -> tuple[Path, str, str | None, str]:
    """실제 추출/변환 로직 (동기) - 백그라운드 태스크에서 asyncio.to_thread로
    감싸 호출한다. LibreOffice 변환처럼 느릴 수 있는 작업이 이벤트 루프를
    막지 않게 하기 위함."""
    extracted_text: str | None = None
    extraction_status = "done"
    final_stored_path = stored_path
    final_mime_type = mime_type

    try:
        if ext in IMAGE_EXTS:
            # 비전 직접 전달형 - PNG로 통일 변환 + 리사이즈까지 끝내둔다.
            converted_path = upload_dir / f"{stored_path.stem}.png"
            img = Image.open(stored_path)
            img = extractor.resize_if_needed(img)
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")
            img.save(converted_path, format="PNG")
            if converted_path != stored_path:
                stored_path.unlink(missing_ok=True)
            final_stored_path = converted_path
            final_mime_type = "image/png"

        elif ext in PDF_EXTS:
            # 원본 그대로 비전에 전달 - 텍스트 추출은 없지만, 손상된 PDF를
            # 업로드 시점에 걸러내기 위해 pypdf로 열어만 본다.
            try:
                PdfReader(str(stored_path))
            except Exception as exc:  # noqa: BLE001
                logger.error("손상된 PDF 업로드: %s (%s)", filename, exc)
                extraction_status = "failed"

        else:
            image_dir = upload_dir / "images"
            if ext in TEXT_PLAIN_EXTS:
                result = extractor.extract_text_plain(stored_path)
            elif ext in CSV_EXTS:
                result = extractor.extract_csv(stored_path)
            elif ext in XLSX_EXTS:
                result = extractor.extract_xlsx(stored_path)
            elif ext in DOCX_EXTS:
                result = extractor.extract_docx(stored_path, image_dir)
            elif ext in PPTX_EXTS:
                result = extractor.extract_pptx(stored_path, image_dir)
            else:  # hwp/hwpx
                result = extractor.extract_hwp(stored_path, image_dir)

            if result.status == "failed":
                extraction_status = "failed"
            elif result.fallback_pdf_path:
                # 텍스트가 전혀 없는 스캔본 등 - PDF로 변환된 결과를 그대로
                # 비전 전달형 첨부처럼 취급한다.
                final_stored_path = Path(result.fallback_pdf_path)
                final_mime_type = "application/pdf"
                extracted_text = None
            else:
                extracted_text = json.dumps(result.blocks, ensure_ascii=False)

    except Exception as exc:  # noqa: BLE001
        logger.error("첨부파일 추출 실패 (%s): %s", filename, exc, exc_info=True)
        extraction_status = "failed"

    return final_stored_path, final_mime_type, extracted_text, extraction_status


async def _process_attachment_background(
    attachment_id: uuid.UUID,
    ext: str,
    stored_path: Path,
    upload_dir: Path,
    mime_type: str,
    filename: str,
) -> None:
    """업로드 응답을 즉시 돌려주기 위해 추출/변환을 백그라운드로 미뤄서
    처리한다. 전송 버튼이 추출 완료를 기다리지 않고 바로 눌리도록 하려는
    목적 - PPTX처럼 LibreOffice 변환이 걸리는 포맷은 이 처리에 수 초가 걸릴
    수 있다."""
    try:
        final_stored_path, final_mime_type, extracted_text, extraction_status = (
            await asyncio.to_thread(
                _extract_sync, ext, stored_path, upload_dir, mime_type, filename
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("첨부파일 백그라운드 처리 실패 (%s): %s", filename, exc, exc_info=True)
        final_stored_path, final_mime_type, extracted_text, extraction_status = (
            stored_path,
            mime_type,
            None,
            "failed",
        )

    async with SessionLocal() as session:
        attachment = await session.get(Attachment, attachment_id)
        if attachment is None:
            # 처리 도중 사용자가 취소(DELETE)함 - 그 사이 새로 만들어진 변환
            # 결과물만 정리하고 끝낸다 (원본은 취소 시점에 이미 삭제됐음).
            if final_stored_path != stored_path:
                final_stored_path.unlink(missing_ok=True)
            return
        attachment.stored_path = str(final_stored_path)
        attachment.mime_type = final_mime_type
        attachment.extracted_text = extracted_text
        attachment.extraction_status = extraction_status
        await session.commit()


async def save_generated_file(
    chat_id: uuid.UUID,
    display_name: str,
    ext: str,
    content: bytes,
    generation_meta: dict,
    source_attachment_id: uuid.UUID | None = None,
) -> Attachment:
    """모델이 문서 툴로 만든 파일을 uploads/chats/{chat_id}/generated/ 아래에
    UUID 파일명으로 저장하고 Attachment(source='generated', message_id=NULL)로
    등록한다. message_id는 assistant 메시지가 저장될 때 툴 루프가 연결한다
    (그 전에 연결이 끊기면 7일 뒤 고아 정리 대상). 채팅방 폴더 하위라 채팅방
    삭제 시 delete_chat_upload_dir로 함께 지워진다."""
    if len(content) > MAX_GENERATED_BYTES:
        raise ValueError(
            f"생성된 파일이 너무 큽니다 ({len(content) // (1024 * 1024)}MB, "
            f"최대 {MAX_GENERATED_BYTES // (1024 * 1024)}MB)."
        )
    generated_dir = _chat_upload_dir(chat_id) / "generated"
    file_uuid, stored_path = _save_uploaded_file(generated_dir, ext, content)
    # 업로드 경로와 같은 규칙으로 UPLOAD_ROOT 밖을 가리키지 않는지 확인
    _resolve_under_upload_root(stored_path)

    attachment = Attachment(
        id=file_uuid,
        chat_id=chat_id,
        message_id=None,
        original_name=display_name,
        stored_path=str(stored_path),
        mime_type=MIME_BY_EXT.get(ext, "application/octet-stream"),
        size_bytes=len(content),
        extracted_text=None,
        extraction_status="done",
        source="generated",
        source_attachment_id=source_attachment_id,
        generation_meta=json.dumps(generation_meta, ensure_ascii=False),
    )
    async with SessionLocal() as session:
        session.add(attachment)
        await session.commit()
        await session.refresh(attachment)
    return attachment


def _delete_attachment_files(attachment: Attachment) -> None:
    """채팅 첨부 전용 얇은 래퍼 - 해당 chat_id의 루트 디렉터리를 채워서
    범용 _delete_stored_files에 위임한다."""
    _delete_stored_files(
        _chat_upload_dir(attachment.chat_id),
        attachment.id,
        attachment.original_name,
        attachment.stored_path,
        attachment.extracted_text,
    )


async def _sweep_orphan_attachments() -> None:
    """message_id가 안 채워진 첨부를 정리 - 업로드는 24시간(전송 안 하고 이탈),
    생성 파일은 7일(스트리밍 중 연결이 끊겨 assistant 메시지에 연결되지 못한 경우)."""
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        result = await session.execute(
            select(Attachment).where(
                Attachment.message_id.is_(None),
                or_(
                    and_(
                        Attachment.source != "generated",
                        Attachment.created_at < now - ORPHAN_MAX_AGE,
                    ),
                    and_(
                        Attachment.source == "generated",
                        Attachment.created_at < now - GENERATED_ORPHAN_MAX_AGE,
                    ),
                ),
            )
        )
        orphans = list(result.scalars().all())
        for attachment in orphans:
            _delete_attachment_files(attachment)
            await session.delete(attachment)
        if orphans:
            await session.commit()
            logger.info("고아 첨부파일 %d건 정리됨", len(orphans))


async def orphan_cleanup_loop() -> None:
    """main.py의 lifespan에서 asyncio.create_task로 띄우는 상시 배치 루프.
    이 프로젝트에 Celery/Redis 등 별도 스케줄러가 없어(memory/extract.py의 수동 트리거
    패턴과 동일한 이유), asyncio 루프 + sleep으로 충분한 규모라고 판단."""
    while True:
        try:
            await _sweep_orphan_attachments()
        except Exception as exc:  # noqa: BLE001
            logger.error("고아 첨부파일 정리 중 오류: %s", exc, exc_info=True)
        await asyncio.sleep(ORPHAN_SWEEP_INTERVAL_SECONDS)


def blocks_for_model(attachment: Attachment) -> list[dict]:
    """Attachment 하나를 llm/client.py가 소비하는 내부 content block
    표현(provider 중립)으로 변환한다. base64 인코딩은 여기서 요청 시점에
    매번 수행한다 - DB에 base64를 미리 저장해두면 리사이즈된 원본이 바뀔 때
    stale해질 수 있고, 어차피 파일 자체가 크지 않은 데모 규모라 비용도 낮다.
    """
    if attachment.extracted_text is not None:
        # 텍스트 추출형(하이브리드 포함) - JSON 블록 목록을 그대로 변환.
        stored_blocks = json.loads(attachment.extracted_text)
        blocks: list[dict] = []
        for block in stored_blocks:
            if block["type"] == "text":
                if block["text"].strip():
                    blocks.append({"type": "text", "text": block["text"]})
            else:  # image
                path = Path(block["path"])
                if not path.exists():
                    continue
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                blocks.append(
                    {
                        "type": "image",
                        "media_type": block.get("mime_type", "image/png"),
                        "data": data,
                    }
                )
        return blocks

    # 비전 직접 전달형 (PDF/IMG, 또는 스캔본 폴백으로 변환된 PDF) - 원본을 그대로.
    path = Path(attachment.stored_path)
    if not path.exists():
        return []
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    block_type = "image" if attachment.mime_type.startswith("image/") else "document"
    return [{"type": block_type, "media_type": attachment.mime_type, "data": data}]


# --- 첨부파일 미리보기(뷰어) ---

MAX_TABLE_COLS = 50


MAX_TEXT_PREVIEW_BYTES = 1024 * 1024  # 1MB


PREVIEW_CONVERT_TIMEOUT = 60


# attachment_id별로 동시 변환 요청이 중복 실행되지 않게 막는 락.
# 데모 규모라 dict가 영구히 자라는 것도 문제되지 않는 수준.
_preview_locks: dict[uuid.UUID, asyncio.Lock] = {}


_preview_locks_guard = asyncio.Lock()


async def _get_preview_lock(attachment_id: uuid.UUID) -> asyncio.Lock:
    async with _preview_locks_guard:
        lock = _preview_locks.get(attachment_id)
        if lock is None:
            lock = asyncio.Lock()
            _preview_locks[attachment_id] = lock
        return lock


def _content_disposition(filename: str, inline: bool) -> str:
    disposition = "inline" if inline else "attachment"
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def _csv_table(path: Path, sheet_name: str) -> dict:
    raw = path.read_bytes()
    text = extractor.decode_text_with_fallback(raw)
    all_rows = list(csv.reader(io.StringIO(text)))
    total_rows = len(all_rows)
    limited_rows = [
        ["" if c is None else str(c) for c in row][:MAX_TABLE_COLS]
        for row in all_rows[: extractor.MAX_ROWS + 1]
    ]
    return {
        "name": sheet_name,
        "rows": limited_rows,
        "total_rows": total_rows,
        "truncated": total_rows > extractor.MAX_ROWS + 1,
    }


def _xlsx_tables(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets: list[dict] = []
    try:
        for sheet in wb.worksheets:
            rows: list[list[str]] = []
            total_rows = 0
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                total_rows = i + 1
                if i <= extractor.MAX_ROWS:
                    rows.append(["" if c is None else str(c) for c in row][:MAX_TABLE_COLS])
            sheets.append(
                {
                    "name": sheet.title,
                    "rows": rows,
                    "total_rows": total_rows,
                    "truncated": total_rows > extractor.MAX_ROWS + 1,
                }
            )
    finally:
        wb.close()
    return sheets


def _plain_text_preview(path: Path) -> dict:
    raw = path.read_bytes()
    truncated = len(raw) > MAX_TEXT_PREVIEW_BYTES
    text = extractor.decode_text_with_fallback(raw[:MAX_TEXT_PREVIEW_BYTES])
    return {"text": text, "truncated": truncated}


def _hwp_text_preview(attachment: Attachment) -> dict:
    if not attachment.extracted_text:
        raise HTTPException(status_code=400, detail="추출된 텍스트가 없습니다.")
    try:
        blocks = json.loads(attachment.extracted_text)
    except json.JSONDecodeError:
        blocks = []
    parts = [
        block["text"] if block.get("type") == "text" else "[이미지]"
        for block in blocks
    ]
    return {"text": "\n\n".join(parts), "truncated": False}


def _convert_for_preview(
    stored_path: Path, root_dir: Path, item_id: uuid.UUID
) -> Path | None:
    pdf_path = extractor._convert_to_pdf_with_libreoffice(stored_path)
    if pdf_path is None:
        return None

    preview_dir = root_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    final_path = preview_dir / f"{item_id}.pdf"
    tmp_path = preview_dir / f".{item_id}.pdf.tmp"
    # 변환 결과를 임시 파일로 옮긴 뒤 rename - 캐시 디렉터리에 부분 파일이
    # 남지 않도록(다른 요청이 그 사이에 읽어가는 걸 방지).
    shutil.move(pdf_path, tmp_path)
    tmp_path.rename(final_path)
    return final_path


def _preview_pdf_response(path: Path) -> FileResponse:
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"},
    )


def delete_chat_upload_dir(chat_id: uuid.UUID) -> None:
    """채팅방 삭제 시 chat/router.py가 호출 - 해당 chat의 업로드 디렉터리
    전체(원본 + 추출된 이미지)를 한 번에 정리한다."""
    chat_dir = _chat_upload_dir(chat_id)
    shutil.rmtree(chat_dir, ignore_errors=True)
