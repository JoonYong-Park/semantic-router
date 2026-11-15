"""/users/me - 기존 backend/app/users/router.py. 프로필 사진은 저장소의 users/{id}/avatar.png."""

import io

from flask import Blueprint, jsonify, request, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

from app import storage
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


MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_SIZE = 256
Image.MAX_IMAGE_PIXELS = 50_000_000  # 압축 폭탄 방어


def _avatar_key(uid) -> str:
    return f"users/{uid}/avatar.png"


def _process_avatar(content: bytes) -> bytes:
    """Pillow로 실제로 열리는지 확인하고 256x256 PNG로 다시 인코딩한다 (EXIF 제거)."""
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            img = ImageOps.fit(img.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ApiError(400, "이미지 파일이 아니거나 읽을 수 없습니다.") from e
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


@bp.get("/me/avatar")
def get_avatar():
    u = users.get_profile(current_user_id())
    key = _avatar_key(current_user_id())
    if u is None or not u.get("avatar_updated_at") or not storage.exists(key):
        raise ApiError(404, "프로필 사진이 없습니다.")
    resp = send_file(storage.path(key), mimetype="image/png")
    resp.headers["Cache-Control"] = "private, max-age=86400"  # URL에 ?v=가 붙어 바뀌면 URL도 바뀐다
    return resp


@bp.put("/me/avatar")
def upload_avatar():
    _active_user()
    upload = request.files.get("file")
    if upload is None:
        raise ApiError(422, "file 필드가 없습니다.")
    content = upload.read(MAX_AVATAR_BYTES + 1)
    if len(content) > MAX_AVATAR_BYTES:
        raise ApiError(413, f"사진이 너무 큽니다 (최대 {MAX_AVATAR_BYTES // (1024 * 1024)}MB).")
    storage.write(_avatar_key(current_user_id()), _process_avatar(content))
    users.set_avatar_updated(current_user_id(), dynamo.now_iso())
    return jsonify(_user_out(_active_user()))


@bp.delete("/me/avatar")
def delete_avatar():
    _active_user()
    storage.delete(_avatar_key(current_user_id()))
    users.set_avatar_updated(current_user_id(), None)
    return jsonify(_user_out(_active_user()))
