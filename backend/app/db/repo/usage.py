"""토큰 사용량 카운터 (AP20, AP21).

지금 코드는 사용량 화면을 열 때마다 메시지 전체를 읽어 SUM한다. DynamoDB에는 집계가 없어서,
assistant 메시지를 저장할 때 일별/시간별 카운터 아이템에 원자적으로 ADD해 두고
화면에서는 카운터만 읽는다. 날짜와 시각은 지금 코드와 같은 KST 기준.
"""

import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from boto3.dynamodb.conditions import Key

from app.db import dynamo, keys

KST = ZoneInfo("Asia/Seoul")
HOURLY_TTL_SECONDS = 3 * 24 * 3600  # 시간별 카운터는 "오늘" 그래프에만 쓰므로 3일 뒤 자동 삭제


def record_usage(uid, model: str, input_tokens: int, output_tokens: int,
                 at: datetime | None = None) -> None:
    at = (at or datetime.now(timezone.utc)).astimezone(KST)
    day = at.strftime("%Y-%m-%d")
    hour = at.strftime("%Y-%m-%dT%H")
    counters = {"input_tokens": int(input_tokens or 0), "output_tokens": int(output_tokens or 0), "calls": 1}
    dynamo.update(
        keys.user_pk(uid), keys.usage_day_sk(day, model),
        add=counters, set_={"day": day, "model": model},
    )
    dynamo.update(
        keys.user_pk(uid), keys.usage_hour_sk(hour, model),
        add=counters, set_={"hour": hour, "model": model, "ttl": int(time.time()) + HOURLY_TTL_SECONDS},
    )


def _range(uid, prefix: str, start: str, end: str) -> list[dict]:
    # SK between "USAGE#D#2026-11-01" and "USAGE#D#2026-11-30~"
    # '~'는 영문자, 숫자, '#'보다 뒤에 정렬돼서 end 날짜의 모든 모델 아이템까지 포함한다.
    return dynamo.query_all(
        KeyConditionExpression=Key("PK").eq(keys.user_pk(uid))
        & Key("SK").between(f"{prefix}{start}", f"{prefix}{end}~"),
    )


def used_since(uid, start: date, end: date | None = None) -> int:
    """AP20: 결제 주기 시작일부터의 입력+출력 토큰 합계."""
    end = end or datetime.now(KST).date()
    rows = _range(uid, keys.USAGE_DAY_PREFIX, start.isoformat(), end.isoformat())
    return sum(r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in rows)


def daily(uid, start: date, end: date) -> dict:
    """AP21 (주, 월, 연): {날짜: {"input","output"}}와 모델별 합계."""
    rows = _range(uid, keys.USAGE_DAY_PREFIX, start.isoformat(), end.isoformat())
    return _aggregate(rows, "day")


def hourly(uid, day: date) -> dict:
    """AP21 (오늘): {YYYY-MM-DDTHH: {...}}와 모델별 합계."""
    d = day.isoformat()
    rows = _range(uid, keys.USAGE_HOUR_PREFIX, f"{d}T00", f"{d}T23")
    return _aggregate(rows, "hour")


def _aggregate(rows: list[dict], bucket_attr: str) -> dict:
    buckets: dict = defaultdict(lambda: {"input": 0, "output": 0})
    by_model: dict = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
    for r in rows:
        b = buckets[r[bucket_attr]]
        b["input"] += r.get("input_tokens", 0)
        b["output"] += r.get("output_tokens", 0)
        m = by_model[r["model"]]
        m["calls"] += r.get("calls", 0)
        m["input"] += r.get("input_tokens", 0)
        m["output"] += r.get("output_tokens", 0)
    return {"buckets": dict(buckets), "by_model": dict(by_model)}


def month_range(day: date, months_back: int = 11) -> tuple[date, date]:
    """연 단위 그래프용 시작일(11개월 전 1일)과 끝일."""
    y, m = day.year, day.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1), day + timedelta(days=0)
