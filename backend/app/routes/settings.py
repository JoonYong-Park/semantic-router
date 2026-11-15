"""/settings - 기존 backend/app/settings/router.py를 옮긴 것. URL과 응답 형태는 같다."""

from flask import Blueprint, jsonify, request

from app.auth import current_user_id
from app.db.repo import memory, projects, users
from app.errors import ApiError

bp = Blueprint("settings", __name__, url_prefix="/settings")

# UI에 항상 이 순서/전부를 보여주기 위한 고정 카테고리 목록 (기존과 동일)
MEMORY_CATEGORIES = ["나", "주제", "영역", "사람"]


def _settings_out(s: dict) -> dict:
    return {"personal_instruction": s.get("personal_instruction"), "imported_memory": s.get("imported_memory")}


def _topic_out(m: dict) -> dict:
    return {"id": m["id"], "name": m["name"], "summary": m["summary"],
            "details": m.get("details", []), "updated_at": m["updated_at"]}


@bp.get("")
def get_settings():
    return jsonify(_settings_out(users.get_settings(current_user_id())))


@bp.put("")
def update_settings():
    body = request.get_json(silent=True) or {}
    # 기존 exclude_unset과 같다: 바디에 실제로 들어온 필드만 바꾼다.
    if "personal_instruction" not in body:
        return jsonify(_settings_out(users.get_settings(current_user_id())))
    s = users.update_settings(current_user_id(), personal_instruction=body["personal_instruction"])
    return jsonify(_settings_out(s))


@bp.delete("/instruction")
def delete_instruction():
    return jsonify(_settings_out(users.update_settings(current_user_id(), personal_instruction=None)))


@bp.post("/import")
def import_memory():
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("text"), str):
        raise ApiError(422, "text가 필요합니다.")
    return jsonify(_settings_out(users.update_settings(current_user_id(), imported_memory=body["text"])))


@bp.delete("/import")
def delete_imported_memory():
    users.update_settings(current_user_id(), imported_memory=None)
    return jsonify(imported_memory=None)


@bp.get("/memory")
def get_memory_overview():
    uid = current_user_id()
    by_cat: dict[str, list] = {}
    for m in memory.list_user_memory(uid):  # 이미 최신 수정순
        by_cat.setdefault(m["category"], []).append(_topic_out(m))
    global_groups = [{"category": c, "topics": by_cat.get(c, [])} for c in MEMORY_CATEGORIES]
    project_groups = [
        {"project_id": p["id"], "project_name": p["name"],
         "topics": [_topic_out(m) for m in memory.list_project_memory(p["id"])]}
        for p in projects.list_projects(uid)  # 메모리가 없는 프로젝트도 빈 그룹으로
    ]
    return jsonify({"global": global_groups, "projects": project_groups})


@bp.patch("/memory/<memory_id>")
def update_memory_topic(memory_id):
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("summary"), str) or not isinstance(body.get("details"), list):
        raise ApiError(422, "summary와 details가 필요합니다.")
    topic = memory.update_memory_by_id(current_user_id(), memory_id, body["summary"], body["details"])
    if topic is None:
        raise ApiError(404, "메모리를 찾을 수 없습니다.")
    return jsonify(_topic_out(topic))


@bp.delete("/memory/<memory_id>")
def delete_memory_fact(memory_id):
    if not memory.delete_memory_by_id(current_user_id(), memory_id):
        raise ApiError(404, "메모리를 찾을 수 없습니다.")
    return "", 204
