"""자동 메모리 추출 (기존 memory/extract.py).

대화에서 사용자에 대한 사실을 뽑아 메모리 토픽으로 쌓는다. 프롬프트, diff 방식
({add, update, delete}), "나" 토픽 1개 규칙은 기존과 같다.

달라진 점
- 커서: Conversation.last_extracted_message_id 대신 대화 아이템의 last_extracted_sk
  (메시지 SK가 시간순이라 그 뒤만 Query로 읽는다, AP23).
- 토픽 details는 JSON 문자열이 아니라 리스트로 저장한다.
- 실행: asyncio.Lock 대신 threading.Lock. 하루 2회 예약 실행은 EventBridge Scheduler가
  Lambda를 호출하고 routes/events.py가 run_scheduled()를 부른다 (infra/backend.yaml). 로컬에서는 수동 트리거(/memory/extract-all)만.
"""

import asyncio
import json
import logging
import re
import threading

from app.db import dynamo, keys
from app.db.repo import conversations as conv_repo
from app.db.repo import memory as memory_repo
from app.llm.client import call_model
from app.routing.catalog import get_model

logger = logging.getLogger("uvicorn.error")

MEMORY_MODEL = get_model("openai", "small")
ASSISTANT_CONTEXT_CHARS = 300
_GLOBAL_CATEGORIES = ("나", "주제", "영역", "사람")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…(생략)"


