"""자동 메모리 추출 - 대화에서 사용자에 대한 사실을 뽑아 UserMemory로 쌓는다.

chat/router.py, settings/router.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID
하나에 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지/개인 지침/가져오기
로직은 건드리지 않는다.

하루 2회(07:00, 12:30 KST) 자동 실행되는 스케줄 루프(scheduled_extraction_loop)와
프론트 버튼으로 트리거하는 수동 실행(/memory/extract-all)이 공존한다. 트리거
방식은 서로 완전히 독립적이고(하나가 없어져도 다른 쪽 동작에 영향 없음), 실행
로직(_extract_all_sequentially)만 공유하면서 asyncio.Lock으로 동시 실행만
막는다 - 두 트리거가 우연히 겹쳐도 같은 대화방의 UserMemory를 동시에
지웠다 쓰는 경쟁 상태가 나지 않게. Celery/Redis 없이 asyncio로 충분한
데모 규모라 판단해 attachments/service.py의 orphan_cleanup_loop와 동일한 패턴을 썼다.
수동 트리거는 나중에 제거될 예정이라 그 전까지는 최대한 손대지 않았다."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.llm.client import call_model
from app.core.db import SessionLocal
from app.core.models import DEMO_USER_ID, Conversation, Message, ProjectMemory, UserMemory
from app.routing.catalog import ModelSpec

logger = logging.getLogger("uvicorn.error")


KST = ZoneInfo("Asia/Seoul")


# 하루 중 자동 추출을 돌릴 시각(KST, 24시간제) - 필요하면 이 목록만 바꾸면 됨.
SCHEDULED_TIMES = [(7, 0), (12, 30)]


# 9개 모델 카탈로그에 없는 모델이라 ModelSpec을 즉석에서 만들어 쓴다
# (요약 작업이라 품질보다 비용/속도가 중요해서 작은 모델을 고정으로 지정).
MEMORY_MODEL = ModelSpec(
    model_id="gpt-4o-mini",
    company="openai",
    size="small",
    display_name="GPT-4o mini",
)


async def _extract_memory_for_conversation(user_id: UUID, conv_id: UUID) -> None:
    """대화 하나에서 이전에 요약한 지점 이후의 새 메시지만 뽑아 메모리를 갱신한다.
    스트리밍 완료 후 백그라운드로 도는 _generate_and_save_title과 동일한 패턴
    (Celery/Redis 없이 asyncio.create_task로 충분한 규모라 이렇게 처리).
    """
    async with SessionLocal() as session:
        conv = await session.get(Conversation, conv_id)
        if conv is None:
            return

        query = (
            select(Message)
            .where(Message.conversation_id == conv_id)
            .order_by(Message.created_at.asc())
        )
        if conv.last_extracted_message_id is not None:
            last_msg = await session.get(Message, conv.last_extracted_message_id)
            if last_msg is not None:
                query = query.where(Message.created_at > last_msg.created_at)

        result = await session.execute(query)
        new_messages = result.scalars().all()
        if not new_messages:
            return

        # conversation.project_id 유무로 저장 대상만 분기한다(대체가 아니라
        # 이 대화방이 속한 쪽 하나에만 쓰는 것) - conv를 함수 맨 위에서 이미
        # 통째로 로드해뒀으므로 project_id 확인에 추가 쿼리가 필요 없다.
        # existing_list는 매번 이 시점에 DB에서 새로 읽으므로, 사용자가 메모리
        # 화면에서 직접 수정한 내용이 있어도 다음 추출 시 그 최신 상태를 그대로
        # "기존 메모리"로 보고 반영한다 (수동 편집이 다음 추출에 덮어써지지 않음).
        is_project = conv.project_id is not None
        if is_project:
            existing_result = await session.execute(
                select(ProjectMemory).where(ProjectMemory.project_id == conv.project_id)
            )
            existing_rows = existing_result.scalars().all()
            existing_topics = [
                {
                    "name": m.name,
                    "summary": m.summary,
                    "details": json.loads(m.details) if m.details else [],
                }
                for m in existing_rows
            ]
        else:
            mem_result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == user_id)
            )
            existing_rows = mem_result.scalars().all()
            existing_topics = [
                {
                    "category": m.category,
                    "name": m.name,
                    "summary": m.summary,
                    "details": json.loads(m.details) if m.details else [],
                }
                for m in existing_rows
            ]

        existing_json = json.dumps({"topics": existing_topics}, ensure_ascii=False)
        messages_text = "\n".join(f"{m.role}: {m.content}" for m in new_messages)

        if is_project:
            category_rule = ""
            format_example = (
                '{"topics": [{"name": "토픽 이름", "summary": "한 줄 요약", '
                '"details": ["세부 사실1", "세부 사실2"]}]}'
            )
        else:
            category_rule = (
                '- 각 토픽은 category 필드를 "나" | "주제" | "영역" | "사람" 중 '
                "하나로 분류해:\n"
                "  - \"나\": 사용자 본인에 대한 프로필 정보(직업, 소속, 거주/근무 "
                "지역, 선호도 등 사용자 자신에게만 해당하는 사실). 이 카테고리는 "
                "전체를 통틀어 토픽을 정확히 1개(Profile)만 유지해야 하고, 새로운 "
                "'나' 관련 사실은 기존 '나' 토픽 하나에 계속 병합해 "
                "(절대 2개 이상 만들지 마)\n"
                "  - \"주제\": 사용자가 관심 갖거나 반복해서 묻는 주제/기술/분야\n"
                "  - \"영역\": 사용자가 진행 중인 프로젝트/업무 영역(프로젝트 "
                "자체 메모리와는 별개로, 전역 대화에서 드러나는 업무 맥락)\n"
                "  - \"사람\": 대화에 등장하는 특정 인물(상사, 동료, 팀원 등 "
                "사용자 본인이 아닌 다른 사람) 개개인에 대한 정보. 인물마다 "
                "별도 토픽을 만들고 topic name은 그 사람의 이름/직책으로 해 "
                "(예: \"본부장님\", \"최근표 사원\")\n"
                "- 중요: 한 문장/메시지 안에 여러 종류의 정보가 섞여 있으면 "
                "카테고리별로 쪼개서 각자 알맞은 토픽에 나눠 담아. 예를 들어 "
                "\"나는 A회사 B팀에서 C, D와 함께 일해\"라는 문장이면, "
                "A회사/B팀 소속 정보는 '나' 토픽에, C와 D는 각각 '사람' 토픽으로 "
                "분리해서 저장해 - 사용자 자신에 대한 정보가 아닌 타인에 대한 "
                "정보를 '나' 토픽에 섞어 넣으면 안 됨. 특정 인물의 이름/직책은 "
                "그 사람의 '사람' 토픽에만 적고, '나' 토픽에는 중복해서 적지 마 "
                "(예: '나' 토픽엔 \"동료들과 함께 일한다\"처럼만 남기고, "
                "이름/직책은 각 '사람' 토픽에서만 다뤄)\n"
            )
            format_example = (
                '{"topics": [{"category": "나", "name": "토픽 이름", '
                '"summary": "한 줄 요약", "details": ["세부 사실1", "세부 사실2"]}]}'
            )

        prompt = (
            f"[기존 메모리]\n{existing_json}\n\n"
            f"[새 대화]\n{messages_text}\n\n"
            "메모리는 '토픽' 단위로 관리돼. 각 토픽은 이름(name), 한 줄 요약"
            "(summary), 세부 사실 불릿 목록(details)으로 구성돼.\n"
            "아래 규칙으로 [기존 메모리]를 갱신한 최종 전체 토픽 목록을 만들어줘.\n"
            "- [새 대화]에서 새로운 사실(직업, 소속, 선호도, 담당 업무, 관심사 등)을 "
            "찾으면, 관련 있는 기존 토픽이 있으면 그 토픽의 summary/details에 "
            "병합하고, 없으면 새 토픽을 만들어\n"
            "- 기존 토픽과 내용이 중복되면 유지, 모순되면 최신 내용으로 교체하고 "
            "summary를 그에 맞게 다시 작성해\n"
            f"{category_rule}"
            '- 일반 지식 질문 ("~가 뭐야?" 형태) → 무시\n'
            "- 1인칭(나, 내, 저) 및 2인칭(너, 당신) 표현을 쓰지 말고 "
            "'사용자'라고 표현해 (이 메모리는 나중에 다른 대화의 system 메시지로 "
            "다시 들어가므로, 대화체를 그대로 옮기면 안 됨)\n"
            "- [새 대화]에서 바뀌지 않은 기존 토픽은 그대로 결과에 포함시켜 "
            "(응답은 '기존 전체 + 새 변경사항'을 합친 최종본이어야 함)\n"
            "- 최종 토픽 목록 전체를 JSON으로만 반환\n"
            "- 아래 반환 형식의 값들은 형식만 보여주는 자리표시자다. [기존 메모리]나 "
            "[새 대화]에 실제로 나오지 않은 내용이면 절대 포함하지 마라\n\n"
            f"반환 형식: {format_example}"
        )

        try:
            raw = await call_model(MEMORY_MODEL, prompt)
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            topics = data.get("topics", [])
        except Exception as exc:  # noqa: BLE001
            logger.error("메모리 추출 실패 (conv_id=%s): %s", conv_id, exc)
            return

        # 기존 메모리 전체를 새로 뽑은 최종본으로 교체 (모델이 추가/유지/교체를 다 반영해서 줌)
        if is_project:
            await session.execute(
                delete(ProjectMemory).where(ProjectMemory.project_id == conv.project_id)
            )
            for topic in topics:
                name = topic.get("name")
                summary = topic.get("summary")
                if not name or not summary:
                    continue
                session.add(
                    ProjectMemory(
                        project_id=conv.project_id,
                        name=name,
                        summary=summary,
                        details=json.dumps(topic.get("details", []), ensure_ascii=False),
                    )
                )
        else:
            await session.execute(delete(UserMemory).where(UserMemory.user_id == user_id))
            seen_me = False
            for topic in topics:
                name = topic.get("name")
                summary = topic.get("summary")
                category = topic.get("category")
                if not name or not summary or category not in (
                    "나", "주제", "영역", "사람",
                ):
                    continue
                if category == "나":
                    # 모델이 지시를 어기고 "나" 토픽을 여러 개 반환하는 경우에
                    # 대비한 방어적 가드 - 첫 번째만 쓰고 나머지는 버린다.
                    if seen_me:
                        continue
                    seen_me = True
                session.add(
                    UserMemory(
                        user_id=user_id,
                        category=category,
                        name=name,
                        summary=summary,
                        details=json.dumps(topic.get("details", []), ensure_ascii=False),
                    )
                )

        conv.last_extracted_message_id = new_messages[-1].id
        await session.commit()


# 수동 트리거(extract-all 엔드포인트)와 예약 트리거(scheduled_extraction_loop)가
# 동시에 같은 대화방을 처리하는 걸 막는 락 - 두 호출부 모두 이 함수 하나를
# 거치므로 여기 한 군데에만 걸어두면 트리거 방식과 무관하게 항상 직렬화된다.
_extraction_lock = asyncio.Lock()


async def _extract_all_sequentially(conv_ids: list[UUID]) -> None:
    # 채팅방마다 "기존 메모리 전체 삭제 → 새로 계산한 결과 삽입"을 하기 때문에,
    # 여러 채팅방을 asyncio.create_task로 동시에 돌리면 나중에 커밋되는 쪽이
    # 앞서 처리된 채팅방의 결과까지 지워버리는 경쟁 상태가 생긴다. 그래서 하나의
    # 백그라운드 태스크 안에서 순서대로(await) 처리해 서로 겹치지 않게 한다.
    async with _extraction_lock:
        for conv_id in conv_ids:
            await _extract_memory_for_conversation(DEMO_USER_ID, conv_id)


def _next_scheduled_run(now: datetime) -> datetime:
    """now(KST, tz-aware) 이후 가장 가까운 예약 시각을 반환. 오늘 남은 예약
    시각이 없으면 내일 첫 예약 시각으로 넘어간다."""
    candidates = []
    for day_offset in (0, 1):
        target_date = (now + timedelta(days=day_offset)).date()
        for hour, minute in SCHEDULED_TIMES:
            candidate = datetime(
                target_date.year, target_date.month, target_date.day,
                hour, minute, tzinfo=KST,
            )
            if candidate > now:
                candidates.append(candidate)
    return min(candidates)


async def scheduled_extraction_loop() -> None:
    """하루 2회(SCHEDULED_TIMES, KST) 전체 대화방의 메모리를 자동 갱신하는
    상시 루프. main.py의 lifespan에서 asyncio.create_task로 띄운다
    (attachments/service.py의 orphan_cleanup_loop와 동일한 패턴).

    재시작으로 예약 시각을 놓치면(예: 07:05에 기동) 그날의 그 회차는 건너뛰고
    바로 다음 예약 시각으로 넘어간다 - 놓친 회차를 뒤늦게 따라잡는 로직은 없음
    (데모 규모에서 굳이 필요하지 않다고 판단).
    """
    while True:
        now = datetime.now(KST)
        next_run = _next_scheduled_run(now)
        wait_seconds = (next_run - now).total_seconds()
        logger.info(
            "다음 자동 메모리 추출 예정: %s (%.0f초 후)",
            next_run.isoformat(), wait_seconds,
        )
        await asyncio.sleep(wait_seconds)

        try:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(Conversation).where(Conversation.user_id == DEMO_USER_ID)
                )
                conversations = result.scalars().all()
            await _extract_all_sequentially([conv.id for conv in conversations])
            logger.info("자동 메모리 추출 완료 (%d개 대화방)", len(conversations))
        except Exception as exc:  # noqa: BLE001
            logger.error("자동 메모리 추출 실패: %s", exc, exc_info=True)
