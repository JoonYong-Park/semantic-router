"""채팅 첨부 - 업로드/취소/원본/표/텍스트/PDF 미리보기 (기존 attachments/router.py).

2단계 업로드: 파일을 받으면 저장만 하고 바로 id를 돌려준다("pending"). 추출은
백그라운드에서 하고, 메시지 전송은 이 id만 참조한다(chat/turn.py가 추출 완료를 기다림).

S3 직접 업로드(배포): Lambda 요청 크기 상한(6MB) 때문에 파일 본문은 서버를 거치지 않는다.
  1) POST .../upload-url {filename, size} → presigned POST {url, fields, upload_id}
  2) 브라우저가 S3에 바로 올린다 (키와 크기 상한이 서명에 들어 있다)
  3) POST .../complete {upload_id, filename} → S3에 올라간 크기를 확인하고 첨부를 만든다
버킷이 없는 로컬에서는 1)이 {"mode": "multipart"}를 돌려주고, 프론트는 예전처럼 본문을 보낸다.
"""

import logging
import uuid

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


def check_name_and_size(filename: str, size: int | None) -> str:
    """확장자와 크기를 검사하고 확장자를 돌려준다."""
    ext = files.ext_of(filename)
    if ext not in files.ALL_ALLOWED_EXTS:
        raise ApiError(400, f"지원하지 않는 파일 형식입니다: .{ext or '(확장자 없음)'}")
    if size is not None and size > files.MAX_UPLOAD_BYTES:
        raise ApiError(400, f"파일이 너무 큽니다 (최대 {files.MAX_UPLOAD_BYTES // (1024 * 1024)}MB).")
    return ext


def start_direct_upload(folder: str) -> tuple[dict, int]:
    """1단계: S3 presigned POST를 만든다. (응답, 신고한 크기)."""
    body = request.get_json(silent=True) or {}
    filename = str(body.get("filename") or "")
    try:
        size = int(body.get("size") or 0)
    except (TypeError, ValueError):
        raise ApiError(422, "size가 올바르지 않습니다.")
    ext = check_name_and_size(filename, size)
    fid, key = files.new_file_key(folder, ext)
    post = storage.presigned_post(key, files.MAX_UPLOAD_BYTES)
    if post is None:
        return {"mode": "multipart"}, size
    return {"mode": "s3", "upload_id": fid, "url": post["url"], "fields": post["fields"]}, size


def finish_direct_upload(folder: str) -> tuple[str, str, str, str, int]:
    """3단계: 올라간 파일을 확인한다. (원본 이름, 확장자, id, 키, 크기)."""
    body = request.get_json(silent=True) or {}
    filename = str(body.get("filename") or "")
    try:
        fid = str(uuid.UUID(str(body.get("upload_id"))))
    except ValueError:
        raise ApiError(422, "upload_id가 올바르지 않습니다.")
    ext = check_name_and_size(filename, None)
    key = f"{folder}/{fid}.{ext}"  # files.new_file_key와 같은 규칙 - 다른 위치는 가리킬 수 없다
    size = storage.size_of(key)
    if size is None:
        raise ApiError(400, "업로드된 파일을 찾을 수 없습니다. 다시 올려 주세요.")
    return filename, ext, fid, key, size


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


@bp.post("/<cid>/attachments/upload-url")
def attachment_upload_url(cid):
    owned_conversation(cid)
    resp, _ = start_direct_upload(files.chat_dir(cid))
    return jsonify(resp)


@bp.post("/<cid>/attachments/complete")
def complete_attachment_upload(cid):
    owned_conversation(cid)
    filename, ext, aid, key, size = finish_direct_upload(files.chat_dir(cid))
    existing = conv_repo.get_attachment_in_conv(cid, aid)
    if existing is not None:  # 같은 업로드를 두 번 완료 요청한 경우
        return jsonify(attachment_out(existing))
    att = conv_repo.create_attachment(
        current_user_id(), cid, filename or f"{aid}.{ext}", key,
        files.MIME_BY_EXT.get(ext, "application/octet-stream"), size, aid=aid,
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
