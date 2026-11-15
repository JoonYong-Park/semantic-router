"""`joomini` 테이블과 GSI 3개를 만든다. 로컬(DynamoDB Local)과 AWS에 같은 구조로.

로컬:  docker compose -f docker-compose.dev.yml exec backend python scripts/create_table.py (컨테이너 시작 때 자동으로도 실행됨)
AWS:   DYNAMODB_ENDPOINT를 비우고 AWS 자격 증명이 있는 셸에서 python scripts/create_table.py
       (--recreate는 로컬에서만 허용한다. 실제 데이터가 지워지기 때문)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from botocore.exceptions import ClientError  # noqa: E402

from app.db import dynamo  # noqa: E402
from app.db.keys import GSI_BY_ID, GSI_BY_PROJECT, GSI_BY_UPDATED  # noqa: E402


def _gsi(name: str, pk: str, sk: str | None = None) -> dict:
    schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    if sk:
        schema.append({"AttributeName": sk, "KeyType": "RANGE"})
    return {"IndexName": name, "KeySchema": schema, "Projection": {"ProjectionType": "ALL"}}


TABLE_SPEC = {
    "TableName": dynamo.TABLE_NAME,
    "BillingMode": "PAY_PER_REQUEST",  # 온디맨드: 처리량을 미리 정하지 않고 쓴 만큼 과금
    "KeySchema": [
        {"AttributeName": "PK", "KeyType": "HASH"},
        {"AttributeName": "SK", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": name, "AttributeType": "S"}
        for name in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK")
    ],
    "GlobalSecondaryIndexes": [
        _gsi(GSI_BY_UPDATED, "GSI1PK", "GSI1SK"),
        _gsi(GSI_BY_PROJECT, "GSI2PK", "GSI2SK"),
        _gsi(GSI_BY_ID, "GSI3PK"),
    ],
    # TTL 삭제 이벤트를 받아 S3 파일까지 지우기 위해 Streams를 켠다 (설계 문서 4번 AP13).
    "StreamSpecification": {"StreamEnabled": True, "StreamViewType": "OLD_IMAGE"},
}


def main() -> None:
    client = dynamo.client()
    is_local = bool(os.environ.get("DYNAMODB_ENDPOINT"))

    if "--recreate" in sys.argv:
        if not is_local:
            sys.exit("--recreate는 로컬(DYNAMODB_ENDPOINT 설정)에서만 쓸 수 있습니다.")
        try:
            client.delete_table(TableName=dynamo.TABLE_NAME)
            client.get_waiter("table_not_exists").wait(TableName=dynamo.TABLE_NAME)
            print(f"기존 테이블 삭제: {dynamo.TABLE_NAME}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

    try:
        client.create_table(**TABLE_SPEC)
        print(f"테이블 생성 요청: {dynamo.TABLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceInUseException":
            print(f"이미 있음: {dynamo.TABLE_NAME} (변경 없음)")
            return
        raise

    client.get_waiter("table_exists").wait(TableName=dynamo.TABLE_NAME)

    # TTL은 테이블이 ACTIVE가 된 뒤에 켤 수 있고, 테이블당 한 시간에 한 번만 바꿀 수 있다.
    for _ in range(10):
        try:
            client.update_time_to_live(
                TableName=dynamo.TABLE_NAME,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
            )
            print("TTL 활성화: ttl 속성")
            break
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ResourceInUseException", "ValidationException"):
                time.sleep(2)
                continue
            raise

    desc = client.describe_table(TableName=dynamo.TABLE_NAME)["Table"]
    print("GSI:", ", ".join(g["IndexName"] for g in desc.get("GlobalSecondaryIndexes", [])))


if __name__ == "__main__":
    main()
