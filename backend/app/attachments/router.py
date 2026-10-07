"""채팅 첨부파일 엔드포인트 - 업로드/취소/원본/표/텍스트/PDF 미리보기."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.models import Attachment, Conversation
from app.schemas import AttachmentOut
from app.attachments.service import (
    ALL_ALLOWED_EXTS,
    _chat_upload_dir,
    _content_disposition,
    _convert_for_preview,
    CSV_EXTS,
    _csv_table,
    _delete_attachment_files,
    DOCX_EXTS,
    _ext_of,
    _get_attachment_or_404,
    _get_preview_lock,
    HWP_EXTS,
    _hwp_text_preview,
    MAX_UPLOAD_BYTES,
    MIME_BY_EXT,
    _plain_text_preview,
    PPTX_EXTS,
    _preview_cache_path,
    PREVIEW_CONVERT_TIMEOUT,
    _preview_pdf_response,
    _process_attachment_background,
    _resolve_under_upload_root,
    _save_uploaded_file,
    TEXT_PLAIN_EXTS,
    XLSX_EXTS,
    _xlsx_tables,
)

logger = logging.getLogger("uvicorn.error")


router = APIRouter(prefix="/conversations", tags=["attachments"])


@router.post("/{conversation_id}/attachments", response_model=AttachmentOut)
async def upload_attachment(
    conversation_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> Attachment:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")

    ext = _ext_of(file.filename or "")
    if ext not in ALL_ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: .{ext or '(확장자 없음)'}",
        )

    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB).",
        )

    upload_dir = _chat_upload_dir(conversation_id)
    file_uuid, stored_path = _save_uploaded_file(upload_dir, ext, content)

    mime_type = MIME_BY_EXT.get(ext, "application/octet-stream")
    filename = file.filename or f"{file_uuid}.{ext}"

    # 추출/변환(LibreOffice 등 느릴 수 있는 작업 포함)은 백그라운드로 미루고,
    # 응답은 파일 저장이 끝나는 즉시 돌려준다 - attachment_id는 이 시점부터
    # 이미 유효해서 프론트가 바로 전송에 참조할 수 있다("pending" 상태로).
    attachment = Attachment(
        id=file_uuid,
        chat_id=conversation_id,
        message_id=None,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=mime_type,
        size_bytes=len(content),
        extracted_text=None,
        extraction_status="pending",
    )
    session.add(attachment)
    await session.commit()
    await session.refresh(attachment)

    asyncio.create_task(
        _process_attachment_background(
            attachment.id, ext, stored_path, upload_dir, mime_type, filename
        )
    )

    return attachment


@router.delete(
    "/{conversation_id}/attachments/{attachment_id}",
    status_code=204,
    response_model=None,
)
async def cancel_attachment(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    attachment = await session.get(Attachment, attachment_id)
    if attachment is None or attachment.chat_id != conversation_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    if attachment.message_id is not None:
        raise HTTPException(
            status_code=400, detail="이미 전송된 메시지의 첨부파일은 취소할 수 없습니다."
        )

    _delete_attachment_files(attachment)
    await session.delete(attachment)
    await session.commit()


@router.get("/{conversation_id}/attachments/{attachment_id}/file")
async def get_attachment_file(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    download: int = 0,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    attachment = await _get_attachment_or_404(conversation_id, attachment_id, session)
    path = _resolve_under_upload_root(Path(attachment.stored_path))
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    is_image = attachment.mime_type.startswith("image/")
    is_pdf = attachment.mime_type == "application/pdf"
    inline = (is_image or is_pdf) and not download

    # 이미지는 업로드 시점에 PNG로 변환 저장되므로, 다운로드 파일명 확장자를
    # 실제 저장 확장자에 맞춰준다 (원본이 .jpg여도 실제로는 png 데이터라서).
    download_name = attachment.original_name
    stored_ext = path.suffix.lower()
    if "." in download_name:
        base, _, name_ext = download_name.rpartition(".")
        if stored_ext and f".{name_ext.lower()}" != stored_ext:
            download_name = f"{base}{stored_ext}"

    headers = {
        "Content-Disposition": _content_disposition(download_name, inline),
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(path, media_type=attachment.mime_type, headers=headers)


@router.get("/{conversation_id}/attachments/{attachment_id}/table")
async def get_attachment_table(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    attachment = await _get_attachment_or_404(conversation_id, attachment_id, session)
    ext = _ext_of(attachment.original_name)
    path = _resolve_under_upload_root(Path(attachment.stored_path))
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    if ext in CSV_EXTS:
        sheet = await asyncio.to_thread(_csv_table, path, attachment.original_name)
        return {"sheets": [sheet]}
    if ext in XLSX_EXTS:
        sheets = await asyncio.to_thread(_xlsx_tables, path)
        return {"sheets": sheets}
    raise HTTPException(status_code=400, detail="표 미리보기를 지원하지 않는 형식입니다.")


@router.get("/{conversation_id}/attachments/{attachment_id}/text")
async def get_attachment_text(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    attachment = await _get_attachment_or_404(conversation_id, attachment_id, session)
    ext = _ext_of(attachment.original_name)

    if ext in HWP_EXTS:
        if attachment.extraction_status == "pending":
            # HWP는 원본 재파싱이 아니라 백그라운드 처리가 채워주는
            # extracted_text에 의존하므로, 아직 처리 중이면 "추출 실패"와는
            # 구분되는 안내를 준다.
            raise HTTPException(status_code=409, detail="파일을 아직 처리 중입니다. 잠시 후 다시 시도하세요.")
        return _hwp_text_preview(attachment)

    if ext in TEXT_PLAIN_EXTS:
        path = _resolve_under_upload_root(Path(attachment.stored_path))
        if not path.exists():
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        return await asyncio.to_thread(_plain_text_preview, path)

    raise HTTPException(status_code=400, detail="텍스트 미리보기를 지원하지 않는 형식입니다.")


@router.get("/{conversation_id}/attachments/{attachment_id}/preview-pdf")
async def get_attachment_preview_pdf(
    conversation_id: uuid.UUID,
    attachment_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    attachment = await _get_attachment_or_404(conversation_id, attachment_id, session)
    ext = _ext_of(attachment.original_name)
    if ext not in (DOCX_EXTS | PPTX_EXTS):
        raise HTTPException(status_code=400, detail="미리보기 변환을 지원하지 않는 형식입니다.")

    upload_dir = _chat_upload_dir(conversation_id)
    cache_path = _preview_cache_path(upload_dir, attachment_id)
    if cache_path.exists():
        return _preview_pdf_response(cache_path)

    stored_path = _resolve_under_upload_root(Path(attachment.stored_path))
    if not stored_path.exists():
        raise HTTPException(status_code=404, detail="원본 파일을 찾을 수 없습니다.")

    lock = await _get_preview_lock(attachment_id)
    async with lock:
        # 락 대기 중 다른 요청이 이미 변환을 끝냈을 수 있음
        if cache_path.exists():
            return _preview_pdf_response(cache_path)

        try:
            result_path = await asyncio.wait_for(
                asyncio.to_thread(_convert_for_preview, stored_path, upload_dir, attachment_id),
                timeout=PREVIEW_CONVERT_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            raise HTTPException(
                status_code=422,
                detail="미리보기 변환 시간이 초과되었습니다. 다운로드해서 확인하세요.",
            ) from exc

        if result_path is None:
            raise HTTPException(
                status_code=422,
                detail="미리보기를 만들 수 없습니다. 다운로드해서 확인하세요.",
            )

    return _preview_pdf_response(result_path)
