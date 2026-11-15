"""/projects - 프로젝트 폴더(지침 + 참고자료 + 전용 메모리) (기존 projects/router.py).
URL과 응답 형태는 같다. 모든 엔드포인트가 owned_project()로 소유자를 확인한다
(남의 프로젝트는 없는 것과 똑같이 404)."""

from flask import Blueprint, jsonify, request

from app import storage
from app.auth import current_user_id
from app.db.repo import conversations as conv_repo
from app.db.repo import projects as project_repo
from app.errors import ApiError
from app.memory import extract as memory_extract
from app.routes import file_views
from app.routes.attachments import read_upload
from app.serializers import project_file_out, project_out
from app.services import files

bp = Blueprint("projects", __name__, url_prefix="/projects")

COLORS = {"default", "red", "orange", "yellow", "green", "blue", "purple", "pink"}


def owned_project(pid) -> dict:
    project = project_repo.get_project(current_user_id(), pid)
    if project is None:
        raise ApiError(404, "프로젝트를 찾을 수 없습니다.")
    return project


def _project_file(pid, fid) -> dict:
    owned_project(pid)
    # 프로젝트 파티션에서 바로 찾는다 (GSI3는 최종 일관성이라 방금 올린 파일이 안 보일 수 있다).
    for f in project_repo.list_project_files(pid):
        if f["id"] == fid:
            return f
    raise ApiError(404, "참고자료를 찾을 수 없습니다.")


@bp.post("")
def create_project():
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if not isinstance(name, str):
        raise ApiError(422, "name이 필요합니다.")
    return jsonify(project_out(project_repo.create_project(current_user_id(), name, body.get("instruction"))))


@bp.get("")
def list_projects():
    return jsonify([project_out(p) for p in project_repo.list_projects(current_user_id())])


@bp.get("/<pid>")
def get_project(pid):
    return jsonify(project_out(owned_project(pid)))


@bp.patch("/<pid>")
def update_project(pid):
    owned_project(pid)
    body = request.get_json(silent=True) or {}
    if "color" in body and body["color"] not in COLORS:
        raise ApiError(422, "지원하지 않는 색입니다.")
    fields = {k: body[k] for k in ("name", "instruction", "pinned", "sort_order", "color") if k in body}
    return jsonify(project_out(project_repo.update_project(current_user_id(), pid, **fields)))


@bp.delete("/<pid>")
def delete_project(pid):
    """지침, 참고자료, 메모리, 소속 채팅방(메시지, 첨부)까지 전부 지운다."""
    owned_project(pid)
    result = project_repo.delete_project(current_user_id(), pid)
    storage.delete_prefix(files.project_dir(pid))
    for cid in result["conversation_ids"]:
        storage.delete_prefix(files.chat_dir(cid))
    return "", 204


# ------------------------------------------------------------------ 참고자료
def _process_in_background(pid, item: dict) -> None:
    folder = files.project_dir(pid)
    fields = files.extract_file(item, folder)
    if not project_repo.update_project_file(pid, item["sk"], **fields):
        files.delete_files({**item, **fields}, folder)  # 처리 도중 삭제됨


@bp.post("/<pid>/files")
def upload_project_file(pid):
    project = owned_project(pid)
    filename, ext, content = read_upload()
    used = int(project.get("files_total_bytes", 0))
    limit = int(project["max_capacity_bytes"])
    if used + len(content) > limit:
        raise ApiError(413, f"프로젝트 저장 용량을 초과합니다 ({used + len(content)} / {limit} bytes).")
    fid, key = files.new_file_key(files.project_dir(pid), ext)
    storage.write(key, content)
    item = project_repo.add_project_file(
        current_user_id(), pid, filename or f"{fid}.{ext}", key,
        files.MIME_BY_EXT.get(ext, "application/octet-stream"), len(content), fid=fid,
    )
    files.run_in_background(_process_in_background, pid, item)
    return jsonify(project_file_out(item))


@bp.get("/<pid>/files")
def list_project_files(pid):
    project = owned_project(pid)
    items = project_repo.list_project_files(pid)
    used = sum(int(f["size_bytes"]) for f in items)
    limit = int(project["max_capacity_bytes"])
    return jsonify({
        "files": [project_file_out(f) for f in items],
        "used_bytes": used,
        "max_bytes": limit,
        "percent": round(used / limit * 100, 1) if limit else 0.0,
    })


@bp.delete("/<pid>/files/<fid>")
def delete_project_file(pid, fid):
    item = _project_file(pid, fid)
    files.delete_files(item, files.project_dir(pid))
    project_repo.delete_project_file(current_user_id(), fid, item=item)
    return "", 204


@bp.get("/<pid>/files/<fid>/file")
def get_project_file_content(pid, fid):
    return file_views.file_response(_project_file(pid, fid), request.args.get("download", "0") not in ("0", ""))


@bp.get("/<pid>/files/<fid>/table")
def get_project_file_table(pid, fid):
    return jsonify(file_views.table_response(_project_file(pid, fid)))


@bp.get("/<pid>/files/<fid>/text")
def get_project_file_text(pid, fid):
    return jsonify(file_views.text_response(_project_file(pid, fid)))


@bp.get("/<pid>/files/<fid>/preview-pdf")
def get_project_file_preview_pdf(pid, fid):
    return file_views.preview_pdf_response(_project_file(pid, fid), files.project_dir(pid))


# ------------------------------------------------------------------ 메모리
@bp.post("/<pid>/memory/extract-all")
def extract_project_memory(pid):
    owned_project(pid)
    conv_ids = [c["id"] for c in conv_repo.list_project_conversations(pid)]
    files.run_in_background(memory_extract.extract_all_sequentially, current_user_id(), conv_ids)
    return jsonify({"status": "started", "count": len(conv_ids)})
