"""개인 지침(personal instruction) + 다른 AI에서 가져온 메모리 조회/저장/삭제.

conversations.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID 하나에
설정을 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지 로직은 건드리지 않는다.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_models import DEMO_USER_ID, Project, ProjectMemory, UserMemory, UserSettings
from app.schemas import (
    ImportedMemoryOut,
    ImportMemoryCreate,
    MemoryCategoryGroupOut,
    MemoryOverviewOut,
    MemoryTopicOut,
    MemoryTopicUpdate,
    ProjectMemoryGroupOut,
    SettingsOut,
    SettingsUpdate,
)

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
    # exclude_unset: 요청 바디에 실제로 포함된 필드만 골라서 적용 (필드가 늘어나도
    # 안 보낸 필드를 None으로 덮어쓰지 않도록 안전하게 확장 가능한 패턴).
    updates = body.model_dump(exclude_unset=True)
    settings = await _get_settings_row(session)
    if settings is None:
        settings = UserSettings(user_id=DEMO_USER_ID, **updates)
        session.add(settings)
    else:
        for key, value in updates.items():
            setattr(settings, key, value)
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


# --- 자동 메모리 조회(전역: 카테고리별 토픽 + 프로젝트별 토픽) ---

# UI에 항상 이 순서/전부를 보여주기 위한 고정 카테고리 목록(해당 카테고리에
# 토픽이 하나도 없어도 빈 그룹으로 나오게).
_MEMORY_CATEGORIES = ["나", "주제", "영역", "사람"]


def _to_topic_out(m: UserMemory | ProjectMemory) -> MemoryTopicOut:
    return MemoryTopicOut(
        id=m.id,
        name=m.name,
        summary=m.summary,
        details=json.loads(m.details) if m.details else [],
        updated_at=m.updated_at,
    )


@router.get("/memory", response_model=MemoryOverviewOut)
async def get_memory_overview(
    session: AsyncSession = Depends(get_session),
) -> MemoryOverviewOut:
    global_result = await session.execute(
        select(UserMemory)
        .where(UserMemory.user_id == DEMO_USER_ID)
        .order_by(UserMemory.updated_at.desc())
    )
    topics_by_category: dict[str, list[MemoryTopicOut]] = {}
    for m in global_result.scalars().all():
        topics_by_category.setdefault(m.category, []).append(_to_topic_out(m))

    global_groups = [
        MemoryCategoryGroupOut(category=category, topics=topics_by_category.get(category, []))
        for category in _MEMORY_CATEGORIES
    ]

    # 메모리가 하나도 없는 프로젝트도 목록에 나오게, 프로젝트 전체를 먼저
    # 조회하고 각 프로젝트별로 메모리를 채워 넣는다(outer join 대신 이 방식을
    # 쓴 이유: 다른 모델들과 마찬가지로 이 프로젝트는 ORM relationship을
    # 안 쓰는 컨벤션이라 - conversations.py의 attachments 처리와 동일한 패턴).
    projects_result = await session.execute(
        select(Project).where(Project.user_id == DEMO_USER_ID).order_by(Project.created_at)
    )
    projects = list(projects_result.scalars().all())

    memory_result = await session.execute(
        select(ProjectMemory).order_by(ProjectMemory.updated_at.desc())
    )
    topics_by_project: dict[UUID, list[MemoryTopicOut]] = {}
    for m in memory_result.scalars().all():
        topics_by_project.setdefault(m.project_id, []).append(_to_topic_out(m))

    project_groups = [
        ProjectMemoryGroupOut(
            project_id=p.id,
            project_name=p.name,
            topics=topics_by_project.get(p.id, []),
        )
        for p in projects
    ]

    return MemoryOverviewOut.model_validate(
        {"global": global_groups, "projects": project_groups}
    )


@router.patch("/memory/{memory_id}", response_model=MemoryTopicOut)
async def update_memory_topic(
    memory_id: UUID,
    payload: MemoryTopicUpdate,
    session: AsyncSession = Depends(get_session),
) -> MemoryTopicOut:
    # delete_memory_fact와 동일하게, 전역/프로젝트 중 어느 쪽 id인지 클라이언트가
    # 구분해서 보낼 필요 없게 양쪽을 순서대로 조회한다.
    topic: UserMemory | ProjectMemory | None = await session.get(UserMemory, memory_id)
    if topic is None:
        topic = await session.get(ProjectMemory, memory_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="메모리를 찾을 수 없습니다.")

    topic.summary = payload.summary
    topic.details = json.dumps(payload.details, ensure_ascii=False)
    await session.commit()
    await session.refresh(topic)
    return _to_topic_out(topic)


@router.delete("/memory/{memory_id}", status_code=204, response_model=None)
async def delete_memory_fact(
    memory_id: UUID, session: AsyncSession = Depends(get_session)
) -> None:
    # 전역/프로젝트 메모리 중 어느 쪽 id인지 클라이언트가 구분해서 보낼 필요
    # 없게, 양쪽 테이블을 순서대로 조회해서 있는 쪽에서 지운다.
    global_topic = await session.get(UserMemory, memory_id)
    if global_topic is not None:
        await session.delete(global_topic)
        await session.commit()
        return

    project_topic = await session.get(ProjectMemory, memory_id)
    if project_topic is not None:
        await session.delete(project_topic)
        await session.commit()
        return

    raise HTTPException(status_code=404, detail="메모리를 찾을 수 없습니다.")
