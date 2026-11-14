"""DynamoDB 연결과 공통 헬퍼.

로컬에서는 DYNAMODB_ENDPOINT(예: http://localhost:8001)로 DynamoDB Local에 붙고,
AWS(Lambda)에서는 이 값을 비워 두면 boto3가 실제 DynamoDB 엔드포인트와
Lambda 실행 역할(IAM)의 권한을 그대로 쓴다. 코드는 두 환경에서 같다.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "joomini")
REGION = os.environ.get("AWS_REGION", "ap-northeast-2")


@lru_cache(maxsize=1)
def resource():
    endpoint = os.environ.get("DYNAMODB_ENDPOINT") or None
    return boto3.resource("dynamodb", region_name=REGION, endpoint_url=endpoint)


def table():
    return resource().Table(TABLE_NAME)


def client():
    return resource().meta.client


def now_iso() -> str:
    """UTC ISO 8601 (밀리초, Z). 문자열 정렬 순서가 곧 시간 순서가 되게 고정 폭으로 만든다."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_dynamo(value):
    """boto3는 float를 받지 않는다(Decimal만 허용). 저장 전에 재귀적으로 변환한다."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_dynamo(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [to_dynamo(v) for v in value]
    return value


def from_dynamo(value):
    """Decimal을 int/float로 되돌려 JSON 응답에 바로 쓸 수 있게 한다."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_dynamo(v) for v in value]
    return value


def query_all(**kwargs) -> list[dict]:
    """Query는 호출당 1MB까지만 돌려준다. LastEvaluatedKey로 끝까지 이어 읽는다."""
    items: list[dict] = []
    while True:
        resp = table().query(**kwargs)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            return [from_dynamo(i) for i in items]
        kwargs["ExclusiveStartKey"] = last


def query_partition(pk: str, sk_prefix: str | None = None, **kwargs) -> list[dict]:
    cond = Key("PK").eq(pk)
    if sk_prefix:
        cond = cond & Key("SK").begins_with(sk_prefix)
    return query_all(KeyConditionExpression=cond, **kwargs)


def get_item(pk: str, sk: str) -> dict | None:
    item = table().get_item(Key={"PK": pk, "SK": sk}).get("Item")
    return from_dynamo(item) if item else None


def delete_keys(keys: list[dict]) -> int:
    """BatchWriteItem은 25개씩만 받는다. batch_writer가 나눠 보내고 미처리 항목도 재시도한다."""
    with table().batch_writer() as batch:
        for k in keys:
            batch.delete_item(Key={"PK": k["PK"], "SK": k["SK"]})
    return len(keys)


def update_expr(set_: dict | None = None, remove: list | None = None, add: dict | None = None,
                if_not_exists: dict | None = None) -> dict:
    """UpdateExpression과 이름/값 치환을 만든다.

    name, ttl, source, size, status 같은 DynamoDB 예약어와 부딪히지 않도록
    모든 속성 이름을 #a0, #a1 ... 별칭으로 바꾼다. 반환값은 update_item(**...)이나
    TransactWriteItems의 Update에 그대로 펼쳐 넣는다.
    """
    names: dict[str, str] = {}
    values: dict[str, object] = {}

    def alias(attr: str) -> str:
        for k, v in names.items():
            if v == attr:
                return k
        k = f"#a{len(names)}"
        names[k] = attr
        return k

    def val(v) -> str:
        k = f":v{len(values)}"
        values[k] = to_dynamo(v)
        return k

    parts = []
    sets = [f"{alias(k)} = {val(v)}" for k, v in (set_ or {}).items()]
    sets += [f"{alias(k)} = if_not_exists({alias(k)}, {val(v)})" for k, v in (if_not_exists or {}).items()]
    if sets:
        parts.append("SET " + ", ".join(sets))
    if add:
        parts.append("ADD " + ", ".join(f"{alias(k)} {val(v)}" for k, v in add.items()))
    if remove:
        parts.append("REMOVE " + ", ".join(alias(k) for k in remove))
    out = {"UpdateExpression": " ".join(parts), "ExpressionAttributeNames": names}
    if values:
        out["ExpressionAttributeValues"] = values
    return out


def update(pk: str, sk: str, **kwargs) -> None:
    table().update_item(Key={"PK": pk, "SK": sk}, **update_expr(**kwargs))


def strip_keys(item: dict) -> dict:
    """응답으로 내보낼 때 PK/SK/GSI 키 같은 저장용 속성은 뺀다."""
    return {k: v for k, v in item.items() if k not in _KEY_ATTRS}


_KEY_ATTRS = {"PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "ttl"}
