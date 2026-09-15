"""토큰 사용량 집계 - 결제 주기 기준 남은 토큰 + 기간별(오늘/주간/월간/연간) 통계.

conversations.py/settings.py/memory.py와 마찬가지로 로그인이 없는 데모라
DEMO_USER_ID 하나를 기준으로 집계한다. Semantic Router/스트리밍/컨텍스트
유지/메모리 로직은 건드리지 않는다. messages 테이블에 이미 있는
input_tokens/output_tokens/model_used/created_at만 읽어서 집계하며,
컬럼을 추가로 두지 않는다.

집계 기준 시간대는 Asia/Seoul 고정 (사용자가 한국 기준으로 "오늘/이번 달"을
인식하므로). DB의 created_at은 UTC(timestamptz)로 저장되어 있어 조회 후
파이썬에서 KST로 변환해 버킷을 나눈다 - 기존 _build_history와 동일하게
"전체를 읽어와서 파이썬에서 처리"하는 이 프로젝트의 방식을 따른다.
"""

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_models import DEMO_USER_ID, Conversation, Message
from app.schemas import (
    UsageByModelOut,
    UsageChartPoint,
    UsagePeriod,
    UsageQuotaOut,
    UsageStatsOut,
    UsageSummaryOut,
)

router = APIRouter(prefix="/usage", tags=["usage"])

KST = ZoneInfo("Asia/Seoul")

# 월 한도/결제일은 전사 공통이라 사용자별 설정이 아니라 환경변수(없으면 기본값)로 관리.
# BILLING_DAY는 1~28 사이 값을 가정한다 (2월에도 항상 존재하는 날짜만 지원 -
# 데모 범위에서 29~31일 결제일까지 다룰 필요는 없다고 판단).
TOKEN_LIMIT_PER_CYCLE = int(os.environ.get("TOKEN_LIMIT_PER_CYCLE", "300000"))
BILLING_DAY = int(os.environ.get("BILLING_DAY", "15"))


def _add_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _current_cycle(today: date) -> tuple[date, date]:
    """오늘이 속한 결제 주기의 [시작일, 종료일]을 반환한다 (둘 다 포함)."""
    if today.day >= BILLING_DAY:
        start = date(today.year, today.month, BILLING_DAY)
    else:
        prev_month = today.month - 1 or 12
        prev_year = today.year - 1 if today.month == 1 else today.year
        start = date(prev_year, prev_month, BILLING_DAY)
    end_year, end_month = _add_month(start.year, start.month)
    end = date(end_year, end_month, BILLING_DAY) - timedelta(days=1)
    return start, end


def _kst_midnight_to_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=KST).astimezone(timezone.utc)


@router.get("/quota", response_model=UsageQuotaOut)
async def get_quota(session: AsyncSession = Depends(get_session)) -> UsageQuotaOut:
    now_kst = datetime.now(KST)
    cycle_start, cycle_end = _current_cycle(now_kst.date())
    cycle_start_utc = _kst_midnight_to_utc(cycle_start)

    result = await session.execute(
        select(
            func.coalesce(func.sum(Message.input_tokens), 0),
            func.coalesce(func.sum(Message.output_tokens), 0),
        )
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == DEMO_USER_ID,
            Message.role == "assistant",
            Message.created_at >= cycle_start_utc,
        )
    )
    input_sum, output_sum = result.one()
    used = int(input_sum) + int(output_sum)
    percent = round(used / TOKEN_LIMIT_PER_CYCLE * 100, 2) if TOKEN_LIMIT_PER_CYCLE else 0.0

    return UsageQuotaOut(
        limit=TOKEN_LIMIT_PER_CYCLE,
        used=used,
        remaining=max(TOKEN_LIMIT_PER_CYCLE - used, 0),
        percent=percent,
        cycle_start=cycle_start.isoformat(),
        cycle_end=cycle_end.isoformat(),
        days_left=(cycle_end - now_kst.date()).days,
    )


