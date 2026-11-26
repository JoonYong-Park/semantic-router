"""POST /events - HTTP가 아닌 Lambda 이벤트(EventBridge Scheduler, 수동 aws lambda invoke)를 받는다.

Lambda Web Adapter는 API Gateway 요청이 아닌 이벤트를 원본 JSON 그대로 POST /events 로 넘긴다
(AWS_LWA_PASS_THROUGH_PATH, backend/Dockerfile). 이벤트의 command가 아래 허용 목록에 있을 때만 실행한다.

외부에서 /api/events 로 오는 HTTP 요청과 구분하는 방법
- Lambda Web Adapter는 모든 요청에 x-amzn-request-context 헤더를 덮어써서 넣는다(클라이언트가 보낸 값은 버려진다).
  API Gateway 요청이면 요청 정보 JSON 객체이고, HTTP가 아닌 이벤트면 JSON null이다.
- 그래서 이 헤더가 정확히 null일 때만 이벤트로 본다. 그 밖의 요청은 일반 API와 똑같이
  X-Origin-Verify와 Cognito 토큰 검사를 거치고, 이 라우트에서 404를 받는다.

실행 시간: 이벤트에는 Lambda context 객체가 없다. 대신 x-amzn-lambda-context 헤더의 deadline
(epoch ms)으로 남은 시간을 계산한다.
"""

import json
import logging
import time

from flask import Blueprint, jsonify, request

from app.errors import ApiError

logger = logging.getLogger("uvicorn.error")

bp = Blueprint("events", __name__)

EVENTS_PATH = "/events"


def is_lambda_event() -> bool:
    """Lambda Web Adapter가 넘긴 HTTP가 아닌 이벤트인지. auth.check_origin, authenticate도 이걸로 건너뛴다."""
    return (request.method == "POST" and request.path == EVENTS_PATH
            and request.headers.get("x-amzn-request-context", "").strip() == "null")


class _DeadlineContext:
    """run_scheduled(event, context)가 쓰는 get_remaining_time_in_millis()만 흉내 낸다."""

    def __init__(self, deadline_ms: int | None) -> None:
        self._deadline_ms = deadline_ms

    def get_remaining_time_in_millis(self) -> int:
        if not self._deadline_ms:
            return 10**9
        return max(0, int(self._deadline_ms - time.time() * 1000))


def _lambda_context() -> _DeadlineContext:
    try:
        deadline = json.loads(request.headers.get("x-amzn-lambda-context", "") or "{}").get("deadline")
    except (ValueError, AttributeError):
        deadline = None
    return _DeadlineContext(int(deadline) if deadline else None)


def _memory_extract(event, context):
    from app.memory.extract import run_scheduled

    return run_scheduled(event, context)


def _backfill_titles(event, context):
    from app.chat.title import backfill_missing_titles

    return backfill_missing_titles(event, context)


def _warm(event, context):
    # 요청이 들어온 것만으로 실행 환경이 유지되고, before_request가 라우터 모델 로드도 시작한다.
    return {"warm": True}


# 이벤트로 실행할 수 있는 명령. 여기에 없는 이름은 실행하지 않는다.
COMMANDS = {
    "memory_extract": _memory_extract,
    "backfill_titles": _backfill_titles,
    "warm": _warm,
}


@bp.post(EVENTS_PATH)
def handle_event():
    if not is_lambda_event():
        raise ApiError(404, "Not Found")
    event = request.get_json(silent=True) or {}
    command = event.get("command") if isinstance(event, dict) else None
    fn = COMMANDS.get(command)
    if fn is None:
        logger.warning("알 수 없는 이벤트 명령: %r", command)
        raise ApiError(400, "unknown command")
    started = time.monotonic()
    result = fn(event, _lambda_context())
    if command != "warm":
        logger.info("이벤트 명령 완료: %s (%.1f초)", command, time.monotonic() - started)
    return jsonify(command=command, result=result if isinstance(result, (dict, list)) else None)
