"""현재 사용자 식별 (Cognito JWT 검증).

흐름
- 프론트는 모든 API 요청에 `Authorization: Bearer <access token>`을 붙인다.
- authenticate()가 before_request에서 토큰을 검증하고 g.user에 클레임을 넣는다.
  검증 실패나 토큰 없음은 401 (fail closed). 라우트는 current_user_id()만 부른다.
- 검증: Cognito JWKS 공개키로 RS256 서명, iss(우리 User Pool), token_use=access,
  client_id(우리 앱 클라이언트), exp. JWKS는 캐시하고 모르는 kid가 오면 다시 받는다.
- 로컬에서 로그인 없이 돌려야 할 때만 AUTH_DISABLED=1 → 고정 데모 사용자.

백그라운드 스레드에는 요청 컨텍스트가 없으니 current_user_id()를 부르지 말고 uid를 넘긴다.
"""

import logging
import os

import jwt
from flask import g, request

from app.errors import ApiError

logger = logging.getLogger("uvicorn.error")

DEMO_USER_ID = os.environ.get("DEMO_USER_ID", "00000000-0000-0000-0000-000000000001")
DEMO_USER_NAME = "박준용"
DEMO_USER_EMAIL = "joonyong.park.dev@gmail.com"

REGION = os.environ.get("COGNITO_REGION", "ap-northeast-2")
USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# 토큰 없이 열려 있어야 하는 경로 (헬스 체크, 로그인 흐름 자체)
PUBLIC_PREFIXES = ("/health", "/auth/")

_jwks_client: jwt.PyJWKClient | None = None


def auth_disabled() -> bool:
    return os.environ.get("AUTH_DISABLED") == "1"


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        # 키 목록을 1시간 캐시한다. 모르는 kid(키 교체)면 PyJWKClient가 다시 받아 온다.
        _jwks_client = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", cache_jwk_set=True, lifespan=3600)
    return _jwks_client


def verify_token(token: str, token_use: str) -> dict:
    """Cognito 토큰을 검증하고 클레임을 돌려준다. 실패하면 jwt.PyJWTError 계열 예외."""
    if not USER_POOL_ID or not CLIENT_ID:
        raise jwt.InvalidTokenError("Cognito 설정이 없습니다")
    key = _jwks().get_signing_key_from_jwt(token).key
    options = {"require": ["exp", "iat", "iss", "sub", "token_use"]}
    if token_use == "id":
        # ID 토큰은 aud가 앱 클라이언트 ID다.
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER, audience=CLIENT_ID,
                            options=options, leeway=30)
    else:
        # 액세스 토큰에는 aud가 없고 client_id 클레임으로 확인한다.
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=ISSUER,
                            options={**options, "verify_aud": False}, leeway=30)
        if claims.get("client_id") != CLIENT_ID:
            raise jwt.InvalidTokenError("다른 앱 클라이언트의 토큰")
    if claims.get("token_use") != token_use:
        raise jwt.InvalidTokenError("토큰 종류가 다릅니다")
    return claims


def authenticate() -> None:
    """before_request 훅. 공개 경로와 CORS 사전 요청(OPTIONS)은 통과시킨다."""
    if request.method == "OPTIONS" or request.path.startswith(PUBLIC_PREFIXES):
        return None
    if auth_disabled():
        g.user = {"sub": DEMO_USER_ID}
        return None
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "로그인이 필요합니다.")
    try:
        g.user = verify_token(token.strip(), "access")
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "토큰이 만료되었습니다.")
    except jwt.PyJWTError as e:
        # 토큰 내용은 남기지 않는다. 실패 종류만 기록한다.
        logger.warning("토큰 검증 실패: %s", type(e).__name__)
        raise ApiError(401, "유효하지 않은 토큰입니다.")
    return None


def current_user_id() -> str:
    user = g.get("user")
    if not user or not user.get("sub"):
        # authenticate()를 거치지 않은 경로에서 불리면 사용자 없이 진행하지 않는다.
        raise ApiError(401, "로그인이 필요합니다.")
    return user["sub"]
