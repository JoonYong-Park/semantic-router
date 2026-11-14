"""대화, 메시지, 첨부 (AP5~AP14, AP23).

파티션 구조
- USER#{uid} / CONV#{cid}            대화 아이템 (GSI1: 최신순, GSI2: 프로젝트 소속)
- CONV#{cid} / MSG#{created_at}#{mid}  메시지
- CONV#{cid} / ATT#{aid}               첨부 (GSI3: id로 찾기, ttl: 고아 정리)

대화의 updated_at을 바꾸는 곳은 반드시 _touch_update()를 거친다. GSI1SK와 GSI2SK가
updated_at과 같이 움직여야 목록 순서가 맞기 때문이다 (설계 문서 6번 4).
"""

import time
import uuid

from boto3.dynamodb.conditions import Key

from app.db import dynamo, keys
from app.db.dynamo import now_iso, strip_keys, to_dynamo

UPLOAD_ORPHAN_SECONDS = 24 * 3600        # 전송 안 하고 이탈한 업로드
GENERATED_ORPHAN_SECONDS = 7 * 24 * 3600  # 스트리밍 중 끊겨 연결 못 한 생성 파일


# ------------------------------------------------------------------ 대화
def create_conversation(uid, title: str | None = None, project_id=None) -> dict:
    cid = str(uuid.uuid4())
    now = now_iso()
    item = {
        "PK": keys.user_pk(uid), "SK": keys.conv_sk(cid),
        "id": cid, "user_id": str(uid), "title": title, "pinned": False,
        "project_id": str(project_id) if project_id else None, "sort_order": 0,
        "created_at": now, "updated_at": now, "message_count": 0,
        "GSI1PK": keys.user_pk(uid), "GSI1SK": now,
    }
    if project_id:
        item["GSI2PK"], item["GSI2SK"] = keys.proj_pk(project_id), now
    dynamo.table().put_item(Item=to_dynamo(item))
    return strip_keys(dynamo.from_dynamo(to_dynamo(item)))


def get_conversation(uid, cid) -> dict | None:
    """소유자 확인이 키 구조로 된다: 다른 사용자의 대화는 USER#{내 uid} 아래에 없다."""
    item = dynamo.get_item(keys.user_pk(uid), keys.conv_sk(cid))
    return strip_keys(item) if item else None


def list_conversations(uid) -> list[dict]:
    """AP5: 고정한 대화가 먼저, 나머지는 최신 수정순."""
    items = dynamo.query_all(
        IndexName=keys.GSI_BY_UPDATED,
        KeyConditionExpression=Key("GSI1PK").eq(keys.user_pk(uid)),
        ScanIndexForward=False,
    )
    convs = [strip_keys(i) for i in items]
    pinned = sorted((c for c in convs if c.get("pinned")), key=lambda c: c.get("sort_order", 0))
    return pinned + [c for c in convs if not c.get("pinned")]


def list_project_conversations(pid) -> list[dict]:
    """AP16"""
    items = dynamo.query_all(
        IndexName=keys.GSI_BY_PROJECT,
        KeyConditionExpression=Key("GSI2PK").eq(keys.proj_pk(pid)),
        ScanIndexForward=False,
    )
    return [strip_keys(i) for i in items]


def update_conversation(uid, cid, **fields) -> dict | None:
    """title / pinned / sort_order / last_extracted_sk 변경. updated_at은 바꾸지 않는다."""
    allowed = {"title", "pinned", "sort_order", "last_extracted_sk"}
    set_ = {k: v for k, v in fields.items() if k in allowed and v is not None}
    remove = [k for k, v in fields.items() if k in allowed and v is None]
    dynamo.table().update_item(
        Key={"PK": keys.user_pk(uid), "SK": keys.conv_sk(cid)},
        ConditionExpression="attribute_exists(PK)",
        **dynamo.update_expr(set_=set_ or None, remove=remove or None),
    )
    return get_conversation(uid, cid)


def set_conversation_project(uid, cid, project_id) -> dict | None:
    """대화를 프로젝트에 편입하거나(project_id) 해제한다(None). GSI2 키도 같이 바꾼다."""
    conv = get_conversation(uid, cid)
    if conv is None:
        return None
    if project_id:
        dynamo.update(keys.user_pk(uid), keys.conv_sk(cid), set_={
            "project_id": str(project_id),
            "GSI2PK": keys.proj_pk(project_id), "GSI2SK": conv["updated_at"],
        })
    else:
        dynamo.update(keys.user_pk(uid), keys.conv_sk(cid), remove=["project_id", "GSI2PK", "GSI2SK"])
    return get_conversation(uid, cid)


