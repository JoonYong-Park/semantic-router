"""개인 지침(personal instruction) 조회/저장/삭제.

conversations.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID 하나에
설정을 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지 로직은 건드리지 않는다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_models import DEMO_USER_ID, UserSettings
from app.schemas import SettingsOut, SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


async def _get_settings_row(session: AsyncSession) -> UserSettings | None:
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == DEMO_USER_ID)
    )
    return result.scalar_one_or_none()


@router.get("", response_model=SettingsOut)
async def get_settings(
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    settings = await _get_settings_row(session)
    return SettingsOut(
        personal_instruction=settings.personal_instruction if settings else None
    )


@router.put("", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    settings = await _get_settings_row(session)
    if settings is None:
        settings = UserSettings(
            user_id=DEMO_USER_ID, personal_instruction=body.personal_instruction
        )
        session.add(settings)
    else:
        settings.personal_instruction = body.personal_instruction
    await session.commit()
    return SettingsOut(personal_instruction=settings.personal_instruction)


@router.delete("/instruction", response_model=SettingsOut)
async def delete_instruction(
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    settings = await _get_settings_row(session)
    if settings is not None:
        settings.personal_instruction = None
        await session.commit()
    return SettingsOut(personal_instruction=None)
