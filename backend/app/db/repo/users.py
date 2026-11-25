"""사용자 프로필과 설정 (AP1, AP2)."""

from boto3.dynamodb.conditions import Attr

from app.db import dynamo, keys
from app.db.dynamo import now_iso, strip_keys, to_dynamo


def get_profile(uid) -> dict | None:
    item = dynamo.get_item(keys.user_pk(uid), keys.PROFILE)
    return strip_keys(item) if item else None


def ensure_profile(uid, email: str, name: str, role: str = "user") -> dict:
    """없으면 만들고, 있으면 그대로 둔다 (지금 코드의 시드 ON CONFLICT DO NOTHING과 같은 동작)."""
    item = {
        "PK": keys.user_pk(uid), "SK": keys.PROFILE,
        "id": str(uid), "email": email, "name": name, "role": role,
        "is_active": True, "created_at": now_iso(),
    }
    try:
        dynamo.table().put_item(Item=to_dynamo(item), ConditionExpression="attribute_not_exists(PK)")
    except dynamo.client().exceptions.ConditionalCheckFailedException:
        pass
    return get_profile(uid)


def list_user_ids() -> list[str]:
    """모든 사용자 id. 정기 작업(메모리 추출)용이라 Scan을 쓴다 - 사용자 수가 적은 동안은 충분하고,
    많아지면 PROFILE 항목에 GSI를 두거나 "최근 활동한 사용자" 목록으로 바꾼다."""
    ids: list[str] = []
    kwargs = {"FilterExpression": Attr("SK").eq(keys.PROFILE), "ProjectionExpression": "id"}
    while True:
        resp = dynamo.table().scan(**kwargs)
        ids += [str(i["id"]) for i in resp.get("Items", []) if i.get("id")]
        if "LastEvaluatedKey" not in resp:
            return ids
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def set_avatar_updated(uid, when: str | None) -> None:
    if when is None:
        dynamo.update(keys.user_pk(uid), keys.PROFILE, remove=["avatar_updated_at"])
    else:
        dynamo.update(keys.user_pk(uid), keys.PROFILE, set_={"avatar_updated_at": when})


def get_settings(uid) -> dict:
    item = dynamo.get_item(keys.user_pk(uid), keys.SETTINGS)
    return strip_keys(item) if item else {"personal_instruction": None, "imported_memory": None}


def update_settings(uid, **fields) -> dict:
    """personal_instruction / imported_memory 중 넘어온 것만 바꾼다. None이면 그 값을 지운다."""
    set_ = {k: v for k, v in fields.items() if v is not None}
    set_["updated_at"] = now_iso()
    remove = [k for k, v in fields.items() if v is None]
    dynamo.update(keys.user_pk(uid), keys.SETTINGS, set_=set_, remove=remove or None)
    return get_settings(uid)
