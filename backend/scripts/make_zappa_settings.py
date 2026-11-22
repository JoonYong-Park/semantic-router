"""zappa_settings.json을 만든다 (git 제외 파일). 컨테이너 이미지 방식 Lambda 배포용.

    python scripts/make_zappa_settings.py --account 123456789012 [--frontend-origin https://...] [--env ../.env]

- environment_variables: 비밀이 아닌 설정. `zappa save-python-settings-file`로 이미지 안
  zappa_settings.py에 들어간다.
- aws_environment_variables: 모델 API 키와 Cognito client secret. Lambda 환경 변수로만 넣는다
  (이미지와 git에는 들어가지 않는다). 값은 화면에 출력하지 않는다.
  읽는 순서: 프로세스 환경 변수(GitHub Actions가 Secrets로 넣어 줌) → 저장소 루트 .env → backend/.env.
"""

import argparse
import json
import os
from pathlib import Path

SECRET_KEYS = [
    "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID",
    "TAVILY_API_KEY", "PERPLEXITY_API_KEY", "BRAVE_API_KEY", "HF_TOKEN",
    "COGNITO_CLIENT_SECRET",
]
# 로그인(Cognito) 설정 중 비밀이 아닌 값. 환경 변수로 바꿀 수 있다.
COGNITO_DEFAULTS = {
    "COGNITO_REGION": "ap-northeast-2",
    "COGNITO_USER_POOL_ID": "ap-northeast-2_eQhPcU4Lt",
    "COGNITO_CLIENT_ID": "3e0c78p3tdkuenvn455j1rd8b1",
    "COGNITO_DOMAIN": "https://ap-northeast-2eqhpcu4lt.auth.ap-northeast-2.amazoncognito.com",
}
REGION = "ap-northeast-2"
TABLE = "joomini"


def read_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if k.strip() in SECRET_KEYS and v:
            out[k.strip()] = v
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--frontend-origin", default="*")
    ap.add_argument("--env", default=str(Path(__file__).resolve().parents[2] / ".env"))
    ap.add_argument("--profile", default="joomini", help="빈 값이면 기본 자격 증명(CI의 OIDC 임시 키)을 쓴다")
    args = ap.parse_args()

    files_bucket = f"joomini-files-{args.account}"
    secrets = read_env(Path(__file__).resolve().parents[1] / ".env")   # backend/.env (Cognito secret)
    secrets.update(read_env(Path(args.env)))                           # 저장소 루트 .env (모델 키)
    secrets.update({k: os.environ[k] for k in SECRET_KEYS if os.environ.get(k)})  # CI가 가장 우선
    cognito = {k: os.environ.get(k) or v for k, v in COGNITO_DEFAULTS.items()}
    # 배포 주소(CloudFront)가 정해지면 콜백/로그아웃 주소를 넘긴다. 없으면 넣지 않는다.
    for k in ("COGNITO_REDIRECT_URI", "COGNITO_LOGOUT_URI"):
        if os.environ.get(k):
            cognito[k] = os.environ[k]
    settings = {
        "dev": {
            "app_function": "app.app",
            "project_name": "joomini",
            "runtime": "python3.12",
            "aws_region": REGION,
            **({"profile_name": args.profile} if args.profile else {}),
            "s3_bucket": f"joomini-zappa-{args.account}",
            # 3GB: LibreOffice 변환과 torch import에 메모리가 필요하다 (신규 계정 기본 상한도 3008MB)
            "memory_size": 3008,
            # API Gateway는 29초에 끊지만, 끊긴 뒤에도 Lambda는 답변을 끝까지 만들어 저장한다
            "timeout_seconds": 120,
            "ephemeral_storage": {"Size": 2048},
            "keep_warm": True,
            "binary_support": True,
            "cors": False,  # CORS는 Flask(app/__init__.py)가 FRONTEND_ORIGIN으로 처리
            "environment_variables": {
                "DYNAMODB_TABLE": TABLE,
                "STORAGE_BUCKET": files_bucket,
                "FRONTEND_ORIGIN": args.frontend_origin,
                # 첫 배포는 수동 모델 선택만. 라우터 모델(3GB+)은 콜드 스타트 문제를 확인한 뒤 켠다.
                "ROUTER_MODELS": "off",
                **cognito,
                "COOKIE_SECURE": "1",
            },
            "aws_environment_variables": secrets,
            "extra_permissions": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                        "dynamodb:Query", "dynamodb:BatchWriteItem", "dynamodb:ConditionCheckItem",
                        "dynamodb:DescribeTable",
                    ],
                    "Resource": [
                        f"arn:aws:dynamodb:{REGION}:{args.account}:table/{TABLE}",
                        f"arn:aws:dynamodb:{REGION}:{args.account}:table/{TABLE}/index/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                    "Resource": [f"arn:aws:s3:::{files_bucket}", f"arn:aws:s3:::{files_bucket}/*"],
                },
            ],
        }
    }
    out = Path(__file__).resolve().parents[1] / "zappa_settings.json"
    out.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"작성: {out} (API 키 {len(secrets)}개를 Lambda 환경 변수로 설정: {', '.join(sorted(secrets))})")


if __name__ == "__main__":
    main()
