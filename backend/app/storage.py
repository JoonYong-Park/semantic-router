"""업로드 파일 저장소. 키(예: "chats/{cid}/{aid}.docx")로 파일을 다룬다.

지금은 로컬 디스크(UPLOAD_DIR)만 구현했다. DynamoDB 아이템에는 절대 경로가 아니라
키만 저장하므로, Lambda로 갈 때 이 모듈만 S3 구현으로 바꾸면 된다
(path()는 S3에서 /tmp로 내려받은 경로를 돌려주는 식).
"""

import os
import shutil
from pathlib import Path

ROOT = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))


def path(key: str) -> Path:
    """키에 해당하는 로컬 경로. 키가 저장소 밖을 가리키면(../ 등) 거부한다."""
    p = (ROOT / key).resolve()
    if not p.is_relative_to(ROOT.resolve()):
        raise ValueError(f"저장소 밖을 가리키는 키입니다: {key}")
    return p


def key_of(p: Path) -> str:
    """로컬 경로를 키로 바꾼다 (추출기가 만든 파일 경로를 저장할 때)."""
    return str(Path(p).resolve().relative_to(ROOT.resolve()))


def write(key: str, data: bytes) -> Path:
    p = path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(p)  # 쓰는 도중에 다른 요청이 반쯤 쓰인 파일을 읽지 않게
    return p


def read(key: str) -> bytes:
    return path(key).read_bytes()


def exists(key: str | None) -> bool:
    return bool(key) and path(key).exists()


def delete(key: str | None) -> None:
    if key:
        path(key).unlink(missing_ok=True)


def delete_prefix(prefix: str) -> None:
    """폴더 하나(예: chats/{cid}) 전체 삭제."""
    shutil.rmtree(path(prefix), ignore_errors=True)
