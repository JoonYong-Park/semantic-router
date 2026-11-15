"""채팅 첨부와 프로젝트 참고자료가 같이 쓰는 뷰어 응답 (원본/표/텍스트/PDF 미리보기).
기존 attachments/router.py와 projects/router.py에 똑같이 두 번 있던 로직을 하나로 모았다."""

import concurrent.futures

from flask import send_file

from app import storage
from app.errors import ApiError
from app.services import files

PREVIEW_CONVERT_TIMEOUT = 60
_converter = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _existing_path(item: dict, missing: str = "파일을 찾을 수 없습니다."):
    try:
        path = storage.path(item["storage_key"])
    except ValueError:
        raise ApiError(404, "첨부파일을 찾을 수 없습니다.")
    if not path.exists():
        raise ApiError(404, missing)
    return path


def file_response(item: dict, download: bool):
    path = _existing_path(item)
    is_image = item["mime_type"].startswith("image/")
    is_pdf = item["mime_type"] == "application/pdf"
    inline = (is_image or is_pdf) and not download
    resp = send_file(path, mimetype=item["mime_type"])
    resp.headers["Content-Disposition"] = files.content_disposition(files.download_name(item), inline)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def table_response(item: dict) -> dict:
    ext = files.ext_of(item["original_name"])
    path = _existing_path(item)
    if ext in files.CSV_EXTS:
        return {"sheets": [files.csv_table(path, item["original_name"])]}
    if ext in files.XLSX_EXTS:
        return {"sheets": files.xlsx_tables(path)}
    raise ApiError(400, "표 미리보기를 지원하지 않는 형식입니다.")


def text_response(item: dict) -> dict:
    ext = files.ext_of(item["original_name"])
    if ext in files.HWP_EXTS:
        if item["extraction_status"] == "pending":
            raise ApiError(409, "파일을 아직 처리 중입니다. 잠시 후 다시 시도하세요.")
        out = files.hwp_text_preview(item)
        if out is None:
            raise ApiError(400, "추출된 텍스트가 없습니다.")
        return out
    if ext in files.TEXT_PLAIN_EXTS:
        return files.plain_text_preview(_existing_path(item))
    raise ApiError(400, "텍스트 미리보기를 지원하지 않는 형식입니다.")


def _pdf(path):
    resp = send_file(path, mimetype="application/pdf")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def preview_pdf_response(item: dict, folder: str):
    ext = files.ext_of(item["original_name"])
    if ext not in (files.DOCX_EXTS | files.PPTX_EXTS):
        raise ApiError(400, "미리보기 변환을 지원하지 않는 형식입니다.")
    cache_key = files.preview_key(folder, item["id"])
    if storage.exists(cache_key):
        return _pdf(storage.path(cache_key))
    stored = _existing_path(item, "원본 파일을 찾을 수 없습니다.")

    with files.preview_lock(item["id"]):
        if storage.exists(cache_key):  # 기다리는 동안 다른 요청이 변환을 끝냈을 수 있다
            return _pdf(storage.path(cache_key))
        future = _converter.submit(files.convert_for_preview, stored, folder, item["id"])
        try:
            result = future.result(timeout=PREVIEW_CONVERT_TIMEOUT)
        except concurrent.futures.TimeoutError:
            raise ApiError(422, "미리보기 변환 시간이 초과되었습니다. 다운로드해서 확인하세요.")
        if result is None:
            raise ApiError(422, "미리보기를 만들 수 없습니다. 다운로드해서 확인하세요.")
    return _pdf(result)
