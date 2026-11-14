"""현재 사용자 식별.

지금은 기존 백엔드와 같이 고정 데모 사용자(DEMO_USER_ID)를 쓴다.
Cognito를 붙이면 API Gateway의 Cognito 권한 부여자가 검증한 토큰 클레임(sub)을
Zappa가 넘겨주는 원본 이벤트(request.environ["serverless.event"])에서 읽도록
current_user_id()만 바꾸면 된다. 라우트 코드는 그대로 둔다.
"""

import os

from flask import request

DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "00000000-0000-0000-0000-000000000001")
DEMO_USER_NAME = "박준용"
DEMO_USER_EMAIL = "joonyong.park.dev@gmail.com"


def current_user_id() -> str:
    event = request.environ.get("serverless.event") or {}
    claims = (event.get("requestContext", {}).get("authorizer", {}) or {}).get("claims") or {}
    return claims.get("sub") or DEMO_USER_ID
