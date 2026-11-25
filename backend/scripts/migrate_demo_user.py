"""로그인 도입 전 데모 사용자 데이터를 실제(Google 로그인) 사용자에게 옮긴다.

로그인 전에는 모든 데이터가 고정 데모 사용자(DEMO_USER_ID) 아래에 있었다. 로그인 후에는
Cognito sub가 사용자 키가 되므로, 그 계정으로 한 번 로그인해 프로필을 만든 뒤 이 스크립트를 돌린다.

옮기는 것
- USER#{demo} 파티션의 대화·프로젝트·설정·메모리·사용량 → USER#{new} (PROFILE은 새 계정 것을 쓴다)
  같이 바꾸는 속성: user_id, GSI1PK(대화 최신순)
- 다른 파티션(CONV#, PROJ#)의 첨부·파일·메모리 중 owner_pk가 데모 사용자인 것 → owner_pk만 바꾼다
대화 안의 메시지(CONV#{cid} / MSG#...)는 대화 id 아래에 있어서 그대로 둬도 따라온다.

기본은 dry-run(무엇을 바꿀지 출력만). 실제로 바꾸려면 --apply.
  python backend/scripts/migrate_demo_user.py --to-email you@gmail.com [--profile joomini] [--apply]
"""

import argparse
import os
import sys

import boto3
from boto3.dynamodb.conditions import Attr

DEFAULT_DEMO = "00000000-0000-0000-0000-000000000001"


def scan_all(table, **kw):
    resp = table.scan(**kw)
    yield from resp["Items"]
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kw)
        yield from resp["Items"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to-email", required=True, help="옮겨 받을 사용자의 Google 이메일 (먼저 한 번 로그인해야 한다)")
    ap.add_argument("--demo-id", default=os.environ.get("DEMO_USER_ID", DEFAULT_DEMO))
    ap.add_argument("--table", default=os.environ.get("DYNAMODB_TABLE", "joomini"))
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE_NAME", "joomini") or None)
    ap.add_argument("--region", default="ap-northeast-2")
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다 (없으면 dry-run)")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource("dynamodb").Table(args.table)
    old_pk = f"USER#{args.demo_id}"

    # 새 사용자 찾기: 이메일이 같은 PROFILE 중 데모 사용자가 아닌 것
    profiles = [
        p for p in scan_all(table, FilterExpression=Attr("SK").eq("PROFILE") & Attr("email").eq(args.to_email))
        if p["PK"] != old_pk
    ]
    if len(profiles) != 1:
        print(f"{args.to_email} 프로필이 {len(profiles)}개입니다. 그 계정으로 한 번 로그인한 뒤 다시 실행하세요.")
        return 1
    new_pk = profiles[0]["PK"]
    new_uid = new_pk.split("#", 1)[1]
    print(f"{old_pk} → {new_pk} ({'적용' if args.apply else 'dry-run'})")

    moved = owner_fixed = skipped = 0
    for item in list(scan_all(table)):
        if item["PK"] == old_pk:
            if item["SK"] == "PROFILE":
                continue
            new_item = dict(item, PK=new_pk)
            if "user_id" in new_item:
                new_item["user_id"] = new_uid
            if new_item.get("GSI1PK") == old_pk:
                new_item["GSI1PK"] = new_pk
            if new_item.get("owner_pk") == old_pk:
                new_item["owner_pk"] = new_pk
            print(f"  move  {item['SK']}")
            if args.apply:
                try:
                    table.put_item(Item=new_item, ConditionExpression="attribute_not_exists(PK)")
                except table.meta.client.exceptions.ConditionalCheckFailedException:
                    print(f"  skip  {item['SK']} (새 계정에 이미 있음)")
                    skipped += 1
                    continue
                table.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
            moved += 1
        elif item.get("owner_pk") == old_pk:
            print(f"  owner {item['PK']} / {item['SK']}")
            if args.apply:
                table.update_item(
                    Key={"PK": item["PK"], "SK": item["SK"]},
                    UpdateExpression="SET owner_pk = :n",
                    ExpressionAttributeValues={":n": new_pk},
                )
            owner_fixed += 1

    print(f"옮김 {moved}, owner 변경 {owner_fixed}, 건너뜀 {skipped}")
    if not args.apply:
        print("dry-run이었습니다. 실제로 바꾸려면 --apply를 붙이세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
