"""모델 API 키와 Cognito client secret을 Secrets Manager 비밀(joomini/app) 하나에 JSON으로 넣는다.

    python3 backend/scripts/sync_app_secret.py [--profile joomini] [--secret-id joomini/app]

- 읽는 순서: 저장소 루트 .env, backend/.env, 프로세스 환경 변수(GitHub Actions가 Secrets로 넣어 줌, 가장 우선).
- 이번 실행에서 값을 하나도 못 찾은 키는 비밀에 이미 있는 값을 그대로 둔다(덮어써서 지우지 않는다).
- 값은 화면에 출력하지 않는다. 키 이름과 개수만 출력한다.
- 값이 바뀌지 않았으면 새 버전을 만들지 않는다.
- Lambda는 콜드 스타트 때 이 비밀을 읽는다(app/core/app_secrets.py). 키를 바꾼 뒤에는
  배포(새 이미지)나 설정 변경으로 실행 환경이 새로 떠야 반영된다.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SECRET_KEYS = [
    "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_WORKSPACE_ID",
    "TAVILY_API_KEY", "PERPLEXITY_API_KEY", "BRAVE_API_KEY", "HF_TOKEN",
    "COGNITO_CLIENT_SECRET",
]
REGION = "ap-northeast-2"
ROOT = Path(__file__).resolve().parents[2]


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


def aws(profile: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["aws", "--region", REGION, *(["--profile", profile] if profile else []), *args]
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE_NAME", "joomini"),
                    help="빈 값이면 기본 자격 증명(CI의 OIDC 임시 키)을 쓴다")
    ap.add_argument("--secret-id", default="joomini/app")
    args = ap.parse_args()

    found = read_env(ROOT / "backend" / ".env")
    found.update(read_env(ROOT / ".env"))
    found.update({k: os.environ[k] for k in SECRET_KEYS if os.environ.get(k)})

    current: dict = {}
    got = aws(args.profile, "secretsmanager", "get-secret-value", "--secret-id", args.secret_id,
              "--query", "SecretString", "--output", "text", check=False)
    exists = got.returncode == 0
    if exists:
        current = json.loads(got.stdout or "{}")
    elif "ResourceNotFoundException" not in got.stderr:
        sys.exit(f"비밀을 읽지 못했습니다: {got.stderr.strip().splitlines()[-1] if got.stderr else got.returncode}")

    merged = {**current, **found}
    if exists and merged == current:
        print(f"{args.secret_id}: 변경 없음 (키 {len(merged)}개)")
        return

    # 값을 명령줄 인자로 넘기지 않는다(ps에 보임). 권한 600 임시 파일로 넘긴다.
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        os.chmod(f.name, 0o600)
        json.dump(merged, f)
        path = f.name
    try:
        if exists:
            aws(args.profile, "secretsmanager", "put-secret-value", "--secret-id", args.secret_id,
                "--secret-string", f"file://{path}")
        else:
            aws(args.profile, "secretsmanager", "create-secret", "--name", args.secret_id,
                "--description", "Joomini 모델 API 키, Cognito client secret (backend/scripts/sync_app_secret.py)",
                "--secret-string", f"file://{path}")
    finally:
        os.unlink(path)
    missing = [k for k in SECRET_KEYS if k not in merged]
    print(f"{args.secret_id}: 갱신 (키 {len(merged)}개: {', '.join(sorted(merged))})"
          + (f", 없음: {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
