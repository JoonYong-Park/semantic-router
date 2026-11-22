"""로그인 흐름 (BFF): Google → Cognito → 우리 백엔드.

- GET  /auth/login    : state + PKCE를 만들어 Cognito(→ Google) 로그인 화면으로 보낸다.
- GET  /auth/callback : state 확인 → code를 토큰으로 교환(client secret은 서버에만) → ID 토큰 검증
                        → 프로필 생성 → refresh 토큰을 HttpOnly 쿠키에 넣고 프론트로 돌려보낸다.
- POST /auth/reissue  : refresh 쿠키로 새 access 토큰을 받아 JSON으로 준다 (프론트는 메모리에만 둔다).
- POST /auth/logout   : refresh 토큰 폐기(RevokeToken) + 쿠키 삭제 + Cognito 로그아웃 URL 반환.

토큰을 URL이나 localStorage에 두지 않는다. access 토큰은 응답 본문으로만, refresh 토큰은
HttpOnly·SameSite=Strict·Path=/auth 쿠키로만 오간다.
"""

import base64
import hashlib
import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
import jwt
from flask import Blueprint, jsonify, redirect, request

from app import auth
from app.errors import ApiError

bp = Blueprint("auth", __name__, url_prefix="/auth")
logger = logging.getLogger("uvicorn.error")

COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN", "").rstrip("/")
CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET", "")
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").rstrip("/")
REDIRECT_URI = os.environ.get("COGNITO_REDIRECT_URI", "http://localhost:8000/auth/callback")
LOGOUT_URI = os.environ.get("COGNITO_LOGOUT_URI", FRONTEND_ORIGIN + "/")
# 로컬 http에서도 쓰려면 0. 배포(https)에서는 반드시 1(기본값).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"

OAUTH_COOKIE = "joomini_oauth"     # state + PKCE verifier (10분, 콜백 경로에서만)
REFRESH_COOKIE = "joomini_rt"      # refresh 토큰 (7일, /auth 경로에서만)
REFRESH_MAX_AGE = 7 * 24 * 3600
SCOPES = "openid email profile"
# reissue/logout은 이 헤더가 있어야 받는다. 커스텀 헤더라 다른 사이트의 폼/이미지 요청으로는
# 붙일 수 없고, 붙이려면 CORS 사전 요청을 통과해야 해서 CSRF를 한 번 더 막는다.
CSRF_HEADER = "X-Requested-With"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _token_request(data: dict) -> dict:
    """Cognito 토큰 엔드포인트 호출. client secret은 Basic 인증으로만 보낸다."""
    try:
        r = httpx.post(f"{COGNITO_DOMAIN}/oauth2/token", data=data,
                       auth=(auth.CLIENT_ID, CLIENT_SECRET), timeout=10)
    except httpx.HTTPError as e:
        logger.warning("Cognito 토큰 요청 실패: %s", type(e).__name__)
        raise ApiError(502, "로그인 서버에 연결하지 못했습니다.")
    if r.status_code != 200:
        # 응답 본문에 토큰이 없더라도 그대로 남기지 않고 에러 코드만 남긴다.
        err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error")
        logger.warning("Cognito 토큰 응답 %s (%s)", r.status_code, err)
        raise ApiError(401, "로그인이 만료되었습니다. 다시 로그인하세요.")
    return r.json()


def _set_refresh_cookie(resp, token: str) -> None:
    resp.set_cookie(REFRESH_COOKIE, token, max_age=REFRESH_MAX_AGE, path="/auth",
                    httponly=True, secure=COOKIE_SECURE, samesite="Strict")


def _clear_refresh_cookie(resp) -> None:
    resp.delete_cookie(REFRESH_COOKIE, path="/auth", httponly=True, secure=COOKIE_SECURE, samesite="Strict")


def _check_csrf() -> None:
    if request.headers.get(CSRF_HEADER) != "joomini":
        raise ApiError(403, "허용되지 않은 요청입니다.")
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != FRONTEND_ORIGIN:
        raise ApiError(403, "허용되지 않은 출처입니다.")


