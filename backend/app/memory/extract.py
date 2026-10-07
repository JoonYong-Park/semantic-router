"""자동 메모리 추출 - 대화에서 사용자에 대한 사실을 뽑아 UserMemory로 쌓는다.

chat/router.py, settings/router.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID
하나에 귀속시킨다. Semantic Router/스트리밍/컨텍스트 유지/개인 지침/가져오기
로직은 건드리지 않는다.

하루 2회(07:00, 12:30 KST) 자동 실행되는 스케줄 루프(scheduled_extraction_loop)와
프론트 버튼으로 트리거하는 수동 실행(/memory/extract-all)이 공존한다. 트리거
방식은 서로 완전히 독립적이고(하나가 없어져도 다른 쪽 동작에 영향 없음), 실행
로직(_extract_all_sequentially)만 공유하면서 asyncio.Lock으로 동시 실행만
막는다 - 두 트리거가 우연히 겹쳐도 같은 대화방의 UserMemory를 동시에
고쳐 쓰는 경쟁 상태가 나지 않게. Celery/Redis 없이 asyncio로 충분한
데모 규모라 판단해 attachments/service.py의 orphan_cleanup_loop와 동일한 패턴을 썼다.
수동 트리거는 나중에 제거될 예정이라 그 전까지는 최대한 손대지 않았다."""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.llm.client import call_model
from app.core.db import SessionLocal
from app.core.models import DEMO_USER_ID, Conversation, Message, ProjectMemory, UserMemory
from app.routing.catalog import get_model

logger = logging.getLogger("uvicorn.error")


KST = ZoneInfo("Asia/Seoul")


# 하루 중 자동 추출을 돌릴 시각(KST, 24시간제) - 필요하면 이 목록만 바꾸면 됨.
SCHEDULED_TIMES = [(7, 0), (12, 30)]


# 요약 작업이라 비용/속도를 우선해 OpenAI small을 쓴다. gpt-4o-mini는 "저장할 가치"
# 판단이 약해서(작업 요청/모델 설명을 그대로 저장, 기존 쓰레기 토픽 삭제를 거의 안 함) 
# 카탈로그의 small 모델로 바꿨다.
MEMORY_MODEL = get_model("openai", "small")


# 추출 프롬프트에 넣는 assistant 답변 길이 상한(자) - 맥락 파악용.
ASSISTANT_CONTEXT_CHARS = 300

_GLOBAL_CATEGORIES = ("나", "주제", "영역", "사람")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…(생략)"


def _parse_changes(data: dict) -> dict[str, list[dict]]:
    """모델 응답을 {"add", "update", "delete"} 리스트로 정규화한다(잘못된 항목은 버림)."""
    out: dict[str, list[dict]] = {}
    for key in ("add", "update", "delete"):
        items = data.get(key) if isinstance(data, dict) else None
        out[key] = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return out


def _details_json(topic: dict) -> str:
    details = topic.get("details") or []
    if not isinstance(details, list):
        details = []
    return json.dumps([str(d) for d in details if str(d).strip()], ensure_ascii=False)


async def _delete_marked(session, rows_by_key: dict, changes: dict) -> set[str]:
    """delete 항목의 기존 토픽을 지우고, 지운 키 집합을 돌려준다(같은 응답의 update가
    지운 토픽을 다시 건드리지 않게)."""
    deleted: set[str] = set()
    for item in changes["delete"]:
        key = item.get("id")
        row = rows_by_key.get(key)
        if row is not None and key not in deleted:
            await session.delete(row)
            deleted.add(key)
    return deleted


def _update_row(row, item: dict) -> None:
    if item.get("name"):
        row.name = item["name"]
    if item.get("summary"):
        row.summary = item["summary"]
    if "details" in item:
        row.details = _details_json(item)


async def _apply_project_changes(session, project_id, rows_by_key: dict, changes: dict) -> None:
    deleted = await _delete_marked(session, rows_by_key, changes)
    for item in changes["update"]:
        row = rows_by_key.get(item.get("id"))
        if row is not None and item.get("id") not in deleted:
            _update_row(row, item)
    for item in changes["add"]:
        if not item.get("name") or not item.get("summary"):
            continue
        session.add(
            ProjectMemory(
                project_id=project_id,
                name=item["name"],
                summary=item["summary"],
                details=_details_json(item),
            )
        )