def _parse_changes(data: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key in ("add", "update", "delete"):
        items = data.get(key) if isinstance(data, dict) else None
        out[key] = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    return out


def _details(topic: dict) -> list[str]:
    details = topic.get("details") or []
    if not isinstance(details, list):
        details = []
    return [str(d) for d in details if str(d).strip()]


def _delete_topic(row: dict) -> None:
    dynamo.table().delete_item(Key={"PK": row["PK"], "SK": row["SK"]})


def _apply_project_changes(uid, project_id, rows_by_key: dict, changes: dict) -> None:
    deleted = set()
    for item in changes["delete"]:
        row = rows_by_key.get(item.get("id"))
        if row is not None and item["id"] not in deleted:
            _delete_topic(row)
            deleted.add(item["id"])
    for item in changes["update"]:
        row = rows_by_key.get(item.get("id"))
        if row is None or item.get("id") in deleted:
            continue
        memory_repo.put_project_memory(
            uid, project_id, item.get("name") or row["name"], item.get("summary") or row["summary"],
            _details(item) if "details" in item else row.get("details", []), mem_id=row["id"],
        )
    for item in changes["add"]:
        if item.get("name") and item.get("summary"):
            memory_repo.put_project_memory(uid, project_id, item["name"], item["summary"], _details(item))


def _apply_user_changes(uid, rows_by_key: dict, changes: dict) -> None:
    deleted = set()
    for item in changes["delete"]:
        row = rows_by_key.get(item.get("id"))
        if row is not None and item["id"] not in deleted:
            _delete_topic(row)
            deleted.add(item["id"])
    live = {k: r for k, r in rows_by_key.items() if k not in deleted}
    # "나" 토픽은 전체에서 정확히 1개 - 이미 있으면 새 "나"를 만들지 않고 거기에 합친다.
    me_row = next((r for r in live.values() if r["category"] == "나"), None)

    for item in changes["update"]:
        row = live.get(item.get("id"))
        if row is None:
            continue
        category = row["category"]
        new_cat = item.get("category")
        # 다른 토픽을 "나"로 바꿔 "나"가 2개가 되는 것은 막는다(카테고리만 무시).
        if new_cat in _GLOBAL_CATEGORIES and not (new_cat == "나" and me_row is not None and row is not me_row):
            category = new_cat
        if category != row["category"]:
            # 카테고리가 SK에 들어 있어서(MEM#{category}#{id}) 바뀌면 옮겨 적는다.
            _delete_topic(row)
        memory_repo.put_user_memory(
            uid, category, item.get("name") or row["name"], item.get("summary") or row["summary"],
            _details(item) if "details" in item else row.get("details", []), mem_id=row["id"],
        )

    for item in changes["add"]:
        category = item.get("category")
        if not item.get("name") or not item.get("summary") or category not in _GLOBAL_CATEGORIES:
            continue
        if category == "나" and me_row is not None:
            # 모델이 지시를 어기고 "나"를 새로 만든 경우 - 기존 "나"의 세부 사실에 합친다.
            fresh = dynamo.get_item(me_row["PK"], me_row["SK"]) or me_row
            merged = list(fresh.get("details", []))
            for d in _details(item):
                if d not in merged:
                    merged.append(d)
            memory_repo.put_user_memory(uid, "나", fresh["name"], fresh["summary"], merged, mem_id=fresh["id"])
            continue
        created = memory_repo.put_user_memory(uid, category, item["name"], item["summary"], _details(item))
        if category == "나":
            me_row = dynamo.get_item(keys.user_pk(uid), keys.user_mem_sk("나", created["id"]))


def _raw_topics(pk: str) -> list[dict]:
    """토픽 아이템을 키까지 포함해 읽는다 (삭제와 카테고리 이동에 PK/SK가 필요)."""
    return dynamo.query_partition(pk, keys.MEM_PREFIX)


def extract_for_conversation(uid, cid) -> None:
    """대화 하나에서 이전에 요약한 지점 이후의 새 메시지만 뽑아 메모리를 갱신한다."""
    conv = conv_repo.get_conversation(uid, cid)
    if conv is None:
        return
    new_messages = conv_repo.messages_after(cid, conv.get("last_extracted_sk"))
    if not new_messages:
        return

    is_project = bool(conv.get("project_id"))
    if is_project:
        existing_rows = _raw_topics(keys.proj_pk(conv["project_id"]))
        existing_topics = [
            {"id": f"t{i + 1}", "name": m["name"], "summary": m["summary"], "details": m.get("details", [])}
            for i, m in enumerate(existing_rows)
        ]
    else:
        existing_rows = _raw_topics(keys.user_pk(uid))
        existing_topics = [
            {"id": f"t{i + 1}", "category": m["category"], "name": m["name"], "summary": m["summary"],
             "details": m.get("details", [])}
            for i, m in enumerate(existing_rows)
        ]
    rows_by_key = {f"t{i + 1}": m for i, m in enumerate(existing_rows)}

    existing_json = json.dumps({"topics": existing_topics}, ensure_ascii=False)
    messages_text = "\n".join(
        f"{m['role']}: {m['content'] if m['role'] == 'user' else _clip(m['content'], ASSISTANT_CONTEXT_CHARS)}"
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
        raw = asyncio.run(call_model(MEMORY_MODEL, prompt))
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return
        changes = _parse_changes(json.loads(match.group()))
    except Exception as exc:  # noqa: BLE001
        logger.error("메모리 추출 실패 (conv_id=%s): %s", cid, exc)
        return

    if is_project:
        _apply_project_changes(uid, conv["project_id"], rows_by_key, changes)
    else:
        _apply_user_changes(uid, rows_by_key, changes)
    logger.info(
        "메모리 갱신 (conv_id=%s, scope=%s): 추가 %d, 수정 %d, 삭제 %d %s",
        cid, "project" if is_project else "global",
        len(changes["add"]), len(changes["update"]), len(changes["delete"]),
        [f"{rows_by_key[d['id']]['name']}({d.get('reason', '')})" for d in changes["delete"] if d.get("id") in rows_by_key],
    )
    conv_repo.update_conversation(uid, cid, last_extracted_sk=new_messages[-1]["sk"])


# 수동 트리거와 예약 트리거가 같은 대화를 동시에 처리하지 않게 직렬화한다.
_extraction_lock = threading.Lock()


def extract_all_sequentially(uid, conv_ids: list[str]) -> None:
    with _extraction_lock:
        for cid in conv_ids:
            try:
                extract_for_conversation(uid, cid)
            except Exception as exc:  # noqa: BLE001
                logger.error("메모리 추출 중 오류 (conv_id=%s): %s", cid, exc, exc_info=True)


def run_scheduled(event=None, context=None) -> None:
    """하루 2회(07:00, 12:30 KST) 예약 실행 진입점. EventBridge Scheduler가 Lambda를 호출하면 routes/events.py가 부른다.

    모든 사용자의 대화를 차례로 본다. 대화마다 마지막으로 요약한 지점 이후의 새 메시지만 보내므로
    새 메시지가 없는 대화는 조회 두 번으로 끝난다. Lambda 제한 시간 안에 못 끝내면 남은 사용자는
    다음 실행에서 이어서 처리된다(새 메시지가 그대로 남아 있으니 놓치지 않는다).
    """
    from app.auth import DEMO_USER_ID
    from app.db.repo import users as user_repo

    def time_left_ms() -> int:
        return context.get_remaining_time_in_millis() if context else 10**9

    users = [u for u in user_repo.list_user_ids() if u != str(DEMO_USER_ID)]
    done_users = done_convs = 0
    for uid in users:
        if time_left_ms() < 20_000:
            logger.warning("자동 메모리 추출: 시간이 부족해 %d명 중 %d명까지만 처리", len(users), done_users)
            break
        convs = conv_repo.list_conversations(uid)
        extract_all_sequentially(uid, [c["id"] for c in convs])
        done_users += 1
        done_convs += len(convs)
    logger.info("자동 메모리 추출 완료 (사용자 %d명, 대화방 %d개)", done_users, done_convs)
