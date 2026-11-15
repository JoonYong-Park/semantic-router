"""채팅 첨부 - 업로드/취소/원본/표/텍스트/PDF 미리보기 (기존 attachments/router.py).

2단계 업로드: 파일을 받으면 저장만 하고 바로 id를 돌려준다("pending"). 추출은
백그라운드에서 하고, 메시지 전송은 이 id만 참조한다(chat/turn.py가 추출 완료를 기다림).
"""

import logging

from flask import Blueprint, jsonify, request

from app import storage
from app.auth import current_user_id
from app.db.repo import conversations as conv_repo
from app.errors import ApiError
from app.routes import file_views
from app.routes.conversations import owned_conversation
from app.serializers import attachment_out
from app.services import files

logger = logging.getLogger("uvicorn.error")

bp = Blueprint("attachments", __name__, url_prefix="/conversations")


def read_upload() -> tuple[str, str, bytes]:
    """multipart의 file 필드를 읽고 형식/크기를 검사한다. (원본 이름, 확장자, 내용)."""
    upload = request.files.get("file")
    if upload is None:
        raise ApiError(422, "file 필드가 없습니다.")
    filename = upload.filename or ""
    ext = files.ext_of(filename)
    if ext not in files.ALL_ALLOWED_EXTS:
        raise ApiError(400, f"지원하지 않는 파일 형식입니다: .{ext or '(확장자 없음)'}")
    content = upload.read(files.MAX_UPLOAD_BYTES + 1)
    if len(content) > files.MAX_UPLOAD_BYTES:
        raise ApiError(400, f"파일이 너무 큽니다 (최대 {files.MAX_UPLOAD_BYTES // (1024 * 1024)}MB).")
    return filename, ext, content


def _process_in_background(cid, item: dict) -> None:
    folder = files.chat_dir(cid)
    fields = files.extract_file(item, folder)
    if not conv_repo.update_attachment(cid, item["id"], **fields):
        # 처리 도중 사용자가 취소함 - 그 사이 새로 만든 변환 결과물만 정리한다.
        files.delete_files({**item, **fields}, folder)


def _attachment_in(cid, aid) -> dict:
    owned_conversation(cid)
    att = conv_repo.get_attachment_in_conv(cid, aid)
    if att is None:
        raise ApiError(404, "첨부파일을 찾을 수 없습니다.")
    return att


@bp.post("/<cid>/attachments")
def upload_attachment(cid):
    owned_conversation(cid)
    filename, ext, content = read_upload()
    aid, key = files.new_file_key(files.chat_dir(cid), ext)
    storage.write(key, content)
    att = conv_repo.create_attachment(
        current_user_id(), cid, filename or f"{aid}.{ext}", key,
        files.MIME_BY_EXT.get(ext, "application/octet-stream"), len(content), aid=aid,
    )
    files.run_in_background(_process_in_background, cid, att)
    return jsonify(attachment_out(att))


@bp.delete("/<cid>/attachments/<aid>")
def cancel_attachment(cid, aid):
    att = _attachment_in(cid, aid)
    if att.get("message_id"):
        raise ApiError(400, "이미 전송된 메시지의 첨부파일은 취소할 수 없습니다.")
    files.delete_files(att, files.chat_dir(cid))
    conv_repo.delete_attachment_in_conv(cid, aid)
    return "", 204


@bp.get("/<cid>/attachments/<aid>/file")
def get_attachment_file(cid, aid):
    return file_views.file_response(_attachment_in(cid, aid), request.args.get("download", "0") not in ("0", ""))


@bp.get("/<cid>/attachments/<aid>/table")
def get_attachment_table(cid, aid):
    return jsonify(file_views.table_response(_attachment_in(cid, aid)))


@bp.get("/<cid>/attachments/<aid>/text")
def get_attachment_text(cid, aid):
    return jsonify(file_views.text_response(_attachment_in(cid, aid)))


@bp.get("/<cid>/attachments/<aid>/preview-pdf")
def get_attachment_preview_pdf(cid, aid):
    return file_views.preview_pdf_response(_attachment_in(cid, aid), files.chat_dir(cid))
