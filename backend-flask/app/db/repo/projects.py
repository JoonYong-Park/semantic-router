"""프로젝트와 프로젝트 파일 (AP15~AP19).

- USER#{uid} / PROJ#{pid}               프로젝트 (files_total_bytes: 파일 용량 합계 카운터)
- PROJ#{pid} / FILE#{created_at}#{fid}  프로젝트 파일 (GSI3: id로 찾기)
- PROJ#{pid} / MEM#{mem_id}             프로젝트 메모리 (memory.py)
"""

import uuid

from boto3.dynamodb.conditions import Attr, Key

from app.db import dynamo, keys
from app.db.dynamo import now_iso, strip_keys, to_dynamo
from app.db.repo import conversations

DEFAULT_MAX_CAPACITY = 10_000_000


def create_project(uid, name: str, instruction: str | None = None, color: str = "default") -> dict:
    pid = str(uuid.uuid4())
    now = now_iso()
    item = {
        "PK": keys.user_pk(uid), "SK": keys.proj_sk(pid),
        "id": pid, "user_id": str(uid), "name": name, "instruction": instruction,
        "max_capacity_bytes": DEFAULT_MAX_CAPACITY, "files_total_bytes": 0,
        "pinned": False, "color": color, "sort_order": 0, "created_at": now, "updated_at": now,
    }
    dynamo.table().put_item(Item=to_dynamo(item))
    return get_project(uid, pid)


def get_project(uid, pid) -> dict | None:
    """소유자 확인 포함: 다른 사용자의 프로젝트는 내 파티션에 없다 (_get_owned_project 대체)."""
    item = dynamo.get_item(keys.user_pk(uid), keys.proj_sk(pid))
    return strip_keys(item) if item else None


def list_projects(uid) -> list[dict]:
    items = [strip_keys(i) for i in dynamo.query_partition(keys.user_pk(uid), keys.PROJ_PREFIX)]
    return sorted(items, key=lambda p: p["created_at"])


def update_project(uid, pid, **fields) -> dict | None:
    allowed = {"name", "instruction", "pinned", "color", "sort_order"}
    set_ = {k: v for k, v in fields.items() if k in allowed and v is not None}
    set_["updated_at"] = now_iso()
    remove = [k for k, v in fields.items() if k in allowed and v is None]
    dynamo.table().update_item(
        Key={"PK": keys.user_pk(uid), "SK": keys.proj_sk(pid)},
        ConditionExpression="attribute_exists(PK)",
        **dynamo.update_expr(set_=set_, remove=remove or None),
    )
    return get_project(uid, pid)


# ------------------------------------------------------------------ 파일
def add_project_file(uid, pid, original_name: str, storage_key: str, mime_type: str, size_bytes: int,
                     extraction_status: str = "pending", fid: str | None = None) -> dict:
    """파일 아이템 생성과 용량 카운터 증가를 한 트랜잭션으로.
    용량 초과 검사는 호출 전에 get_project()의 files_total_bytes로 한다
    (ConditionExpression은 a + b 같은 산술을 지원하지 않는다)."""
    fid = str(fid or uuid.uuid4())
    now = now_iso()
    item = {
        "PK": keys.proj_pk(pid), "SK": keys.file_sk(now, fid),
        "id": fid, "project_id": str(pid), "original_name": original_name, "storage_key": storage_key,
        "mime_type": mime_type, "size_bytes": size_bytes, "extraction_status": extraction_status,
        "created_at": now, "GSI3PK": fid, "owner_pk": keys.user_pk(uid),
    }
    dynamo.client().transact_write_items(TransactItems=[
        {"Put": {"TableName": dynamo.TABLE_NAME, "Item": to_dynamo(item)}},
        {"Update": {
            "TableName": dynamo.TABLE_NAME,
            "Key": {"PK": keys.user_pk(uid), "SK": keys.proj_sk(pid)},
            "ConditionExpression": "attribute_exists(PK)",
            **dynamo.update_expr(add={"files_total_bytes": size_bytes}),
        }},
    ])
    out = strip_keys(dynamo.from_dynamo(to_dynamo(item)))
    out["sk"] = item["SK"]
    return out


