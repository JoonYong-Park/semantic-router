"""9개 첨부파일 포맷의 텍스트/이미지 추출.

반환 형식은 두 가지로 통일한다:
- 텍스트 추출형(txt/md/csv/xlsx/docx/pptx/hwp/hwpx): 순서 보존 블록 목록
  `list[dict]` (JSON으로 직렬화해 Attachment.extracted_text에 저장).
  블록은 {"type": "text", "text": str} 또는
  {"type": "image", "path": str(저장된 이미지 파일 절대경로), "mime_type": str} 둘 중 하나.
- 비전 직접 전달형(pdf/img): 이 모듈에서 다루지 않는다 - attachments.py가 원본
  파일을 그대로 두고 extracted_text를 NULL로 남긴다.

스캔본 등으로 하이브리드 포맷(docx/pptx/hwp)에서 텍스트가 전혀 추출되지
않았을 때는 LibreOffice headless로 PDF 변환해 비전 파이프라인에 태우도록
ExtractionResult.fallback_pdf_path에 변환된 PDF 경로를 담아 반환한다.
"""

from __future__ import annotations

import csv
import io
import logging
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger("uvicorn.error")

MAX_ROWS = 1000
MIN_SLIDE_TEXT_CHARS = 50
MAX_IMAGE_DIMENSION = 1568  # Anthropic 권장 상한 - 다른 provider에도 무난히 적용됨

LIBREOFFICE_TIMEOUT = 120

# LibreOffice headless는 프로필(~/.config/libreoffice)을 공유해서 동시에
# 여러 변환이 돌면 프로필 락 충돌로 변환 자체가 실패한다 - 실제로 동시 2개
# 요청을 테스트했을 때도 "exit status 1"로 실패하는 것을 확인했다(허용치를
# 2로 뒀다가 이 문제를 발견하고 1로 낮춤). 그래서 완전 직렬화(허용치 1)로
# 막는다 - 호출 스레드를 그대로 블로킹하므로 asyncio.Semaphore가 아니라
# threading.Semaphore를 쓴다(이 함수는 to_thread로 감싸 호출되거나 동기
# 컨텍스트에서 직접 호출되므로 스레드 기준으로 동작해야 함). 요청마다
# 격리된 -env:UserInstallation 프로필을 쓰면 진짜 동시 실행도 가능하지만,
# 임시 프로필 디렉터리 생성/정리 코드가 추가로 필요해 변경 범위가 커진다 -
# 데모 규모에서는 변환 요청이 드물게 겹치는 정도라 직렬화 대기로 충분하다고
# 판단해 이 방식을 선택.
_LIBREOFFICE_MAX_CONCURRENT = 1
_libreoffice_semaphore = threading.Semaphore(_LIBREOFFICE_MAX_CONCURRENT)


