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


def create_app() -> Flask:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = Flask(__name__)
    app.json.ensure_ascii = False  # 한글을 \uXXXX가 아니라 그대로 내보낸다
    app.json.sort_keys = False

    @app.after_request
    def _cors(resp):
        # 로컬 개발용 CORS. 배포 환경에서는 API Gateway의 CORS 설정이 이 역할을 맡는다.
        origin = os.environ.get("FRONTEND_ORIGIN")
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        return resp

    from app import errors
    from app.routes import attachments, conversations, misc, projects, settings, usage, users

    errors.register(app)
    for module in (conversations, attachments, projects, settings, users, usage, misc):
        app.register_blueprint(module.bp)

    @app.before_request
    def _start_models():
        start_model_loading()

    @app.before_request
    def _seed_demo_user():
        # 로그인 전 단계라 기존 백엔드처럼 고정 사용자를 시드한다 (콜드 스타트마다 1회).
        if not app.config.get("_seeded"):
            from app.auth import DEMO_USER_EMAIL, DEMO_USER_ID, DEMO_USER_NAME
            from app.db.repo import users as user_repo

            try:
                user_repo.ensure_profile(DEMO_USER_ID, DEMO_USER_EMAIL, DEMO_USER_NAME)
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
