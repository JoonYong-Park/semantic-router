"""zappa_settings.json을 만든다 (git 제외 파일). 컨테이너 이미지 방식 Lambda 배포용.

    python scripts/make_zappa_settings.py --account 123456789012 [--frontend-origin https://...] [--env ../.env]

- environment_variables: 비밀이 아닌 설정. `zappa save-python-settings-file`로 이미지 안
  zappa_settings.py에 들어간다.
- aws_environment_variables: 모델 API 키. 저장소 루트 .env에서 읽어 Lambda 환경 변수로만 넣는다
  (이미지와 git에는 들어가지 않는다). 값은 화면에 출력하지 않는다.
"""

import argparse
import json
from pathlib import Path

SECRET_KEYS = [
    "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID",
    "TAVILY_API_KEY", "PERPLEXITY_API_KEY", "BRAVE_API_KEY", "HF_TOKEN",
]
REGION = "ap-northeast-2"
TABLE = "joomini"


def read_env(path: Path) -> dict:
    out = {}
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
    ap.add_argument("--profile", default="joomini")
    args = ap.parse_args()

    files_bucket = f"joomini-files-{args.account}"
    secrets = read_env(Path(args.env))
    settings = {
        "dev": {
            "app_function": "app.app",
            "project_name": "joomini",
            "runtime": "python3.12",
            "aws_region": REGION,
            "profile_name": args.profile,
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
