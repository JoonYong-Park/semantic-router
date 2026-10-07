"""프로젝트 폴더(지침 + 참고자료 + 전용 메모리) CRUD.

파일 저장/추출은 채팅 첨부(attachments/service.py)와 완전히 동일한 유틸리티를 그대로
재사용한다 - _save_uploaded_file/_delete_stored_files/_extract_sync는 이미
root_dir 파라미터로 일반화돼 있어서 chat_id 대신 project_id 기준 경로만
넘기면 그대로 동작한다. 채팅 첨부 쪽 코드(attachments/service.py)는 이 파일에서
건드리지 않는다 - DB에 쓰는 부분(_process_attachment_background 등)만
Attachment 모델에 결합돼 있어서, 그 부분만 ProjectFile용으로 따로 뒀다.
"""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attachments import service as attachment_service
from app.memory import extract as memory_extract
from app.core.db import SessionLocal, get_session
from app.core.models import DEMO_USER_ID, Conversation, Project, ProjectFile
from app.schemas import (
    ProjectCreate,
    ProjectFileOut,
    ProjectFilesOut,
    ProjectOut,
    ProjectUpdate,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_upload_dir(project_id: uuid.UUID) -> Path:
    return attachment_service.PROJECTS_ROOT / str(project_id)


def _delete_project_upload_dir(project_id: uuid.UUID) -> None:
    """프로젝트 삭제 시 호출 - 해당 프로젝트의 업로드 디렉터리 전체를 정리한다
    (attachments/service.py의 delete_chat_upload_dir와 동일한 패턴)."""
    shutil.rmtree(_project_upload_dir(project_id), ignore_errors=True)


async def _process_project_file_background(
    file_id: uuid.UUID,
    ext: str,
    stored_path: Path,
    upload_dir: Path,
    mime_type: str,
    filename: str,
) -> None:
    """attachments/service.py의 _process_attachment_background와 동일한 패턴이지만
    Attachment가 아니라 ProjectFile에 결과를 쓴다 - 추출 로직 본체(_extract_sync)는
    그대로 재사용하고, DB에 쓰는 부분만 모델이 달라서 따로 둔다."""
    try:
        final_stored_path, final_mime_type, extracted_text, extraction_status = (
            await asyncio.to_thread(
                attachment_service._extract_sync,
                ext, stored_path, upload_dir, mime_type, filename,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("프로젝트 참고자료 백그라운드 처리 실패 (%s): %s", filename, exc, exc_info=True)
        final_stored_path, final_mime_type, extracted_text, extraction_status = (
            stored_path, mime_type, None, "failed",
        )

    async with SessionLocal() as session:
        project_file = await session.get(ProjectFile, file_id)
        if project_file is None:
            # 처리 도중 파일이 삭제됨 - 새로 만들어진 변환 결과물만 정리.
            if final_stored_path != stored_path:
                final_stored_path.unlink(missing_ok=True)
            return
        project_file.stored_path = str(final_stored_path)
        project_file.mime_type = final_mime_type
        project_file.extracted_text = extracted_text
        project_file.extraction_status = extraction_status
        await session.commit()


async def _get_owned_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    """현재 사용자의 프로젝트만 돌려준다. 없거나 남의 프로젝트면 똑같이 404 -
    남의 프로젝트 id가 존재한다는 사실도 드러내지 않기 위함. 프로젝트를 id로
    다루는 모든 엔드포인트(참고자료/메모리 포함)가 이 함수를 거친다."""
    project = await session.get(Project, project_id)
    if project is None or project.user_id != DEMO_USER_ID:
        raise HTTPException(status_code=404, detail="프로젝트를 찾을 수 없습니다.")
    return project


@router.post("", response_model=ProjectOut)
async def create_project(
    body: ProjectCreate, session: AsyncSession = Depends(get_session)
) -> Project:
    project = Project(user_id=DEMO_USER_ID, name=body.name, instruction=body.instruction)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[Project]:
    result = await session.execute(
        select(Project).where(Project.user_id == DEMO_USER_ID).order_by(Project.created_at)
    )
    return list(result.scalars().all())


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Project:
    project = await _get_owned_project(session, project_id)
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> Project:
    project = await _get_owned_project(session, project_id)
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(project, key, value)
    await session.commit()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204, response_model=None)
async def delete_project(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> None:
    project = await _get_owned_project(session, project_id)

    # 프로젝트를 지우면 지침(Project 행 자체)/참고자료(project_files)/메모리
    # (project_memory)뿐 아니라 소속 채팅방까지 전부 지워진다 - conversations.
    # project_id가 ON DELETE CASCADE라 DB 행 정리는 FK가 알아서 하지만, 채팅
    # 업로드 디렉터리(uploads/chats/{chat_id})는 DB가 못 지워주니 미리 대상
    # conversation_id를 조회해뒀다가 커밋 후 직접 지운다(일반 채팅 삭제
    # 엔드포인트가 하는 것과 동일한 정리).
    conv_result = await session.execute(
        select(Conversation.id).where(Conversation.project_id == project_id)
    )
    conversation_ids = [row[0] for row in conv_result.all()]

    await session.execute(delete(Project).where(Project.id == project_id))
    await session.commit()

    _delete_project_upload_dir(project_id)
    for conv_id in conversation_ids:
        attachment_service.delete_chat_upload_dir(conv_id)


@router.post("/{project_id}/files", response_model=ProjectFileOut)
async def upload_project_file(
    project_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
) -> ProjectFile:
    project = await _get_owned_project(session, project_id)

    ext = attachment_service._ext_of(file.filename or "")
    if ext not in attachment_service.ALL_ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 파일 형식입니다: .{ext or '(확장자 없음)'}",
        )

    content = await file.read()
    if len(content) > attachment_service.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"파일이 너무 큽니다 (최대 {attachment_service.MAX_UPLOAD_BYTES // (1024 * 1024)}MB).",
        )

    # 용량 사전 체크 - "현재 사용량 합 조회 후 비교"만 한다. 동시에 같은
    # 프로젝트에 여러 파일이 업로드되면 이 체크 사이에 레이스 컨디션으로
    # 상한을 살짝 넘길 수 있는데, 지금 규모에서 DB 행 잠금(FOR UPDATE)까지
    # 넣는 건 오버엔지니어링이라고 판단해 뺐다(요청 스펙에 명시된 결정).
    used_result = await session.execute(
        select(func.coalesce(func.sum(ProjectFile.size_bytes), 0)).where(
            ProjectFile.project_id == project_id
        )
    )
    used_bytes = used_result.scalar_one()
    if used_bytes + len(content) > project.max_capacity_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"프로젝트 저장 용량을 초과합니다 "
                f"({used_bytes + len(content)} / {project.max_capacity_bytes} bytes)."
            ),
        )

    upload_dir = _project_upload_dir(project_id)
    file_uuid, stored_path = attachment_service._save_uploaded_file(upload_dir, ext, content)

    mime_type = attachment_service.MIME_BY_EXT.get(ext, "application/octet-stream")
    filename = file.filename or f"{file_uuid}.{ext}"

    project_file = ProjectFile(
        id=file_uuid,
        project_id=project_id,
        original_name=filename,
        stored_path=str(stored_path),
        mime_type=mime_type,
        size_bytes=len(content),
        extracted_text=None,
        extraction_status="pending",
    )
    session.add(project_file)
    await session.commit()
    await session.refresh(project_file)

    asyncio.create_task(
        _process_project_file_background(
            project_file.id, ext, stored_path, upload_dir, mime_type, filename
        )
    )

    return project_file


