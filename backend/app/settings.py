"""개인 지침(personal instruction) + 다른 AI에서 가져온 메모리 조회/저장/삭제.

conversations.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID 하나에
설정을 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지 로직은 건드리지 않는다.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_models import DEMO_USER_ID, UserSettings
from app.schemas import ImportedMemoryOut, ImportMemoryCreate, SettingsOut, SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


async def _get_settings_row(session: AsyncSession) -> UserSettings | None:
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == DEMO_USER_ID)
    )
    return result.scalar_one_or_none()


def _to_settings_out(settings: UserSettings | None) -> SettingsOut:
    if settings is None:
        return SettingsOut()
    return SettingsOut(
        personal_instruction=settings.personal_instruction,
        imported_memory=settings.imported_memory,
    )


@router.get("", response_model=SettingsOut)
async def get_settings(
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    settings = await _get_settings_row(session)
    return _to_settings_out(settings)


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
    return _to_settings_out(settings)


@router.delete("/instruction", response_model=SettingsOut)
async def delete_instruction(
    session: AsyncSession = Depends(get_session),
) -> SettingsOut:
    settings = await _get_settings_row(session)
    if settings is not None:
        settings.personal_instruction = None
        await session.commit()
    return _to_settings_out(settings)


@router.post("/import", response_model=SettingsOut)
async def import_memory(
    body: ImportMemoryCreate, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    settings = await _get_settings_row(session)
    if settings is None:
        settings = UserSettings(user_id=DEMO_USER_ID, imported_memory=body.text)
        session.add(settings)
    else:
        settings.imported_memory = body.text
    await session.commit()
    return _to_settings_out(settings)


@router.delete("/import", response_model=ImportedMemoryOut)
async def delete_imported_memory(
    session: AsyncSession = Depends(get_session),
) -> ImportedMemoryOut:
    settings = await _get_settings_row(session)
    if settings is not None:
        settings.imported_memory = None
        await session.commit()
    return ImportedMemoryOut(imported_memory=None)
