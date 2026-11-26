"""모델 API 키와 Cognito client secret을 Secrets Manager에서 읽어 환경 변수로 넣는다.

배포 Lambda 환경 변수에는 비밀값을 두지 않고 APP_SECRET_ID(Secrets Manager 비밀 이름)만 둔다.
Lambda 설정을 볼 수 있는 권한만으로 키가 보이지 않게 하려는 것이다.
비밀은 JSON 하나({"OPENAI_API_KEY": "...", ...})이고, 배포 스크립트가 GitHub Secrets 값으로 갱신한다
(backend/scripts/sync_app_secret.py).

엔진 모듈(llm/http.py, routes/auth.py 등)이 import 시점에 os.environ을 읽으므로, create_app()이
라우트를 import하기 전에 load()를 부른다. 콜드 스타트 때 한 번만 읽는다.
APP_SECRET_ID가 없으면(로컬 개발) 아무것도 하지 않고 .env 값을 그대로 쓴다.
"""

import json
import logging
import os

logger = logging.getLogger("uvicorn.error")

_loaded = False


def load() -> None:
    global _loaded
    secret_id = os.environ.get("APP_SECRET_ID", "")
    if _loaded or not secret_id:
        return
    import boto3

    # 읽지 못하면 그대로 예외를 낸다(fail closed). 키 없이 뜬 함수가 요청마다 실패하는 것보다
    # 초기화 실패로 드러나는 편이 원인을 찾기 쉽다.
    values = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])
    for key, value in values.items():
        if isinstance(value, str) and value:
            os.environ.setdefault(key, value)  # 환경 변수에 직접 준 값이 있으면 그쪽이 우선
    _loaded = True
    logger.info("앱 비밀값 %d개를 Secrets Manager에서 읽었습니다", len(values))  # 값과 이름은 남기지 않는다