def decode_text_with_fallback(raw: bytes) -> str:
    """UTF-8 실패 시 cp949로 재시도. CSV 추출과 텍스트 미리보기 엔드포인트가
    공유하는 인코딩 폴백 로직."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


@dataclass
class ExtractionResult:
    blocks: list[dict] | None = None  # 텍스트 추출형 결과 (성공 시)
    fallback_pdf_path: str | None = None  # 텍스트가 전혀 없어 PDF로 변환한 경우
    status: str = "done"  # "done" | "failed"
    error: str | None = None


def _save_image_bytes(image_bytes: bytes, image_dir: Path, ext: str = "png") -> str:
    image_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4()}.{ext}"
    path = image_dir / name
    # 문서 내 원본 이미지도 리사이즈 상한을 맞춰서 토큰/전송량을 통제한다.
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = resize_if_needed(img)
        img = img.convert("RGB") if img.mode in ("CMYK",) else img
        img.save(path, format="PNG")
    except Exception:  # noqa: BLE001 - 손상된 이미지는 원본 바이트 그대로 저장
        path.write_bytes(image_bytes)
    return str(path)


def resize_if_needed(img: Image.Image) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_IMAGE_DIMENSION:
        return img
    scale = MAX_IMAGE_DIMENSION / longest
    return img.resize((int(w * scale), int(h * scale)))


# --- TXT / MD ---


def extract_text_plain(path: Path) -> ExtractionResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    return ExtractionResult(blocks=[{"type": "text", "text": text}])


# --- CSV ---


def extract_csv(path: Path) -> ExtractionResult:
    raw = path.read_bytes()
    text = decode_text_with_fallback(raw)

    rows = list(csv.reader(io.StringIO(text)))
    truncated = len(rows) > MAX_ROWS + 1  # +1은 헤더
    rows = rows[: MAX_ROWS + 1]

    if not rows:
        return ExtractionResult(blocks=[{"type": "text", "text": ""}])

    md = _rows_to_markdown_table(rows)
    if truncated:
        md += f"\n\n(주의: 최대 {MAX_ROWS}행까지만 표시됨, 원본은 더 많은 행을 포함)"
    return ExtractionResult(blocks=[{"type": "text", "text": md}])


def _rows_to_markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header, *body = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in body:
        # 헤더보다 열이 적은 행은 빈 칸으로 채워서 마크다운 테이블 깨짐 방지
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")
    return "\n".join(lines)


# --- XLSX ---


def extract_xlsx(path: Path) -> ExtractionResult:
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        rows: list[list[str]] = []
        for i, row in enumerate(sheet.iter_rows(values_only=True)):
            if i > MAX_ROWS:
                break
            rows.append(["" if c is None else str(c) for c in row])
        if not rows:
            continue
        md = _rows_to_markdown_table(rows)
        parts.append(f"### 시트: {sheet.title}\n\n{md}")
    wb.close()
    text = "\n\n".join(parts) if parts else "(빈 워크북)"
    return ExtractionResult(blocks=[{"type": "text", "text": text}])


# --- DOCX ---


def extract_docx(path: Path, image_dir: Path) -> ExtractionResult:
    doc = DocxDocument(str(path))
    blocks: list[dict] = []
    has_text = False

    for para in doc.paragraphs:
        # 문단 안의 인라인 이미지(rId)를 텍스트와 같은 순서로 뽑는다.
        for run in para.runs:
            for blip in run._element.iter(qn("a:blip")):
                r_embed = blip.get(qn("r:embed"))
                if not r_embed:
                    continue
                try:
                    image_part = doc.part.related_parts[r_embed]
                    img_path = _save_image_bytes(image_part.blob, image_dir)
                    blocks.append(
                        {"type": "image", "path": img_path, "mime_type": "image/png"}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("docx 이미지 추출 실패: %s", exc)
        if para.text.strip():
            blocks.append({"type": "text", "text": para.text})
            has_text = True

    for table in doc.tables:
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        if rows:
            blocks.append({"type": "text", "text": _rows_to_markdown_table(rows)})
            has_text = True

    if not has_text:
        # 텍스트가 전혀 없음(스캔본 등) - 이미지가 있어도 순서 정보만으로는
        # 부족하다고 보고 LibreOffice로 PDF 변환해 비전 파이프라인에 위임.
        fallback = _convert_to_pdf_with_libreoffice(path)
        if fallback:
            return ExtractionResult(blocks=None, fallback_pdf_path=fallback)

    return ExtractionResult(blocks=blocks or [{"type": "text", "text": ""}])


# --- PPTX ---


def extract_pptx(path: Path, image_dir: Path) -> ExtractionResult:
    prs = Presentation(str(path))
    blocks: list[dict] = []
    has_text = False

    for slide_idx, slide in enumerate(prs.slides):
        slide_text_parts: list[str] = []
        slide_blocks: list[dict] = []

        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    img_path = _save_image_bytes(shape.image.blob, image_dir)
                    slide_blocks.append(
                        {"type": "image", "path": img_path, "mime_type": "image/png"}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("pptx 이미지 추출 실패: %s", exc)
            elif shape.has_text_frame and shape.text_frame.text.strip():
                text = shape.text_frame.text
                slide_text_parts.append(text)
                slide_blocks.append({"type": "text", "text": text})

        slide_text = "\n".join(slide_text_parts)
        if len(slide_text.strip()) < MIN_SLIDE_TEXT_CHARS:
            # 텍스트가 거의 없는 슬라이드(이미지 위주/스캔) - 슬라이드 전체를
            # 이미지로 렌더링해서 비전 처리로 폴백한다.
            rendered = _render_slide_as_image(path, slide_idx, image_dir)
            if rendered:
                blocks.append(
                    {"type": "image", "path": rendered, "mime_type": "image/png"}
                )
                continue

        blocks.extend(slide_blocks)
        if slide_text.strip():
            has_text = True

    if not has_text:
        fallback = _convert_to_pdf_with_libreoffice(path)
        if fallback:
            return ExtractionResult(blocks=None, fallback_pdf_path=fallback)

    return ExtractionResult(blocks=blocks or [{"type": "text", "text": ""}])


def _render_slide_as_image(pptx_path: Path, slide_index: int, image_dir: Path) -> str | None:
    """슬라이드 하나를 PNG로 렌더링. LibreOffice로 전체 덱을 PDF로 변환한 뒤
    poppler-utils(pdftoppm)로 해당 페이지만 래스터화한다 (둘 다 backend Dockerfile에
    시스템 패키지로 설치되어 있어야 함 - libreoffice, poppler-utils).
    """
    pdf_path = _convert_to_pdf_with_libreoffice(pptx_path)
    if pdf_path is None:
        return None

    image_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = image_dir / f"slide-{uuid.uuid4()}"
    page_num = slide_index + 1
    try:
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-f", str(page_num),
                "-l", str(page_num),
                "-singlefile",
                pdf_path,
                str(out_prefix),
            ],
            check=True,
            timeout=LIBREOFFICE_TIMEOUT,
            capture_output=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("슬라이드 렌더링 실패 (slide=%s): %s", page_num, exc)
        return None

    out_path = f"{out_prefix}.png"
    return out_path if Path(out_path).exists() else None


# --- HWP / HWPX ---


def extract_hwp(path: Path, image_dir: Path) -> ExtractionResult:
    """hwp-hwpx-parser의 통합 Reader API 사용 (HWP5/HWPX 공통 인터페이스).

    실제 설치된 패키지로 확인한 API는 애초에 예상했던 것(HwpDocument,
    .paragraphs/.tables/.images)과 달랐다 - 실사용 중 ImportError로 발견됨.
    실제 API는 `Reader(path)`이고, `reader.text`가 표까지 마크다운으로 이미
    인라인된 완전한 텍스트를 제공한다(실제 hwp 파일로 확인: 표 내용이 text
    안에 등장 순서 그대로 포함되어 있고, reader.tables는 같은 표 데이터에
    대한 별도 접근자일 뿐 - 텍스트와 별개로 또 붙이면 중복됨). 이미지는
    `get_images()`가 각각 `.save(path)`만 제공해서, docx/pptx처럼 문서 내
    정확한 위치에 끼워 넣을 수는 없고 텍스트 뒤에 순서대로 덧붙인다."""
    try:
        from hwp_hwpx_parser import Reader  # type: ignore
    except ImportError as exc:
        logger.error("hwp_hwpx_parser import 실패: %s", exc)
        return ExtractionResult(status="failed", error=str(exc))

    blocks: list[dict] = []
    has_text = False
    try:
        with Reader(str(path)) as reader:
            if reader.is_encrypted:
                return ExtractionResult(status="failed", error="암호로 보호된 문서입니다.")
            if not reader.is_valid:
                return ExtractionResult(status="failed", error="유효하지 않은 HWP/HWPX 파일입니다.")

            text = (reader.text or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
                has_text = True

            for image in reader.get_images():
                tmp_path = image_dir / f"{uuid.uuid4()}.tmp"
                try:
                    image_dir.mkdir(parents=True, exist_ok=True)
                    image.save(tmp_path)
                    img_path = _save_image_bytes(tmp_path.read_bytes(), image_dir)
                    blocks.append(
                        {"type": "image", "path": img_path, "mime_type": "image/png"}
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("hwp 이미지 추출 실패: %s", exc)
                finally:
                    tmp_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("hwp 추출 실패 (%s): %s", path, exc)
        return ExtractionResult(status="failed", error=str(exc))

    if not has_text:
        fallback = _convert_to_pdf_with_libreoffice(path)
        if fallback:
            return ExtractionResult(blocks=None, fallback_pdf_path=fallback)

    return ExtractionResult(blocks=blocks or [{"type": "text", "text": ""}])


# --- LibreOffice fallback (스캔본 등 텍스트 0건인 하이브리드 문서) ---


def _convert_to_pdf_with_libreoffice(path: Path) -> str | None:
    out_dir = path.parent
    try:
        with _libreoffice_semaphore:
            subprocess.run(
                [
                    "libreoffice", "--headless", "--norestore",
                    "--convert-to", "pdf",
                    "--outdir", str(out_dir),
                    str(path),
                ],
                check=True,
                timeout=LIBREOFFICE_TIMEOUT,
                capture_output=True,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("LibreOffice PDF 변환 실패 (%s): %s", path, exc)
        return None

    pdf_path = out_dir / f"{path.stem}.pdf"
    return str(pdf_path) if pdf_path.exists() else None
