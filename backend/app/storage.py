"""업로드 파일 저장소. 키(예: "chats/{cid}/{aid}.docx")로 파일을 다룬다.

- 로컬: UPLOAD_DIR 디스크에 바로 저장한다 (STORAGE_BUCKET 없음).
- Lambda: STORAGE_BUCKET(S3)이 원본이고, UPLOAD_DIR(/tmp)은 그 캐시다.
  Lambda 디스크는 실행 환경마다 따로이고 사라질 수 있어서, 파일의 정본은 항상 S3에 둔다.

DynamoDB 아이템에는 절대 경로가 아니라 키만 저장하므로, 두 방식에서 데이터 형태가 같다.
추출기나 LibreOffice처럼 로컬 파일이 필요한 코드는 path(key)로 받은 경로를 쓰고,
그 코드가 새로 만든 파일은 upload_local(key)로 S3에 올린다.
"""

import os
import shutil
from functools import lru_cache
from pathlib import Path

ROOT = Path(os.environ.get("UPLOAD_DIR", "/data/uploads"))
BUCKET = os.environ.get("STORAGE_BUCKET") or None


@lru_cache(maxsize=1)
def _s3():
    import boto3

    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))


def _local(key: str) -> Path:
    p = (ROOT / key).resolve()
    if not p.is_relative_to(ROOT.resolve()):
        raise ValueError(f"저장소 밖을 가리키는 키입니다: {key}")
    return p


def path(key: str) -> Path:
    """키에 해당하는 로컬 경로. S3 모드에서는 캐시에 없으면 내려받는다
    (없는 키면 내려받지 않고 경로만 돌려주므로 exists()로 먼저 확인한다)."""
    p = _local(key)
    if BUCKET and not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            _s3().download_file(BUCKET, key, str(p))
        except Exception:  # noqa: BLE001 - 없는 키
            p.unlink(missing_ok=True)
    return p


def local_dir(prefix: str) -> Path:
    """추출기와 LibreOffice가 결과 파일을 쓸 로컬 작업 폴더."""
    p = _local(prefix)
    p.mkdir(parents=True, exist_ok=True)
    return p


def key_of(p: Path) -> str:
    """로컬 경로를 키로 바꾼다 (추출기가 만든 파일 경로를 저장할 때)."""
    return str(Path(p).resolve().relative_to(ROOT.resolve()))


def upload_local(key: str) -> None:
    """로컬에서 만들어진 파일(변환본, 추출 이미지, 미리보기)을 S3에 올린다. 로컬 모드에서는 할 일 없음."""
    if BUCKET:
        p = _local(key)
        if p.exists():
            _s3().upload_file(str(p), BUCKET, key)


def write(key: str, data: bytes) -> Path:
    p = _local(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(p)  # 쓰는 도중에 다른 요청이 반쯤 쓰인 파일을 읽지 않게
    upload_local(key)
    return p


def read(key: str) -> bytes:
    return path(key).read_bytes()


def exists(key: str | None) -> bool:
    if not key:
        return False
    if _local(key).exists():
        return True
    if BUCKET:
        try:
            _s3().head_object(Bucket=BUCKET, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def delete(key: str | None) -> None:
    if not key:
        return
    _local(key).unlink(missing_ok=True)
    if BUCKET:
        _s3().delete_object(Bucket=BUCKET, Key=key)


def delete_prefix(prefix: str) -> None:
    """폴더 하나(예: chats/{cid}) 전체 삭제."""
    shutil.rmtree(_local(prefix), ignore_errors=True)
    if BUCKET:
        s3 = _s3()
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix.rstrip("/") + "/"):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                s3.delete_objects(Bucket=BUCKET, Delete={"Objects": objs})