@router.get("/{project_id}/files", response_model=ProjectFilesOut)
async def list_project_files(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ProjectFilesOut:
    project = await _get_owned_project(session, project_id)

    result = await session.execute(
        select(ProjectFile)
        .where(ProjectFile.project_id == project_id)
        .order_by(ProjectFile.created_at)
    )
    files = list(result.scalars().all())
    used_bytes = sum(f.size_bytes for f in files)
    percent = (
        round(used_bytes / project.max_capacity_bytes * 100, 1)
        if project.max_capacity_bytes
        else 0.0
    )
    return ProjectFilesOut(
        files=[ProjectFileOut.model_validate(f) for f in files],
        used_bytes=used_bytes,
        max_bytes=project.max_capacity_bytes,
        percent=percent,
    )


@router.delete("/{project_id}/files/{file_id}", status_code=204, response_model=None)
async def delete_project_file(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    await _get_owned_project(session, project_id)
    project_file = await session.get(ProjectFile, file_id)
    if project_file is None or project_file.project_id != project_id:
        raise HTTPException(status_code=404, detail="참고자료를 찾을 수 없습니다.")

    # 유예기간/고아 정리 개념 없음 - 요청 즉시 디스크+DB에서 지운다(스펙 명시).
    attachment_service._delete_stored_files(
        _project_upload_dir(project_id),
        project_file.id,
        project_file.original_name,
        project_file.stored_path,
        project_file.extracted_text,
    )
    await session.delete(project_file)
    await session.commit()


# --- 참고자료 미리보기(뷰어) ---
#
# 채팅 첨부 뷰어(attachments/service.py)와 완전히 동일한 4종 엔드포인트(file/table/
# text/preview-pdf)를 그대로 제공한다. 표/텍스트 파싱, PDF 변환, 경로 검증,
# 캐시 락 등 실제 로직은 attachments/service.py의 (모듈 내부용) 헬퍼를 그대로
# 재사용한다 - ProjectFile이 Attachment와 같은 필드(stored_path/mime_type/
# extracted_text 등)를 가지고 있어서 별도 어댑터 없이 그대로 동작한다.


async def _get_project_file_or_404(
    project_id: uuid.UUID, file_id: uuid.UUID, session: AsyncSession
) -> ProjectFile:
    await _get_owned_project(session, project_id)
    project_file = await session.get(ProjectFile, file_id)
    if project_file is None or project_file.project_id != project_id:
        raise HTTPException(status_code=404, detail="참고자료를 찾을 수 없습니다.")
    return project_file


@router.get("/{project_id}/files/{file_id}/file")
async def get_project_file_content(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    download: int = 0,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    project_file = await _get_project_file_or_404(project_id, file_id, session)
    path = attachment_service._resolve_under_upload_root(Path(project_file.stored_path))
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    is_image = project_file.mime_type.startswith("image/")
    is_pdf = project_file.mime_type == "application/pdf"
    inline = (is_image or is_pdf) and not download

    download_name = project_file.original_name
    stored_ext = path.suffix.lower()
    if "." in download_name:
        base, _, name_ext = download_name.rpartition(".")
        if stored_ext and f".{name_ext.lower()}" != stored_ext:
            download_name = f"{base}{stored_ext}"

    headers = {
        "Content-Disposition": attachment_service._content_disposition(download_name, inline),
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(path, media_type=project_file.mime_type, headers=headers)


@router.get("/{project_id}/files/{file_id}/table")
async def get_project_file_table(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project_file = await _get_project_file_or_404(project_id, file_id, session)
    ext = attachment_service._ext_of(project_file.original_name)
    path = attachment_service._resolve_under_upload_root(Path(project_file.stored_path))
    if not path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    if ext in attachment_service.CSV_EXTS:
        sheet = await asyncio.to_thread(
            attachment_service._csv_table, path, project_file.original_name
        )
        return {"sheets": [sheet]}
    if ext in attachment_service.XLSX_EXTS:
        sheets = await asyncio.to_thread(attachment_service._xlsx_tables, path)
        return {"sheets": sheets}
    raise HTTPException(status_code=400, detail="표 미리보기를 지원하지 않는 형식입니다.")


@router.get("/{project_id}/files/{file_id}/text")
async def get_project_file_text(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project_file = await _get_project_file_or_404(project_id, file_id, session)
    ext = attachment_service._ext_of(project_file.original_name)

    if ext in attachment_service.HWP_EXTS:
        if project_file.extraction_status == "pending":
            raise HTTPException(
                status_code=409, detail="파일을 아직 처리 중입니다. 잠시 후 다시 시도하세요."
            )
        return attachment_service._hwp_text_preview(project_file)

    if ext in attachment_service.TEXT_PLAIN_EXTS:
        path = attachment_service._resolve_under_upload_root(Path(project_file.stored_path))
        if not path.exists():
            raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
        return await asyncio.to_thread(attachment_service._plain_text_preview, path)

    raise HTTPException(status_code=400, detail="텍스트 미리보기를 지원하지 않는 형식입니다.")


@router.get("/{project_id}/files/{file_id}/preview-pdf")
async def get_project_file_preview_pdf(
    project_id: uuid.UUID,
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    project_file = await _get_project_file_or_404(project_id, file_id, session)
    ext = attachment_service._ext_of(project_file.original_name)
    if ext not in (attachment_service.DOCX_EXTS | attachment_service.PPTX_EXTS):
        raise HTTPException(status_code=400, detail="미리보기 변환을 지원하지 않는 형식입니다.")

    upload_dir = _project_upload_dir(project_id)
    cache_path = attachment_service._preview_cache_path(upload_dir, file_id)
    if cache_path.exists():
        return attachment_service._preview_pdf_response(cache_path)

    stored_path = attachment_service._resolve_under_upload_root(Path(project_file.stored_path))
    if not stored_path.exists():
        raise HTTPException(status_code=404, detail="원본 파일을 찾을 수 없습니다.")

    # attachments/service.py와 동일한 attachment_id별 락 딕셔너리를 그대로 공유한다 -
    # UUID는 테이블과 무관하게 전역에서 유일하므로 채팅 첨부/참고자료가 같은
    # id를 가질 일이 없어 안전하다.
    lock = await attachment_service._get_preview_lock(file_id)
    async with lock:
        if cache_path.exists():
            return attachment_service._preview_pdf_response(cache_path)

        try:
            result_path = await asyncio.wait_for(
                asyncio.to_thread(
                    attachment_service._convert_for_preview, stored_path, upload_dir, file_id
                ),
                timeout=attachment_service.PREVIEW_CONVERT_TIMEOUT,
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

    return attachment_service._preview_pdf_response(result_path)


@router.post("/{project_id}/memory/extract-all")
async def extract_project_memory(
    project_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    project = await _get_owned_project(session, project_id)

    result = await session.execute(
        select(Conversation.id).where(Conversation.project_id == project_id)
    )
    conv_ids = [row[0] for row in result.all()]

    # memory/extract.py의 _extraction_lock을 그대로 공유 - 전역 수동/자동 트리거와
    # 겹쳐도 같은 대화방을 동시에 처리하지 않는다(memory/extract.py 참고).
    asyncio.create_task(memory_extract._extract_all_sequentially(conv_ids))

    return {"status": "started", "count": len(conv_ids)}
