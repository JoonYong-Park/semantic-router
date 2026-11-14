"""/users/me - 기존 backend/app/users/router.py 중 프로필 부분.
아바타(업로드 파일)는 S3 전환과 함께 옮긴다."""

from flask import Blueprint, jsonify, request

from app.auth import current_user_id
from app.db import dynamo, keys
from app.db.repo import users
from app.errors import ApiError

bp = Blueprint("users", __name__, url_prefix="/users")


def _user_out(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"],
            "avatar_updated_at": u.get("avatar_updated_at")}


def _active_user() -> dict:
    u = users.get_profile(current_user_id())
    if u is None or not u.get("is_active", True):
        raise ApiError(404, "사용자를 찾을 수 없습니다.")
    return u


@bp.get("/me")
def get_current_user():
    return jsonify(_user_out(_active_user()))


@bp.patch("/me")
def update_current_user():
    """이름만 수정할 수 있다. 이메일은 계정 식별자라 변경 불가 (기존과 동일)."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name or len(name) > 100:
        raise ApiError(422, "이름을 입력해주세요.")
    _active_user()
    dynamo.update(keys.user_pk(current_user_id()), keys.PROFILE, set_={"name": name})
    return jsonify(_user_out(_active_user()))