def _period_spec(period: UsagePeriod, today: date):
    """기간별로 (버킷 키 목록, KST datetime -> 버킷 키, 짧은 라벨, 전체 라벨,
    범위 설명, 조회 시작 시각(UTC))을 만들어 반환한다."""
    if period == "today":
        keys: list = list(range(24))

        def key_of(dt: datetime):
            return dt.hour

        def label(k):
            return f"{k}시"

        full_label = label
        range_label = f"{today.year}년 {today.month}월 {today.day}일"
        start_utc = _kst_midnight_to_utc(today)

    elif period in ("week", "month"):
        days = 7 if period == "week" else 30
        start_date = today - timedelta(days=days - 1)
        keys = [start_date + timedelta(days=i) for i in range(days)]

        def key_of(dt: datetime):
            return dt.date()

        def label(k: date):
            return f"{k.month}/{k.day}"

        def full_label(k: date):
            return f"{k.month}월 {k.day}일"

        range_label = f"{full_label(start_date)} ~ {full_label(today)}"
        start_utc = _kst_midnight_to_utc(start_date)

    else:  # year - 이번 달 포함 최근 12개월
        months = []
        y, m = today.year, today.month
        for _ in range(12):
            months.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        months.reverse()
        keys = months

        def key_of(dt: datetime):
            return (dt.year, dt.month)

        def label(k: tuple[int, int]):
            return f"{k[1]}월"

        def full_label(k: tuple[int, int]):
            return f"{k[0]}년 {k[1]}월"

        range_label = f"{full_label(months[0])} ~ {full_label(months[-1])}"
        start_utc = _kst_midnight_to_utc(date(months[0][0], months[0][1], 1))

    return keys, key_of, label, full_label, range_label, start_utc


@router.get("/stats", response_model=UsageStatsOut)
async def get_stats(
    period: UsagePeriod, session: AsyncSession = Depends(get_session)
) -> UsageStatsOut:
    now_kst = datetime.now(KST)
    keys, key_of, label_fn, full_label_fn, range_label, start_utc = _period_spec(
        period, now_kst.date()
    )

    result = await session.execute(
        select(Message.created_at, Message.input_tokens, Message.output_tokens, Message.model_used)
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == DEMO_USER_ID,
            Message.role == "assistant",
            Message.created_at >= start_utc,
        )
    )
    rows = result.all()

    # 데이터가 없는 시간대/날짜/월도 0으로 채워서 그래프에서 막대가 안 빠지게 한다.
    buckets = {k: {"input": 0, "output": 0} for k in keys}
    by_model: dict[str, dict[str, int]] = {}
    total_input = 0
    total_output = 0

    for created_at, input_tokens, output_tokens, model_used in rows:
        input_tokens = input_tokens or 0
        output_tokens = output_tokens or 0
        total_input += input_tokens
        total_output += output_tokens

        key = key_of(created_at.astimezone(KST))
        if key in buckets:
            buckets[key]["input"] += input_tokens
            buckets[key]["output"] += output_tokens

        if model_used:
            stat = by_model.setdefault(model_used, {"calls": 0, "input": 0, "output": 0})
            stat["calls"] += 1
            stat["input"] += input_tokens
            stat["output"] += output_tokens

    chart = [
        UsageChartPoint(
            label=label_fn(k),
            full_label=full_label_fn(k),
            input=buckets[k]["input"],
            output=buckets[k]["output"],
        )
        for k in keys
    ]

    by_model_out = sorted(
        (
            UsageByModelOut(
                model=name,
                calls=stat["calls"],
                input=stat["input"],
                output=stat["output"],
                total=stat["input"] + stat["output"],
            )
            for name, stat in by_model.items()
        ),
        key=lambda m: m.total,
        reverse=True,
    )

    return UsageStatsOut(
        period=period,
        range_label=range_label,
        summary=UsageSummaryOut(
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total_input + total_output,
        ),
        chart=chart,
        by_model=by_model_out,
    )
