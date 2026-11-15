"""전역 메모리(USER#) 와 프로젝트 메모리(PROJ#) (AP3, AP4, AP18).

토픽 하나 = 아이템 하나. details는 지금 코드처럼 JSON 문자열이 아니라 DynamoDB 리스트로 저장한다.
"""

import uuid

from boto3.dynamodb.conditions import Key

from app.db import dynamo, keys
from app.db.dynamo import now_iso, strip_keys


def _sorted(items: list[dict]) -> list[dict]:
    return sorted((strip_keys(i) for i in items), key=lambda i: i["updated_at"], reverse=True)


def list_user_memory(uid, category: str | None = None) -> list[dict]:
    prefix = keys.MEM_PREFIX + (f"{category}#" if category else "")
    return _sorted(dynamo.query_partition(keys.user_pk(uid), prefix))


def put_user_memory(uid, category: str, name: str, summary: str, details: list[str],
                    mem_id: str | None = None) -> dict:
    """mem_id가 없으면 추가, 있으면 덮어쓴다(update). created_at은 처음 값을 유지한다."""
    mem_id = mem_id or str(uuid.uuid4())
    now = now_iso()
    dynamo.update(
        keys.user_pk(uid), keys.user_mem_sk(category, mem_id),
        set_={"id": mem_id, "category": category, "name": name, "summary": summary,
              "details": details, "updated_at": now, "GSI3PK": mem_id, "owner_pk": keys.user_pk(uid)},
        if_not_exists={"created_at": now},
    )
    return {"id": mem_id, "category": category, "name": name, "summary": summary, "details": details}


def list_project_memory(pid) -> list[dict]:
    return _sorted(dynamo.query_partition(keys.proj_pk(pid), keys.MEM_PREFIX))


def put_project_memory(uid, pid, name: str, summary: str, details: list[str],
                       mem_id: str | None = None) -> dict:
    mem_id = mem_id or str(uuid.uuid4())
    now = now_iso()
    dynamo.update(
        keys.proj_pk(pid), keys.proj_mem_sk(mem_id),
        set_={"id": mem_id, "project_id": str(pid), "name": name, "summary": summary,
              "details": details, "updated_at": now, "GSI3PK": mem_id, "owner_pk": keys.user_pk(uid)},
        if_not_exists={"created_at": now},
    )
    return {"id": mem_id, "project_id": str(pid), "name": name, "summary": summary, "details": details}


def _find_owned(uid, mem_id) -> dict | None:
    found = dynamo.query_all(
        IndexName=keys.GSI_BY_ID, KeyConditionExpression=Key("GSI3PK").eq(str(mem_id))
    )
    for item in found:
        if item.get("SK", "").startswith(keys.MEM_PREFIX) and item.get("owner_pk") == keys.user_pk(uid):
            return item
    return None


def update_memory_by_id(uid, mem_id, summary: str, details: list[str]) -> dict | None:
    """메모리 화면에서 토픽 요약과 상세를 직접 고칠 때 (전역, 프로젝트 공통)."""
    item = _find_owned(uid, mem_id)
    if item is None:
        return None
    dynamo.update(item["PK"], item["SK"], set_={"summary": summary, "details": details, "updated_at": now_iso()})
    return strip_keys(dynamo.get_item(item["PK"], item["SK"]))


def delete_memory_by_id(uid, mem_id) -> bool:
    """전역인지 프로젝트 메모리인지 모르는 id를 지운다 (AP4).
    GSI3로 위치를 찾고, owner_pk로 내 메모리인지 확인한 뒤에만 지운다."""
    found = dynamo.query_all(
        IndexName=keys.GSI_BY_ID, KeyConditionExpression=Key("GSI3PK").eq(str(mem_id))
    )
    for item in found:
        if item.get("SK", "").startswith(keys.MEM_PREFIX) and item.get("owner_pk") == keys.user_pk(uid):
            dynamo.table().delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            return True
    return False
