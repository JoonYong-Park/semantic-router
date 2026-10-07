"""현재 사용자 조회. 로그인이 아직 없어서 시드된 고정 사용자(DEMO_USER_ID)를
"현재 사용자"로 돌려준다. 로그인이 붙으면 이 함수가 세션/토큰에서 꺼낸 사용자를
돌려주도록 바꾸면 되고, 화면(사이드바 사용자 메뉴, 계정 설정)은 그대로 쓴다."""

import asyncio
import io
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.models import DEMO_USER_ID, User
from app.schemas import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

USERS_ROOT = Path(os.environ.get("UPLOAD_DIR", "/app/uploads")) / "users"
MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 원본 업로드 상한
AVATAR_SIZE = 256  # 정사각형으로 잘라 이 크기로 줄여 저장한다
Image.MAX_IMAGE_PIXELS = 50_000_000  # 압축 폭탄(수억 픽셀짜리 이미지) 방어


def _avatar_path(user_id) -> Path:
    return USERS_ROOT / str(user_id) / "avatar.png"


def _process_avatar(content: bytes) -> bytes:
    """업로드 원본을 검증하고 256x256 PNG로 다시 인코딩한다. 확장자/Content-Type은
    믿지 않고 Pillow가 실제로 열리는지로 판단하며, 재인코딩하므로 EXIF(위치 정보 등)와
    이상한 페이로드가 저장되지 않는다. CPU 작업이라 to_thread에서 호출한다."""
    try:
        with Image.open(io.BytesIO(content)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)  # 폰 사진 회전 반영
            img = ImageOps.fit(img.convert("RGBA"), (AVATAR_SIZE, AVATAR_SIZE))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ValueError("이미지 파일이 아니거나 읽을 수 없습니다.") from e
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


@router.get("/me", response_model=UserOut)
async def get_current_user(session: AsyncSession = Depends(get_session)) -> User:
    user = await session.get(User, DEMO_USER_ID)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return user


@router.patch("/me", response_model=UserOut)
async def update_current_user(
    body: UserUpdate, session: AsyncSession = Depends(get_session)
) -> User:
    """이름만 수정할 수 있다. 이메일은 계정 식별자라 변경 불가."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="이름을 입력해주세요.")
    user = await session.get(User, DEMO_USER_ID)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    user.name = name
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/me/avatar")
async def get_avatar(session: AsyncSession = Depends(get_session)) -> FileResponse:
    user = await session.get(User, DEMO_USER_ID)
    path = _avatar_path(DEMO_USER_ID)
    if user is None or user.avatar_updated_at is None or not path.exists():
        raise HTTPException(status_code=404, detail="프로필 사진이 없습니다.")
    # URL에 ?v=<avatar_updated_at>이 붙어 사진이 바뀌면 URL도 바뀌므로 오래 캐시해도 된다.
    return FileResponse(
        path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"}
    )


@router.put("/me/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile, session: AsyncSession = Depends(get_session)
) -> User:
    user = await session.get(User, DEMO_USER_ID)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    content = await file.read(MAX_AVATAR_BYTES + 1)
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"사진이 너무 큽니다 (최대 {MAX_AVATAR_BYTES // (1024 * 1024)}MB).",
        )
    try:
        png = await asyncio.to_thread(_process_avatar, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    path = _avatar_path(DEMO_USER_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 임시 파일에 쓰고 교체해서, 쓰는 도중에 GET이 반쯤 쓰인 파일을 읽지 않게 한다.
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(path)

    user.avatar_updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserOut)
async def delete_avatar(session: AsyncSession = Depends(get_session)) -> User:
    user = await session.get(User, DEMO_USER_ID)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    _avatar_path(DEMO_USER_ID).unlink(missing_ok=True)
    user.avatar_updated_at = None
    await session.commit()
    await session.refresh(user)
    return user
