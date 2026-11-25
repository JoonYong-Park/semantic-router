"""Joomini 백엔드 (Flask, Lambda + Zappa 배포용).

기존 FastAPI 백엔드(backend/)를 기능별로 옮겨 오는 중이다. 진행 상황은
docs/ko/AWS_MIGRATION_LOG.md에 기록한다.
"""

import logging
import os
import threading

from flask import Flask, jsonify

logger = logging.getLogger("uvicorn.error")  # 엔진 코드가 이 이름의 로거를 쓴다 (기존 uvicorn 로그 이름)

_models_started = False
_models_lock = threading.Lock()


def _load_models() -> None:
    """auto 라우팅용 HF 모델 로드. 수동 모드는 로드 전에도 동작한다 (기존 lifespan과 같음)."""
    from app.routing import classifier, complexity

    for name, fn in (("카테고리 분류기", classifier.load_classifier),
                     ("복잡도 임베딩 모델", complexity.load_complexity_model)):
        logger.info("%s 로드 시작...", name)
        try:
            fn()
            logger.info("%s 로드 완료", name)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 로드 실패 - auto 모드를 쓸 수 없습니다: %s", name, exc)


def start_model_loading() -> None:
    """첫 요청 때 한 번만 백그라운드로 로드를 시작한다. 앱 생성 시점에 시작하지 않는 이유:
    Flask 개발 서버의 리로더가 프로세스를 두 번 띄워 모델을 두 번 올리게 된다."""
    global _models_started
    if os.environ.get("ROUTER_MODELS", "on") == "off":
        return
    with _models_lock:
        if _models_started:
            return
        _models_started = True
    threading.Thread(target=_load_models, daemon=True).start()


STREAM_PREFIX = "/stream"


def _strip_stream_prefix(wsgi_app):
    """/stream/conversations/... 를 /conversations/... 로 받는다.

    배포에서는 API Gateway가 /stream/* 만 응답 스트리밍 Lambda(joomini-stream)로 보내고 나머지는
    Zappa Lambda로 보낸다. 앱은 어느 쪽에서 돌든 같은 라우트를 쓰도록 접두사를 떼 준다
    (로컬에서는 같은 서버가 둘 다 받는다)."""

    def middleware(environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == STREAM_PREFIX or path.startswith(STREAM_PREFIX + "/"):
            environ["PATH_INFO"] = path[len(STREAM_PREFIX):] or "/"
        return wsgi_app(environ, start_response)

    return middleware


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Flask(__name__)
    app.wsgi_app = _strip_stream_prefix(app.wsgi_app)
    app.json.ensure_ascii = False  # 한글을 \uXXXX가 아니라 그대로 내보낸다
    app.json.sort_keys = False

    @app.after_request
    def _cors(resp):
        # 로컬 개발용 CORS. 배포 환경에서는 API Gateway의 CORS 설정이 이 역할을 맡는다.
        origin = os.environ.get("FRONTEND_ORIGIN")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return resp

    from app import errors
    from app import auth
    from app.routes import attachments, conversations, misc, projects, settings, usage, users
    from app.routes import auth as auth_routes

    errors.register(app)
    for module in (auth_routes, conversations, attachments, projects, settings, users, usage, misc):
        app.register_blueprint(module.bp)

    # 배포에서는 CloudFront를 거친 요청만 받고(X-Origin-Verify), 그다음 Cognito access 토큰을 검증한다
    # (/health, /auth/*, OPTIONS 제외).
    app.before_request(auth.check_origin)
    app.before_request(auth.authenticate)

    @app.before_request
    def _start_models():
        start_model_loading()

    @app.before_request
    def _seed_demo_user():
        # 로그인을 끈 로컬 개발(AUTH_DISABLED=1)에서만 고정 사용자를 시드한다.
        # 로그인 사용자의 프로필은 /auth/callback에서 ID 토큰으로 만든다.
        if auth.auth_disabled() and not app.config.get("_seeded"):
            from app.db.repo import users as user_repo

            try:
                user_repo.ensure_profile(auth.DEMO_USER_ID, auth.DEMO_USER_EMAIL, auth.DEMO_USER_NAME)
                app.config["_seeded"] = True
            except Exception:
                pass  # 테이블이 아직 없으면 /health가 원인을 알려준다

    @app.get("/health")
    def health():
        from app.db import dynamo
        from app.routing import classifier, complexity

        models = {"category_classifier_ready": classifier.is_ready(),
                  "complexity_model_ready": complexity.is_ready()}
        try:
            status = dynamo.client().describe_table(TableName=dynamo.TABLE_NAME)["Table"]["TableStatus"]
        except Exception as e:  # 테이블이 없거나 연결 실패
            return jsonify(status="degraded", dynamodb=f"error: {type(e).__name__}", **models), 503
        return jsonify(status="ok", dynamodb=status, table=dynamo.TABLE_NAME, **models)

    return app


# Zappa는 모듈 수준의 WSGI 앱 객체를 가리킨다 (zappa_settings.json의 app_function).
app = create_app()