def list_project_files(pid, done_only: bool = False) -> list[dict]:
    """AP17: SK에 created_at이 있어서 생성순으로 나온다."""
    kwargs = {"FilterExpression": Attr("extraction_status").eq("done")} if done_only else {}
    rows = dynamo.query_all(
        KeyConditionExpression=Key("PK").eq(keys.proj_pk(pid)) & Key("SK").begins_with(keys.FILE_PREFIX),
        **kwargs,
    )
    out = []
    for r in rows:
        f = strip_keys(r)
        f["sk"] = r["SK"]
        out.append(f)
    return out


def get_project_file(uid, fid) -> dict | None:
    found = dynamo.query_all(IndexName=keys.GSI_BY_ID, KeyConditionExpression=Key("GSI3PK").eq(str(fid)))
    for item in found:
        if item["SK"].startswith(keys.FILE_PREFIX) and item.get("owner_pk") == keys.user_pk(uid):
            fresh = dynamo.get_item(item["PK"], item["SK"])
            if fresh:
                f = strip_keys(fresh)
                f["sk"] = item["SK"]
                return f
    return None


def update_project_file(pid, sk: str, **fields) -> bool:
    """None 값은 속성을 지운다. 처리 도중 파일이 삭제됐으면 False."""
    set_ = {k: v for k, v in fields.items() if v is not None}
    remove = [k for k, v in fields.items() if v is None]
    try:
        dynamo.table().update_item(
            Key={"PK": keys.proj_pk(pid), "SK": sk},
            ConditionExpression="attribute_exists(PK)",
            **dynamo.update_expr(set_=set_ or None, remove=remove or None),
        )
    except dynamo.client().exceptions.ConditionalCheckFailedException:
        return False
    return True


def delete_project_file(uid, fid, item: dict | None = None) -> dict | None:
    """item(list_project_files의 한 항목, sk 포함)을 넘기면 GSI 조회 없이 바로 지운다."""
    f = item or get_project_file(uid, fid)
    if f is None:
        return None
    dynamo.client().transact_write_items(TransactItems=[
        {"Delete": {"TableName": dynamo.TABLE_NAME, "Key": {"PK": keys.proj_pk(f["project_id"]), "SK": f["sk"]}}},
        {"Update": {
            "TableName": dynamo.TABLE_NAME,
            "Key": {"PK": keys.user_pk(uid), "SK": keys.proj_sk(f["project_id"])},
            **dynamo.update_expr(add={"files_total_bytes": -int(f["size_bytes"])}),
        }},
    ])
    return f  # storage_key로 파일 삭제는 호출하는 쪽에서


# ------------------------------------------------------------------ 삭제 (AP19)
def delete_project(uid, pid) -> dict | None:
    """프로젝트, 파일, 메모리, 소속 대화와 그 메시지까지 지운다.
    아이템이 많을 수 있어서 Lambda에서는 Zappa @task(비동기)로 부른다.
    반환값의 conversation_ids로 S3 chats/{cid}/, projects/{pid}/ 정리를 한다."""
    if get_project(uid, pid) is None:
        return None
    conv_ids = [c["id"] for c in conversations.list_project_conversations(pid)]
    for cid in conv_ids:
        conversations.delete_conversation(uid, cid)
    children = dynamo.query_all(
        KeyConditionExpression=Key("PK").eq(keys.proj_pk(pid)), ProjectionExpression="PK, SK"
    )
    dynamo.delete_keys(children)
    dynamo.table().delete_item(Key={"PK": keys.user_pk(uid), "SK": keys.proj_sk(pid)})
    return {"project_id": str(pid), "conversation_ids": conv_ids, "deleted_children": len(children)}
