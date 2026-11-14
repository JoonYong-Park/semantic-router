"""Joomini 백엔드 (Flask, Lambda + Zappa 배포용).

기존 FastAPI 백엔드(backend/)를 기능별로 옮겨 오는 중이다. 진행 상황은
docs/ko/AWS_MIGRATION_LOG.md에 기록한다.
"""

import os

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

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

    @app.get("/health")
    def health():
        from app.db import dynamo

        try:
            status = dynamo.client().describe_table(TableName=dynamo.TABLE_NAME)["Table"]["TableStatus"]
        except Exception as e:  # 테이블이 없거나 연결 실패
            return jsonify(status="degraded", dynamodb=f"error: {type(e).__name__}"), 503
        return jsonify(status="ok", dynamodb=status, table=dynamo.TABLE_NAME)

    return app


# Zappa는 모듈 수준의 WSGI 앱 객체를 가리킨다 (zappa_settings.json의 app_function).
app = create_app()
