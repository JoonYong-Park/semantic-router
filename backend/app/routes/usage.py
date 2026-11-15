"""/usage - 결제 주기 기준 남은 토큰 + 기간별 통계 (기존 usage/router.py).

기존은 화면을 열 때마다 메시지 전체를 SUM했고, 여기서는 답변 저장 때 쌓아 둔
일별/시간별 카운터(repo/usage.py)만 읽는다. 응답 모양과 KST 기준은 같다.
"""

import os
from datetime import date, datetime, timedelta

from flask import Blueprint, jsonify, request

from app.auth import current_user_id
from app.db.repo import usage
from app.errors import ApiError

bp = Blueprint("usage", __name__, url_prefix="/usage")

KST = usage.KST
TOKEN_LIMIT_PER_CYCLE = int(os.environ.get("TOKEN_LIMIT_PER_CYCLE", "300000"))
BILLING_DAY = int(os.environ.get("BILLING_DAY", "15"))


def _current_cycle(today: date) -> tuple[date, date]:
    if today.day >= BILLING_DAY:
        start = date(today.year, today.month, BILLING_DAY)
    else:
        prev_month = today.month - 1 or 12
        prev_year = today.year - 1 if today.month == 1 else today.year
        start = date(prev_year, prev_month, BILLING_DAY)
    end_year, end_month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start, date(end_year, end_month, BILLING_DAY) - timedelta(days=1)


@bp.get("/quota")
def get_quota():
    today = datetime.now(KST).date()
    start, end = _current_cycle(today)
    used = usage.used_since(current_user_id(), start, today)
    return jsonify({
        "limit": TOKEN_LIMIT_PER_CYCLE,
        "used": used,
        "remaining": max(TOKEN_LIMIT_PER_CYCLE - used, 0),
        "percent": round(used / TOKEN_LIMIT_PER_CYCLE * 100, 2) if TOKEN_LIMIT_PER_CYCLE else 0.0,
        "cycle_start": start.isoformat(),
        "cycle_end": end.isoformat(),
        "days_left": (end - today).days,
    })


@bp.get("/stats")
def get_stats():
    period = request.args.get("period")
    if period not in ("today", "week", "month", "year"):
        raise ApiError(422, "period는 today, week, month, year 중 하나여야 합니다.")
    uid = current_user_id()
    today = datetime.now(KST).date()

    if period == "today":
        agg = usage.hourly(uid, today)
        keys = list(range(24))
        bucket_of = {int(h[-2:]): v for h, v in agg["buckets"].items()}  # "YYYY-MM-DDTHH" → 시
        label = full_label = lambda k: f"{k}시"  # noqa: E731
        range_label = f"{today.year}년 {today.month}월 {today.day}일"
    elif period in ("week", "month"):
        days = 7 if period == "week" else 30
        start = today - timedelta(days=days - 1)
        agg = usage.daily(uid, start, today)
        keys = [start + timedelta(days=i) for i in range(days)]
        bucket_of = {date.fromisoformat(d): v for d, v in agg["buckets"].items()}
        label = lambda k: f"{k.month}/{k.day}"  # noqa: E731
        full_label = lambda k: f"{k.month}월 {k.day}일"  # noqa: E731
        range_label = f"{full_label(start)} ~ {full_label(today)}"
    else:  # year: 이번 달 포함 최근 12개월
        y, m = today.year, today.month
        months = []
        for _ in range(12):
            months.append((y, m))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        months.reverse()
        agg = usage.daily(uid, date(months[0][0], months[0][1], 1), today)
        keys = months
        bucket_of: dict = {}
        for d, v in agg["buckets"].items():
            k = (int(d[:4]), int(d[5:7]))
            b = bucket_of.setdefault(k, {"input": 0, "output": 0})
            b["input"] += v["input"]
            b["output"] += v["output"]
        label = lambda k: f"{k[1]}월"  # noqa: E731
        full_label = lambda k: f"{k[0]}년 {k[1]}월"  # noqa: E731
        range_label = f"{full_label(months[0])} ~ {full_label(months[-1])}"

    # 데이터가 없는 시간대/날짜/월도 0으로 채워서 그래프에서 막대가 안 빠지게 한다.
    chart = []
    total_in = total_out = 0
    for k in keys:
        b = bucket_of.get(k, {"input": 0, "output": 0})
        total_in += b["input"]
        total_out += b["output"]
        chart.append({"label": label(k), "full_label": full_label(k), "input": b["input"], "output": b["output"]})

    by_model = sorted(
        ({"model": name, "calls": s["calls"], "input": s["input"], "output": s["output"],
          "total": s["input"] + s["output"]} for name, s in agg["by_model"].items()),
        key=lambda x: x["total"], reverse=True,
    )
    return jsonify({
        "period": period,
        "range_label": range_label,
        "summary": {"input_tokens": total_in, "output_tokens": total_out, "total_tokens": total_in + total_out},
        "chart": chart,
        "by_model": by_model,
    })