def _ensure_profile_from_id_token(id_token: str | None) -> None:
    if not id_token:
        return
    from app.db.repo import users as user_repo

    claims = auth.verify_token(id_token, "id")
    email = claims.get("email") or ""
    user_repo.ensure_profile(claims["sub"], email, claims.get("name") or email.split("@")[0] or "사용자")


@bp.get("/login")
def login():
    if not (COGNITO_DOMAIN and auth.CLIENT_ID and CLIENT_SECRET):
        raise ApiError(503, "로그인 설정(Cognito)이 없습니다.")
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    query = urlencode({
        "response_type": "code",
        "client_id": auth.CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "identity_provider": "Google",  # Cognito 화면을 건너뛰고 바로 Google로
    })
    resp = redirect(f"{COGNITO_DOMAIN}/oauth2/authorize?{query}")
    # Lax: Google/Cognito에서 돌아오는 최상위 GET 이동에는 쿠키가 실려야 한다.
    resp.set_cookie(OAUTH_COOKIE, f"{state}.{verifier}", max_age=600, path="/auth/callback",
                    httponly=True, secure=COOKIE_SECURE, samesite="Lax")
    return resp


@bp.get("/callback")
def callback():
    fail = redirect(f"{FRONTEND_ORIGIN}/?login_error=1")
    fail.delete_cookie(OAUTH_COOKIE, path="/auth/callback")

    saved_state, _, verifier = (request.cookies.get(OAUTH_COOKIE) or "").partition(".")
    state, code = request.args.get("state", ""), request.args.get("code", "")
    if request.args.get("error") or not code or not saved_state or not verifier:
        return fail
    if not secrets.compare_digest(saved_state, state):
        logger.warning("로그인 콜백 state 불일치")
        return fail

    try:
        tokens = _token_request({
            "grant_type": "authorization_code",
            "client_id": auth.CLIENT_ID,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        })
        _ensure_profile_from_id_token(tokens.get("id_token"))
    except (ApiError, jwt.PyJWTError) as e:
        logger.warning("로그인 콜백 실패: %s", type(e).__name__)
        return fail

    resp = redirect(f"{FRONTEND_ORIGIN}/")
    resp.delete_cookie(OAUTH_COOKIE, path="/auth/callback")
    _set_refresh_cookie(resp, tokens["refresh_token"])
    return resp


@bp.post("/reissue")
def reissue():
    _check_csrf()
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not refresh_token:
        raise ApiError(401, "로그인이 필요합니다.")
    try:
        tokens = _token_request({
            "grant_type": "refresh_token",
            "client_id": auth.CLIENT_ID,
            "refresh_token": refresh_token,
        })
        # 액세스 토큰에는 이메일/이름이 없어 ID 토큰으로 프로필을 보장한다 (있으면 그대로 둔다).
        _ensure_profile_from_id_token(tokens.get("id_token"))
    except (ApiError, jwt.PyJWTError):
        resp = jsonify(detail="로그인이 만료되었습니다. 다시 로그인하세요.")
        _clear_refresh_cookie(resp)
        return resp, 401

    resp = jsonify(access_token=tokens["access_token"], expires_in=tokens.get("expires_in", 900))
    resp.headers["Cache-Control"] = "no-store"
    if tokens.get("refresh_token"):  # refresh 토큰 회전을 켜면 새 토큰이 온다
        _set_refresh_cookie(resp, tokens["refresh_token"])
    return resp


@bp.post("/logout")
def logout():
    _check_csrf()
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if refresh_token:
        try:
            # refresh 토큰과 그 토큰으로 발급된 access 토큰을 모두 무효화한다.
            httpx.post(f"{COGNITO_DOMAIN}/oauth2/revoke",
                       data={"token": refresh_token, "client_id": auth.CLIENT_ID},
                       auth=(auth.CLIENT_ID, CLIENT_SECRET), timeout=10)
        except httpx.HTTPError as e:
            logger.warning("토큰 폐기 실패: %s", type(e).__name__)
    # Cognito 로그인 세션 쿠키까지 지워야 다음 로그인 때 계정 선택을 다시 한다.
    logout_url = f"{COGNITO_DOMAIN}/logout?" + urlencode({"client_id": auth.CLIENT_ID, "logout_uri": LOGOUT_URI})
    resp = jsonify(logout_url=logout_url)
    _clear_refresh_cookie(resp)
    return resp