def _touch_update(uid, conv: dict, now: str, add_messages: int = 0) -> dict:
    """대화의 updated_at과 GSI 정렬 키를 함께 바꾸는 Update (트랜잭션에 넣는 형태)."""
    set_ = {"updated_at": now, "GSI1SK": now}
    if conv.get("project_id"):
        set_["GSI2SK"] = now
    upd = dynamo.update_expr(set_=set_, add={"message_count": add_messages} if add_messages else None)
    return {"Update": {
        "TableName": dynamo.TABLE_NAME,
        "Key": {"PK": keys.user_pk(uid), "SK": keys.conv_sk(conv["id"])},
        "ConditionExpression": "attribute_exists(PK)",
        **upd,
    }}


def touch_conversation(uid, cid) -> None:
    conv = get_conversation(uid, cid)
    if conv:
        dynamo.client().transact_write_items(TransactItems=[_touch_update(uid, conv, now_iso())])


def delete_conversation(uid, cid) -> int:
    """AP14: 메시지와 첨부까지 지운다. 반환값은 지운 아이템 수.
    S3의 chats/{cid}/ 파일 삭제는 호출하는 쪽(서비스 계층)이 맡는다."""
    if get_conversation(uid, cid) is None:
        return 0
    children = dynamo.query_all(
        KeyConditionExpression=Key("PK").eq(keys.conv_pk(cid)), ProjectionExpression="PK, SK"
    )
    n = dynamo.delete_keys(children)
    dynamo.table().delete_item(Key={"PK": keys.user_pk(uid), "SK": keys.conv_sk(cid)})
    return n + 1


# ------------------------------------------------------------------ 메시지
def add_message(uid, cid, role: str, content: str, attachment_ids: list[str] | None = None,
                **meta) -> dict:
    """메시지 저장 + 첨부 연결 + 대화 갱신을 한 트랜잭션으로 (AP9, AP10, AP6).

    meta: token_count, model_used, task_category, complexity_score, input_tokens,
          output_tokens, web_search_used, web_search_query
    반환값의 "sk"는 나중에 token_count를 고칠 때(update_message) 쓴다.
    """
    conv = get_conversation(uid, cid)
    if conv is None:
        raise LookupError("conversation not found")
    mid = str(uuid.uuid4())
    now = now_iso()
    msg = {
        "PK": keys.conv_pk(cid), "SK": keys.msg_sk(now, mid),
        "id": mid, "conversation_id": str(cid), "role": role, "content": content,
        "created_at": now, **meta,
    }
    items = [
        {"Put": {"TableName": dynamo.TABLE_NAME, "Item": to_dynamo(msg)}},
        _touch_update(uid, conv, now, add_messages=1),
    ]
    for aid in attachment_ids or []:
        items.append({"Update": {
            "TableName": dynamo.TABLE_NAME,
            "Key": {"PK": keys.conv_pk(cid), "SK": keys.att_sk(aid)},
            # 이 대화의 첨부만 연결된다. 남의 첨부 id를 넣으면 조건 실패로 전체가 취소된다.
            "ConditionExpression": "attribute_exists(PK)",
            **dynamo.update_expr(set_={"message_id": mid}, remove=["ttl"]),
        }})
    dynamo.client().transact_write_items(TransactItems=items)  # 최대 100개 아이템
    out = strip_keys(dynamo.from_dynamo(to_dynamo(msg)))
    out["sk"] = msg["SK"]
    return out


def update_message(cid, sk: str, **fields) -> None:
    dynamo.update(keys.conv_pk(cid), sk, set_={k: v for k, v in fields.items() if v is not None})


def get_conversation_page(cid) -> list[dict]:
    """AP7: 메시지(시간순)와 각 메시지의 첨부를 Query 한 번으로."""
    rows = dynamo.query_partition(keys.conv_pk(cid))
    now = int(time.time())
    messages, by_msg = [], {}
    for r in rows:
        if r["SK"].startswith(keys.MSG_PREFIX):
            m = strip_keys(r)
            m["sk"] = r["SK"]
            messages.append(m)
        elif r["SK"].startswith(keys.ATT_PREFIX) and r.get("message_id"):
            if r.get("ttl") and r["ttl"] < now:
                continue  # TTL 만료됐지만 아직 안 지워진 아이템은 숨긴다
            by_msg.setdefault(r["message_id"], []).append(strip_keys(r))
    for m in messages:  # SK가 MSG#{created_at}#... 이라 이미 시간순
        m["attachments"] = sorted(by_msg.get(m["id"], []), key=lambda a: a["created_at"])
    return messages