async def _apply_user_changes(session, user_id, rows_by_key: dict, changes: dict) -> None:
    deleted = await _delete_marked(session, rows_by_key, changes)
    live = {k: r for k, r in rows_by_key.items() if k not in deleted}
    # "나" 토픽은 전체에서 정확히 1개 - 이미 있으면 새 "나"를 만들지 않고 거기에 합친다.
    me_row = next((r for r in live.values() if r.category == "나"), None)

    for item in changes["update"]:
        row = live.get(item.get("id"))
        if row is None:
            continue
        category = item.get("category")
        # 다른 토픽을 "나"로 바꿔 "나"가 2개가 되는 것은 막는다(카테고리만 무시).
        if category in _GLOBAL_CATEGORIES and not (category == "나" and me_row is not None and row is not me_row):
            row.category = category
        _update_row(row, item)

    for item in changes["add"]:
        category = item.get("category")
        if not item.get("name") or not item.get("summary") or category not in _GLOBAL_CATEGORIES:
            continue
        if category == "나" and me_row is not None:
            # 모델이 지시를 어기고 "나"를 새로 만든 경우 - 기존 "나"의 세부 사실에 합친다.
            merged = json.loads(me_row.details) if me_row.details else []
            for d in json.loads(_details_json(item)):
                if d not in merged:
                    merged.append(d)
            me_row.details = json.dumps(merged, ensure_ascii=False)
            continue
        row = UserMemory(
            user_id=user_id,
            category=category,
            name=item["name"],
            summary=item["summary"],
            details=_details_json(item),
        )
        session.add(row)
        if category == "나":
            me_row = row


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
                    "id": f"t{i + 1}",
                    "name": m.name,
                    "summary": m.summary,
                    "details": json.loads(m.details) if m.details else [],
                }
                for i, m in enumerate(existing_rows)
            ]
        else:
            mem_result = await session.execute(
                select(UserMemory).where(UserMemory.user_id == user_id)
            )
            existing_rows = mem_result.scalars().all()
            existing_topics = [
                {
                    "id": f"t{i + 1}",
                    "category": m.category,
                    "name": m.name,
                    "summary": m.summary,
                    "details": json.loads(m.details) if m.details else [],
                }
                for i, m in enumerate(existing_rows)
            ]
        rows_by_key = {f"t{i + 1}": m for i, m in enumerate(existing_rows)}

        existing_json = json.dumps({"topics": existing_topics}, ensure_ascii=False)
        # assistant 답변은 사용자 말의 맥락을 잡는 데만 필요하므로 앞부분만 넣는다 -
        # 전문을 넣으면 모델이 설명한 내용(개념 해설 등)이 사용자 사실로 저장됐다.
        messages_text = "\n".join(
            f"{m.role}: {m.content if m.role == 'user' else _clip(m.content, ASSISTANT_CONTEXT_CHARS)}"
            for m in new_messages
        )

        if is_project:
            category_rule = ""
            format_example = (
                '{"add": [{"name": "토픽 이름", "summary": "한 줄 요약", '
                '"details": ["세부 사실1"]}], '
                '"update": [{"id": "t1", "name": "토픽 이름", "summary": "한 줄 요약", '
                '"details": ["기존 사실", "새 사실"]}], '
                '"delete": [{"id": "t2", "reason": "삭제 사유"}]}'
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
                "  - \"주제\": 사용자가 관심을 직접 밝혔거나(\"요즘 ~를 공부 중이야\", "
                "\"~에 관심 많아\") [기존 메모리]와 [새 대화]를 통틀어 여러 번 다룬 "
                "주제/기술/분야만. 질문 한 번이나 작업 요청 한 번으로는 만들지 마\n"
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
                '{"add": [{"category": "주제", "name": "토픽 이름", '
                '"summary": "한 줄 요약", "details": ["세부 사실1"]}], '
                '"update": [{"id": "t1", "category": "나", "name": "토픽 이름", '
                '"summary": "한 줄 요약", "details": ["기존 사실", "새 사실"]}], '
                '"delete": [{"id": "t2", "reason": "삭제 사유"}]}'
            )

        prompt = (
            f"[기존 메모리]\n{existing_json}\n\n"
            f"[새 대화]\n{messages_text}\n\n"
            "메모리는 '토픽' 단위로 관리돼. 각 토픽은 이름(name), 한 줄 요약"
            "(summary), 세부 사실 불릿 목록(details)으로 구성돼.\n"
            "아래 규칙으로 [기존 메모리]에 적용할 변경사항만 만들어줘. 응답에 넣지 "
            "않은 기존 토픽은 그대로 유지되므로, 바꿀 게 없는 토픽은 절대 응답에 넣지 마.\n"
            "- 저장 기준: \"이 사실이 앞으로 전혀 다른 대화에서 사용자에게 더 잘 "
            "답하는 데 도움이 되는가?\" 그렇다고 확신할 때만 저장해. 애매하면 "
            "저장하지 마\n"
            "- 사실의 출처는 user 메시지여야 해. assistant 메시지는 user 말의 "
            "맥락을 이해하는 데만 쓰고, assistant가 쓴 설명/답변/생성물 내용을 "
            "사용자에 대한 사실로 저장하지 마\n"
            "- 반대로 사용자가 직접 밝힌 선호(답변 형식·길이·말투·언어 등, 예: \"답변은 "
            "표로 정리해줘\", \"항상 짧게 답해\")는 앞으로의 모든 대화에 쓰이므로 "
            "반드시 저장해\n"
            "- [새 대화]에서 저장할 새 사실을 찾으면, 관련 있는 기존 토픽이 있으면 "
            "update(그 토픽 id + 병합한 전체 summary/details - details는 기존 사실도 "
            "빠짐없이 포함한 최종 목록), 없으면 add로 새 토픽을 만들어\n"
            "- 기존 사실과 모순되면 update에서 최신 내용으로 교체하고 summary를 다시 써. "
            "이미 있는 사실과 같은 내용이면 아무것도 하지 마\n"
            f"{category_rule}"
            "- 다음은 저장하지 마(무시):\n"
            "  - 일회성 작업 요청: 이미지/그림/포스터 생성, 문서·엑셀·PPT 작성, "
            "번역, 요약, 코드 예시 작성 등 '한 번 해 달라'는 요청 자체와 그 결과물\n"
            "  - 모델(assistant)이 생성한 내용과 설명(개념 해설, 코드, 문구 등)\n"
            "  - 파일명, 예시/샘플 데이터, 테스트용 문장, 인사말\n"
            '  - 일반 지식 질문 한 번 ("~가 뭐야?", "~ 설명해줘" 형태)\n'
            "- 저장하면 안 되는 실제 사례(이런 토픽은 만들지 마):\n"
            '  - "고양이 일러스트" / "산 풍경 이미지" / "여름 행사 포스터": 이미지 생성 요청 한 번\n'
            '  - "팀 회의 안건": 워드 파일 작성 요청 한 번\n'
            '  - "매출 추이": 표를 만들려고 넣은 예시 숫자\n'
            '  - "명언": 명언을 하나 알려 달라는 요청과 그 답\n'
            '  - "CAP 정리": assistant가 설명한 개념 내용(C/A/P 정의, 해결 패턴 등)\n'
            '  - "이진 탐색": 코드 예시를 한 번 요청한 것\n'
            '  - 파일명 "이미지_20261006_032620.png" 같은 생성 파일 이름\n'
            "- 1인칭(나, 내, 저) 및 2인칭(너, 당신) 표현을 쓰지 말고 "
            "'사용자'라고 표현해 (이 메모리는 나중에 다른 대화의 system 메시지로 "
            "다시 들어가므로, 대화체를 그대로 옮기면 안 됨)\n"
            "- delete: [기존 메모리] 토픽 중 위 저장 기준에 맞지 않는 것(작업 요청, "
            "모델 생성 내용, 예시 데이터, 파일명, 지식 질문 한 번 등 - 위 '실제 사례' 같은 "
            "토픽)은 id와 사유를 적어 delete에 넣어. [새 대화]에 언급되지 않았다는 "
            "이유만으로는 절대 삭제하지 마 - 사람/소속/업무/선호처럼 기준에 맞는 토픽은 "
            "새 대화와 무관해도 유지돼야 해\n"
            "- 변경이 없으면 {\"add\": [], \"update\": [], \"delete\": []}을 반환해\n"
            "- JSON으로만 반환\n"
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
            changes = _parse_changes(data)
        except Exception as exc:  # noqa: BLE001
            logger.error("메모리 추출 실패 (conv_id=%s): %s", conv_id, exc)
            return

        # 모델이 돌려준 변경분만 반영한다. 응답에 없는 기존 토픽은 건드리지 않아서,
        # 모델이 목록을 다시 쓰다 토픽을 빠뜨려도 사라지지 않고 행 id도 유지된다.
        if is_project:
            await _apply_project_changes(session, conv.project_id, rows_by_key, changes)
        else:
            await _apply_user_changes(session, user_id, rows_by_key, changes)
        logger.info(
            "메모리 갱신 (conv_id=%s, scope=%s): 추가 %d, 수정 %d, 삭제 %d %s",
            conv_id, "project" if is_project else "global",
            len(changes["add"]), len(changes["update"]), len(changes["delete"]),
            [f"{rows_by_key[d['id']].name}({d.get('reason', '')})" for d in changes["delete"] if d.get("id") in rows_by_key],
        )

        conv.last_extracted_message_id = new_messages[-1].id
        await session.commit()


# 수동 트리거(extract-all 엔드포인트)와 예약 트리거(scheduled_extraction_loop)가
# 동시에 같은 대화방을 처리하는 걸 막는 락 - 두 호출부 모두 이 함수 하나를
# 거치므로 여기 한 군데에만 걸어두면 트리거 방식과 무관하게 항상 직렬화된다.
_extraction_lock = asyncio.Lock()


async def _extract_all_sequentially(conv_ids: list[UUID]) -> None:
    # 채팅방마다 "기존 메모리 읽기 → 모델에 변경분 요청 → 반영"을 하기 때문에,
    # 여러 채팅방을 asyncio.create_task로 동시에 돌리면 서로 오래된 목록을 기준으로
    # 수정/삭제해 결과가 꼬인다(같은 토픽 중복 추가 등). 그래서 하나의 백그라운드
    # 태스크 안에서 순서대로(await) 처리해 서로 겹치지 않게 한다.
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
