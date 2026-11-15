"""채팅 첨부와 프로젝트 참고자료의 저장, 추출, 미리보기 (기존 attachments/service.py).

첨부 아이템(DynamoDB)과 프로젝트 파일 아이템은 같은 필드를 쓴다:
  id, original_name, storage_key, mime_type, size_bytes, extraction_status,
  extracted_key (추출 결과 JSON의 저장소 키, 텍스트 추출형만)

기존과 달라진 점
- 추출 텍스트는 아이템이 아니라 저장소 파일({폴더}/{id}.extracted.json)에 둔다.
  DynamoDB 아이템은 400KB가 상한이라 긴 문서의 추출 결과가 들어가지 않는다.
- 추출 결과의 이미지 블록은 절대 경로("path") 대신 저장소 키("key")를 갖는다.
- 백그라운드 추출은 asyncio 태스크 대신 스레드로 돈다 (Flask는 동기 서버).
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import quote

import openpyxl
from PIL import Image
from pypdf import PdfReader

from app import storage
from app.documents import extractor

logger = logging.getLogger("uvicorn.error")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_GENERATED_BYTES = 25 * 1024 * 1024

TEXT_PLAIN_EXTS = {"txt", "md"}
CSV_EXTS = {"csv"}
XLSX_EXTS = {"xlsx"}
DOCX_EXTS = {"docx"}
PPTX_EXTS = {"pptx"}
HWP_EXTS = {"hwp", "hwpx"}
PDF_EXTS = {"pdf"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
ALL_ALLOWED_EXTS = (
    TEXT_PLAIN_EXTS | CSV_EXTS | XLSX_EXTS | DOCX_EXTS | PPTX_EXTS | HWP_EXTS | PDF_EXTS | IMAGE_EXTS
)

MIME_BY_EXT = {
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "hwp": "application/x-hwp",
    "hwpx": "application/hwp+zip",
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}

MAX_TABLE_COLS = 50
MAX_TEXT_PREVIEW_BYTES = 1024 * 1024


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def chat_dir(cid) -> str:
    return f"chats/{cid}"


def project_dir(pid) -> str:
    return f"projects/{pid}"


def new_file_key(folder: str, ext: str) -> tuple[str, str]:
    """원본 파일 이름은 경로에 쓰지 않는다 (경로 조작 방지). (id, 키)를 돌려준다."""
    fid = str(uuid.uuid4())
    return fid, f"{folder}/{fid}.{ext}"


def run_in_background(fn, *args) -> None:
    """응답을 먼저 돌려주고 느린 작업(추출, 제목 생성)을 이어서 한다.
    로컬 Flask 서버용. Lambda에서는 응답 뒤 실행이 멈추므로 Zappa @task로 바꾼다."""
    threading.Thread(target=fn, args=args, daemon=True).start()


# ------------------------------------------------------------------ 추출
def _extract_sync(ext: str, stored_path: Path, folder_path: Path, mime_type: str, filename: str):
    """기존 _extract_sync와 같다. 반환: (최종 경로, 최종 mime, 추출 블록 또는 None, 상태)."""
    blocks = None
    status = "done"
    final_path = stored_path
    final_mime = mime_type
    try:
        if ext in IMAGE_EXTS:
            converted = folder_path / f"{stored_path.stem}.png"
            img = Image.open(stored_path)
            img = extractor.resize_if_needed(img)
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGB")
            img.save(converted, format="PNG")
            if converted != stored_path:
                stored_path.unlink(missing_ok=True)
            final_path, final_mime = converted, "image/png"
        elif ext in PDF_EXTS:
            try:
                PdfReader(str(stored_path))
            except Exception as exc:  # noqa: BLE001
                logger.error("손상된 PDF 업로드: %s (%s)", filename, exc)
                status = "failed"
        else:
            image_dir = folder_path / "images"
            if ext in TEXT_PLAIN_EXTS:
                result = extractor.extract_text_plain(stored_path)
            elif ext in CSV_EXTS:
                result = extractor.extract_csv(stored_path)
            elif ext in XLSX_EXTS:
                result = extractor.extract_xlsx(stored_path)
            elif ext in DOCX_EXTS:
                result = extractor.extract_docx(stored_path, image_dir)
            elif ext in PPTX_EXTS:
                result = extractor.extract_pptx(stored_path, image_dir)
            else:
                result = extractor.extract_hwp(stored_path, image_dir)

            if result.status == "failed":
                status = "failed"
            elif result.fallback_pdf_path:
                # 텍스트가 전혀 없는 스캔본 - PDF로 변환된 결과를 비전 전달형처럼 다룬다.
                final_path, final_mime = Path(result.fallback_pdf_path), "application/pdf"
            else:
                blocks = result.blocks
    except Exception as exc:  # noqa: BLE001
        logger.error("첨부파일 추출 실패 (%s): %s", filename, exc, exc_info=True)
        status = "failed"
    return final_path, final_mime, blocks, status


def extract_file(item: dict, folder: str) -> dict:
    """업로드된 파일을 추출하고, 아이템에 반영할 필드를 돌려준다."""
    ext = ext_of(item["original_name"])
    stored = storage.path(item["storage_key"])
    try:
        final_path, final_mime, blocks, status = _extract_sync(
            ext, stored, storage.path(folder), item["mime_type"], item["original_name"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("첨부파일 백그라운드 처리 실패 (%s): %s", item["original_name"], exc, exc_info=True)
        final_path, final_mime, blocks, status = stored, item["mime_type"], None, "failed"

    fields = {
        "storage_key": storage.key_of(final_path),
        "mime_type": final_mime,
        "extraction_status": status,
    }
    if blocks is not None:
        for b in blocks:
            if b.get("type") == "image" and b.get("path"):
                b["key"] = storage.key_of(Path(b.pop("path")))
        extracted_key = f"{folder}/{item['id']}.extracted.json"
        storage.write(extracted_key, json.dumps(blocks, ensure_ascii=False).encode("utf-8"))
        fields["extracted_key"] = extracted_key
    return fields


def load_blocks(item: dict) -> list[dict] | None:
    """추출 결과 블록 목록. 텍스트 추출형이 아니면 None."""
    key = item.get("extracted_key")
    if not key or not storage.exists(key):
        return None
    try:
        return json.loads(storage.read(key))
    except json.JSONDecodeError:
        return []


def delete_files(item: dict, folder: str) -> None:
    """원본, 스캔본 변환 전 원본, 추출 이미지, 추출 JSON, 미리보기 캐시까지 지운다."""
    storage.delete(item.get("storage_key"))
    original_ext = ext_of(item["original_name"])
    stored_ext = Path(item.get("storage_key") or "").suffix.lstrip(".").lower()
    if original_ext and stored_ext != original_ext:
        storage.delete(f"{folder}/{item['id']}.{original_ext}")
    for b in load_blocks(item) or []:
        if b.get("type") == "image":
            storage.delete(b.get("key"))
    storage.delete(item.get("extracted_key"))
    storage.delete(preview_key(folder, item["id"]))


# ------------------------------------------------------------------ 모델 입력
def blocks_for_model(item: dict) -> list[dict]:
    """첨부 하나를 llm/client.py가 쓰는 내부 content 블록으로 바꾼다 (기존과 같은 모양)."""
    stored_blocks = load_blocks(item)
    if stored_blocks is not None:
        out: list[dict] = []
        for block in stored_blocks:
            if block["type"] == "text":
                if block["text"].strip():
                    out.append({"type": "text", "text": block["text"]})
            elif storage.exists(block.get("key")):
                data = base64.b64encode(storage.read(block["key"])).decode("ascii")
                out.append({"type": "image", "media_type": block.get("mime_type", "image/png"), "data": data})
        return out

    if not storage.exists(item.get("storage_key")):
        return []
    data = base64.b64encode(storage.read(item["storage_key"])).decode("ascii")
    block_type = "image" if item["mime_type"].startswith("image/") else "document"
    return [{"type": block_type, "media_type": item["mime_type"], "data": data}]


# ------------------------------------------------------------------ 미리보기
def preview_key(folder: str, item_id) -> str:
    return f"{folder}/previews/{item_id}.pdf"


def content_disposition(filename: str, inline: bool) -> str:
    disposition = "inline" if inline else "attachment"
    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def download_name(item: dict) -> str:
    """이미지는 PNG로 바꿔 저장하므로 다운로드 이름의 확장자를 실제 저장 형식에 맞춘다."""
    name = item["original_name"]
    stored_ext = Path(item["storage_key"]).suffix.lower()
    if "." in name:
        base, _, name_ext = name.rpartition(".")
        if stored_ext and f".{name_ext.lower()}" != stored_ext:
            return f"{base}{stored_ext}"
    return name


def csv_table(path: Path, sheet_name: str) -> dict:
    text = extractor.decode_text_with_fallback(path.read_bytes())
    all_rows = list(csv.reader(io.StringIO(text)))
    return {
        "name": sheet_name,
        "rows": [["" if c is None else str(c) for c in row][:MAX_TABLE_COLS]
                 for row in all_rows[: extractor.MAX_ROWS + 1]],
        "total_rows": len(all_rows),
        "truncated": len(all_rows) > extractor.MAX_ROWS + 1,
    }


def xlsx_tables(path: Path) -> list[dict]:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets: list[dict] = []
    try:
        for sheet in wb.worksheets:
            rows: list[list[str]] = []
            total = 0
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                total = i + 1
                if i <= extractor.MAX_ROWS:
                    rows.append(["" if c is None else str(c) for c in row][:MAX_TABLE_COLS])
            sheets.append({"name": sheet.title, "rows": rows, "total_rows": total,
                           "truncated": total > extractor.MAX_ROWS + 1})
    finally:
        wb.close()
    return sheets


def plain_text_preview(path: Path) -> dict:
    raw = path.read_bytes()
    return {
        "text": extractor.decode_text_with_fallback(raw[:MAX_TEXT_PREVIEW_BYTES]),
        "truncated": len(raw) > MAX_TEXT_PREVIEW_BYTES,
    }


def hwp_text_preview(item: dict) -> dict | None:
    blocks = load_blocks(item)
    if not blocks:
        return None
    parts = [b["text"] if b.get("type") == "text" else "[이미지]" for b in blocks]
    return {"text": "\n\n".join(parts), "truncated": False}


_preview_locks: dict[str, threading.Lock] = {}
_preview_locks_guard = threading.Lock()


def preview_lock(item_id) -> threading.Lock:
    with _preview_locks_guard:
        return _preview_locks.setdefault(str(item_id), threading.Lock())


def convert_for_preview(stored_path: Path, folder: str, item_id) -> Path | None:
    pdf_path = extractor._convert_to_pdf_with_libreoffice(stored_path)
    if pdf_path is None:
        return None
    final = storage.path(preview_key(folder, item_id))
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f".{item_id}.pdf.tmp")
    shutil.move(pdf_path, tmp)
    tmp.rename(final)
    return final