def messages_after(cid, after_sk: str | None) -> list[dict]:
    """AP23: 메모리 추출 커서(last_extracted_sk) 이후 메시지만."""
    cond = Key("PK").eq(keys.conv_pk(cid))
    cond = cond & (Key("SK").gt(after_sk) if after_sk else Key("SK").begins_with(keys.MSG_PREFIX))
    rows = dynamo.query_all(KeyConditionExpression=cond)
    out = []
    for r in rows:
        if r["SK"].startswith(keys.MSG_PREFIX):
            m = strip_keys(r)
            m["sk"] = r["SK"]
            out.append(m)
    return out


# ------------------------------------------------------------------ 첨부
def create_attachment(uid, cid, original_name: str, s3_key: str, mime_type: str, size_bytes: int,
                      source: str = "upload", extraction_status: str = "pending", **extra) -> dict:
    """업로드(또는 생성) 시점에 만든다. message_id가 채워질 때까지 ttl이 걸려 있어서,
    메시지에 연결되지 않으면 DynamoDB TTL이 지워 준다 (AP13, 기존 정리 루프 대체)."""
    aid = str(uuid.uuid4())
    ttl_seconds = GENERATED_ORPHAN_SECONDS if source == "generated" else UPLOAD_ORPHAN_SECONDS
    item = {
        "PK": keys.conv_pk(cid), "SK": keys.att_sk(aid),
        "id": aid, "chat_id": str(cid), "original_name": original_name, "s3_key": s3_key,
        "mime_type": mime_type, "size_bytes": size_bytes, "source": source,
        "extraction_status": extraction_status, "created_at": now_iso(),
        "ttl": int(time.time()) + ttl_seconds,
        "GSI3PK": aid, "owner_pk": keys.user_pk(uid), **extra,
    }
    dynamo.table().put_item(Item=to_dynamo(item))
    return strip_keys(dynamo.from_dynamo(to_dynamo(item)))


def get_attachment(uid, aid) -> dict | None:
    """AP11: id로 찾고 소유자를 확인한다."""
    found = dynamo.query_all(IndexName=keys.GSI_BY_ID, KeyConditionExpression=Key("GSI3PK").eq(str(aid)))
    for item in found:
        if item["SK"].startswith(keys.ATT_PREFIX) and item.get("owner_pk") == keys.user_pk(uid):
            # GSI는 최종 일관성이라, 확정 값은 본 테이블에서 다시 읽는다.
            fresh = dynamo.get_item(item["PK"], item["SK"])
            return strip_keys(fresh) if fresh else None
    return None


def list_attachments(cid) -> list[dict]:
    """AP12"""
    return [strip_keys(i) for i in dynamo.query_partition(keys.conv_pk(cid), keys.ATT_PREFIX)]


def update_attachment(cid, aid, **fields) -> None:
    """추출 상태, 추출 텍스트 S3 키 등 갱신."""
    dynamo.update(keys.conv_pk(cid), keys.att_sk(aid), set_={k: v for k, v in fields.items() if v is not None})


def delete_attachment(uid, aid) -> dict | None:
    att = get_attachment(uid, aid)
    if att is None:
        return None
    dynamo.table().delete_item(Key={"PK": keys.conv_pk(att["chat_id"]), "SK": keys.att_sk(aid)})
    return att  # s3_key로 파일 삭제는 호출하는 쪽에서


# ------------------------------------------------------------------ 검색 (AP22)
def search(uid, query: str, limit: int = 20) -> list[dict]:
    """1차 배포용 앱 내 검색: 제목, 메시지 본문, 첨부 파일명 부분 일치(대소문자 무시).
    대화당 1건, 일치 횟수가 많은 순. 대화 수가 많아지면 OpenSearch로 옮긴다."""
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for conv in list_conversations(uid):
        best = None
        title = (conv.get("title") or "")
        if q in title.lower():
            best = {"score": 3 + title.lower().count(q), "snippet": title, "matched_role": None}
        for m in get_conversation_page(conv["id"]):
            text = m.get("content") or ""
            names = " ".join(a["original_name"] for a in m.get("attachments", []))
            hits = text.lower().count(q) + names.lower().count(q)
            if hits and (best is None or hits > best["score"]):
                pos = text.lower().find(q)
                snippet = text[max(0, pos - 40): pos + 80] if pos >= 0 else names
                best = {"score": hits, "snippet": snippet, "matched_role": m["role"]}
        if best:
            results.append({"conversation_id": conv["id"], "title": conv.get("title"), **best})
    results.sort(key=lambda r: r["score"], reverse=True)
    return [{k: v for k, v in r.items() if k != "score"} for r in results[:limit]]
