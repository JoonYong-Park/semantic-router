"""문서 툴(create_document / create_spreadsheet / create_presentation)의 렌더러.

툴 인자(JSON)를 받아 검증하고 파일 바이트를 만든다. 모두 동기 함수라
tools.py가 asyncio.to_thread로 감싸 호출한다. DB/파일 저장은 하지 않는다
(attachments.save_generated_file이 담당).

- 검증 실패는 DocValidationError로 올린다. 메시지는 모델이 고쳐서 다시
  호출할 수 있게 "어느 필드가 왜"를 구체적으로 적는다.
- 고칠 수 있는 문제(표 행 길이 불일치, 분량 초과 등)는 렌더링을 계속하고
  warnings에 기록한다.
- 폰트 정책: docx/xlsx/pptx는 사용자가 Windows에서 열 것이므로 "맑은 고딕"을
  지정하고, pdf는 서버에 설치된 "Noto Sans CJK KR"로 만든 docx를 LibreOffice로
  변환한다(서버에 맑은 고딕이 없어 그대로 변환하면 대체 글꼴로 바뀜).
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app import extraction

OFFICE_FONT = "맑은 고딕"
PDF_FONT = "Noto Sans CJK KR"
MAX_WARNINGS = 20


class DocValidationError(ValueError):
    """모델이 인자를 고쳐서 다시 호출해야 하는 검증 실패."""


@dataclass
class RenderResult:
    content: bytes
    ext: str
    pages_or_slides: int | None
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class _Warnings(list):
    """경고가 수백 개로 불어나 결과 JSON이 커지지 않게 상한을 둔다."""

    def add(self, message: str) -> None:
        if len(self) < MAX_WARNINGS:
            self.append(message)
        elif len(self) == MAX_WARNINGS:
            self.append("(경고가 더 있지만 생략됨)")


# --- 공통 유틸 ---

_FILENAME_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str, fallback: str) -> str:
    """확장자 없는 파일명을 받아 경로/금지 문자를 지우고 길이를 제한한다.
    비면 fallback(보통 title), 그것도 비면 "문서"."""
    for candidate in (name, fallback, "문서"):
        cleaned = _FILENAME_FORBIDDEN.sub("", candidate or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
        # 모델이 확장자를 붙여 보낸 경우 중복되지 않게 뗀다
        cleaned = re.sub(r"\.(docx|pdf|md|txt|xlsx|csv|pptx|hwpx)$", "", cleaned, flags=re.I)
        if cleaned:
            return cleaned[:80]
    return "문서"


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise DocValidationError(message)


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _is_rows(value) -> bool:
    return isinstance(value, list) and all(_is_str_list(r) for r in value)


def _normalize_rows(rows: list[list[str]], where: str, warnings: _Warnings) -> list[list[str]]:
    """첫 행(헤더) 길이에 맞춰 나머지 행을 자르거나 빈 칸으로 채운다."""
    if not rows:
        return rows
    width = len(rows[0])
    fixed = [rows[0]]
    for i, row in enumerate(rows[1:], start=1):
        if len(row) != width:
            warnings.add(f"{where} {i}행의 칸 수({len(row)})가 헤더({width})와 달라 맞춤")
            row = (row + [""] * width)[:width]
        fixed.append(row)
    return fixed


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


# =====================================================================
# create_document: docx / pdf / md / txt
# =====================================================================

MAX_BLOCKS = 500
MAX_PARAGRAPH_CHARS = 5000
MAX_TABLE_ROWS = 200
DOC_BLOCK_TYPES = ("heading", "paragraph", "bullets", "numbered", "table")


def document_formats() -> tuple[str, ...]:
    """create_document가 받는 형식."""
    return ("docx", "pdf", "md", "txt", "hwpx")


def _validate_document(args: dict, warnings: _Warnings) -> list[dict]:
    formats = document_formats()
    _require(args.get("format") in formats,
             f"format은 {'/'.join(formats)} 중 하나여야 합니다 (받은 값: {args.get('format')!r})")
    _require(isinstance(args.get("title"), str), "title은 문자열이어야 합니다")
    blocks = args.get("blocks")
    _require(isinstance(blocks, list), "blocks는 배열이어야 합니다")
    _require(len(blocks) <= MAX_BLOCKS, f"blocks가 {len(blocks)}개, 최대 {MAX_BLOCKS}개입니다")

    cleaned: list[dict] = []
    for i, b in enumerate(blocks):
        where = f"blocks[{i}]"
        _require(isinstance(b, dict), f"{where}는 객체여야 합니다")
        btype = b.get("type")
        _require(btype in DOC_BLOCK_TYPES,
                 f"{where}.type은 {'/'.join(DOC_BLOCK_TYPES)} 중 하나여야 합니다 (받은 값: {btype!r})")
        text = b.get("text") if isinstance(b.get("text"), str) else ""
        items = b.get("items") if _is_str_list(b.get("items")) else []
        rows = b.get("rows") if _is_rows(b.get("rows")) else []
        level = b.get("level") if isinstance(b.get("level"), int) else 0

        if btype in ("heading", "paragraph"):
            _require(len(text) <= MAX_PARAGRAPH_CHARS,
                     f"{where}.text가 {len(text)}자, 최대 {MAX_PARAGRAPH_CHARS}자입니다 (문단을 나누세요)")
            if not text.strip():
                warnings.add(f"{where}({btype})의 text가 비어 있어 건너뜀")
                continue
            if btype == "heading" and level not in (1, 2, 3):
                warnings.add(f"{where}.level={level}은 1~3이어야 해서 {min(max(level, 1), 3)}로 조정")
                level = min(max(level, 1), 3)
        elif btype in ("bullets", "numbered"):
            items = [it for it in items if it.strip()]
            if not items:
                warnings.add(f"{where}({btype})의 items가 비어 있어 건너뜀")
                continue
            for j, it in enumerate(items):
                _require(len(it) <= MAX_PARAGRAPH_CHARS,
                         f"{where}.items[{j}]가 {len(it)}자, 최대 {MAX_PARAGRAPH_CHARS}자입니다")
        else:  # table
            _require(len(rows) <= MAX_TABLE_ROWS,
                     f"{where}.rows가 {len(rows)}행, 최대 {MAX_TABLE_ROWS}행입니다 (표를 나누세요)")
            if not rows or not rows[0]:
                warnings.add(f"{where}(table)의 rows가 비어 있어 건너뜀")
                continue
            rows = _normalize_rows(rows, f"{where}.rows", warnings)
        cleaned.append({"type": btype, "level": level, "text": text, "items": items, "rows": rows})

    # 모델이 title과 똑같은 heading을 첫 블록으로 또 넣는 경우가 많아(실측)
    # 제목이 두 번 찍히지 않게 뺀다.
    title = (args.get("title") or "").strip()
    if title and cleaned and cleaned[0]["type"] == "heading" and cleaned[0]["text"].strip() == title:
        cleaned.pop(0)
    return cleaned


def _set_docx_style_font(style, font_name: str) -> None:
    """스타일 글꼴을 라틴/동아시아 모두 font_name으로 고정한다. 기본 템플릿의
    제목 스타일은 테마 글꼴(asciiTheme 등)을 써서, 테마 속성을 지우지 않으면
    지정한 글꼴이 무시된다."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    style.font.name = font_name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), font_name)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        rfonts.attrib.pop(qn(attr), None)


def _restart_numbering(doc, paragraph) -> None:
    """python-docx의 "List Number"는 문서 전체가 번호 하나를 공유해서, 번호
    목록 블록이 여러 개면 두 번째 목록이 이어서 번호가 매겨진다. 블록마다 새
    w:num(시작 1)을 만들어 붙여 목록별로 1부터 다시 시작하게 한다."""
    from docx.oxml.ns import qn

    style_numpr = doc.styles["List Number"].element.pPr.numPr
    style_num_id = style_numpr.numId.val
    numbering = doc.part.numbering_part.numbering_definitions._numbering
    abstract_id = None
    for num in numbering.findall(qn("w:num")):
        if num.numId == style_num_id:
            abstract_id = num.abstractNumId.val
            break
    if abstract_id is None:
        return
    new_num = numbering.add_num(abstract_id)
    new_num.add_lvlOverride(ilvl=0).add_startOverride(1)
    numpr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    numpr.get_or_add_ilvl().val = 0
    numpr.get_or_add_numId().val = new_num.numId


def _build_docx(title: str, blocks: list[dict], font_name: str, warnings: _Warnings) -> bytes:
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm

    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Cm(2.54))

    for style_name in ("Normal", "Title", "Heading 1", "Heading 2", "Heading 3",
                       "List Bullet", "List Number"):
        _set_docx_style_font(doc.styles[style_name], font_name)

    if title.strip():
        doc.add_paragraph(title, style="Title")

    numbering_failed = False
    for b in blocks:
        if b["type"] == "heading":
            doc.add_paragraph(b["text"], style=f"Heading {b['level']}")
        elif b["type"] == "paragraph":
            doc.add_paragraph(b["text"])
        elif b["type"] == "bullets":
            for it in b["items"]:
                doc.add_paragraph(it, style="List Bullet")
        elif b["type"] == "numbered":
            for k, it in enumerate(b["items"]):
                p = doc.add_paragraph(it, style="List Number")
                if k == 0 and not numbering_failed:
                    try:
                        _restart_numbering(doc, p)
                        first_num_id = p._p.pPr.numPr.numId.val
                    except Exception:  # noqa: BLE001
                        numbering_failed = True
                        warnings.add("번호 목록 번호 재시작에 실패해 목록끼리 번호가 이어질 수 있음")
                elif not numbering_failed:
                    numpr = p._p.get_or_add_pPr().get_or_add_numPr()
                    numpr.get_or_add_ilvl().val = 0
                    numpr.get_or_add_numId().val = first_num_id
        else:  # table
            rows = b["rows"]
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    cell = table.cell(r, c)
                    cell.text = value
                    if r == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
                        shd = OxmlElement("w:shd")
                        shd.set(qn("w:val"), "clear")
                        shd.set(qn("w:color"), "auto")
                        shd.set(qn("w:fill"), "D9E2F3")
                        cell._tc.get_or_add_tcPr().append(shd)
            doc.add_paragraph()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _build_markdown(title: str, blocks: list[dict]) -> str:
    lines: list[str] = []
    if title.strip():
        lines += [f"# {title}", ""]
    for b in blocks:
        if b["type"] == "heading":
            lines += [f"{'#' * (b['level'] + 1)} {b['text']}", ""]
        elif b["type"] == "paragraph":
            lines += [b["text"], ""]
        elif b["type"] == "bullets":
            lines += [f"- {it}" for it in b["items"]] + [""]
        elif b["type"] == "numbered":
            lines += [f"{k}. {it}" for k, it in enumerate(b["items"], start=1)] + [""]
        else:
            rows = b["rows"]
            lines.append("| " + " | ".join(_md_cell(v) for v in rows[0]) + " |")
            lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
            for row in rows[1:]:
                lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_text(title: str, blocks: list[dict]) -> str:
    lines: list[str] = []
    if title.strip():
        lines += [title, "=" * max(_display_width(title), 4), ""]
    for b in blocks:
        if b["type"] == "heading":
            underline = "=" if b["level"] == 1 else "-"
            lines += [b["text"], underline * max(_display_width(b["text"]), 4), ""]
        elif b["type"] == "paragraph":
            lines += [b["text"], ""]
        elif b["type"] == "bullets":
            lines += [f"- {it}" for it in b["items"]] + [""]
        elif b["type"] == "numbered":
            lines += [f"{k}. {it}" for k, it in enumerate(b["items"], start=1)] + [""]
        else:
            lines += [" | ".join(row) for row in b["rows"]] + [""]
    return "\n".join(lines).rstrip() + "\n"


def _build_hwpx(title: str, blocks: list[dict], warnings: _Warnings) -> bytes:
    """python-hwpx로 HWPX를 만든다. 제목/문단/표는 라이브러리 API를 그대로
    쓰고, 목록(bullets/numbered)은 목록 서식 API의 안정성을 검증하지 못해 접두사
    붙인 문단으로 대체한다. 한글(Hancom)에서 실제로 열리는지는 사람이 확인해야 함."""
    from hwpx import HwpxDocument

    doc = HwpxDocument.new()
    if title.strip():
        doc.add_heading(title, level=1)
    list_fallback = False
    for b in blocks:
        if b["type"] == "heading":
            doc.add_heading(b["text"], level=min(b["level"] + 1, 3))
        elif b["type"] == "paragraph":
            doc.add_paragraph(b["text"])
        elif b["type"] in ("bullets", "numbered"):
            list_fallback = True
            for k, it in enumerate(b["items"], start=1):
                doc.add_paragraph(f"{'•' if b['type'] == 'bullets' else f'{k}.'} {it}")
        else:
            rows = b["rows"]
            table = doc.add_table(len(rows), len(rows[0]))
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    table.set_cell_text(r, c, value)
    if list_fallback:
        warnings.add("hwpx는 목록 서식 대신 '•'/'1.' 접두사를 붙인 일반 문단으로 만들었음")
    return doc.to_bytes()


def render_document(args: dict) -> RenderResult:
    warnings = _Warnings()
    blocks = _validate_document(args, warnings)
    fmt = args["format"]
    title = args.get("title") or ""
    _require(blocks or title.strip(), "title과 blocks가 모두 비어 있어 만들 내용이 없습니다")
    meta = {"blocks": len(blocks)}

    if fmt == "docx":
        return RenderResult(_build_docx(title, blocks, OFFICE_FONT, warnings), "docx", None, warnings, meta)
    if fmt == "md":
        return RenderResult(_build_markdown(title, blocks).encode("utf-8"), "md", None, warnings, meta)
    if fmt == "txt":
        return RenderResult(_build_text(title, blocks).encode("utf-8"), "txt", None, warnings, meta)
    if fmt == "hwpx":
        return RenderResult(_build_hwpx(title, blocks, warnings), "hwpx", None, warnings, meta)

    # pdf: 서버 폰트로 만든 docx를 기존 LibreOffice 변환 경로(동시 1개 세마포어,
    # 타임아웃 공유)로 PDF로 바꾼다.
    from pypdf import PdfReader

    docx_bytes = _build_docx(title, blocks, PDF_FONT, warnings)
    with tempfile.TemporaryDirectory(prefix="docgen-") as tmp:
        docx_path = Path(tmp) / "document.docx"
        docx_path.write_bytes(docx_bytes)
        pdf_path = extraction._convert_to_pdf_with_libreoffice(docx_path)
        if not pdf_path:
            raise RuntimeError("PDF 변환(LibreOffice)에 실패했습니다")
        pdf_bytes = Path(pdf_path).read_bytes()
    pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    return RenderResult(pdf_bytes, "pdf", pages, warnings, meta)


# =====================================================================
# create_spreadsheet: xlsx / csv
# =====================================================================

MAX_SHEETS = 10
MAX_SHEET_ROWS = 2000
COLUMN_TYPES = ("text", "integer", "decimal", "percent", "date", "currency_krw")
NUMERIC_TYPES = ("integer", "decimal", "currency_krw")  # 합계 행 대상 (percent는 합산 의미가 없어 제외)
NUMBER_FORMATS = {
    "integer": "#,##0",
    "decimal": "#,##0.00",
    "percent": "0.0%",
    "date": "yyyy-mm-dd",
    "currency_krw": "#,##0",
}
# 스프레드시트에서 수식/DDE로 해석될 수 있는 시작 문자(CSV/수식 주입 방지)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_SHEET_NAME_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


def _convert_cell(value: str, ctype: str):
    """문자열 셀을 열 타입에 맞게 변환한다. 실패하면 None(호출부가 텍스트로 둠)."""
    raw = value.strip()
    if ctype == "text" or raw == "":
        return raw if ctype == "text" else None
    if ctype == "date":
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None
    cleaned = re.sub(r"[,\s원₩%]", "", raw)
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    if ctype == "percent":
        return float(number / 100)
    if ctype in ("integer", "currency_krw") and number == number.to_integral_value():
        return int(number)
    return float(number)


def _validate_spreadsheet(args: dict) -> list[dict]:
    _require(args.get("format") in ("xlsx", "csv"),
             f"format은 xlsx/csv 중 하나여야 합니다 (받은 값: {args.get('format')!r})")
    sheets = args.get("sheets")
    _require(isinstance(sheets, list) and sheets, "sheets는 1개 이상의 배열이어야 합니다")
    _require(len(sheets) <= MAX_SHEETS, f"sheets가 {len(sheets)}개, 최대 {MAX_SHEETS}개입니다")
    for i, s in enumerate(sheets):
        where = f"sheets[{i}]"
        _require(isinstance(s, dict), f"{where}는 객체여야 합니다")
        cols = s.get("columns")
        _require(isinstance(cols, list) and cols, f"{where}.columns는 1개 이상이어야 합니다")
        for j, c in enumerate(cols):
            _require(isinstance(c, dict) and isinstance(c.get("header"), str),
                     f"{where}.columns[{j}].header는 문자열이어야 합니다")
            _require(c.get("type") in COLUMN_TYPES,
                     f"{where}.columns[{j}].type은 {'/'.join(COLUMN_TYPES)} 중 하나여야 합니다 (받은 값: {c.get('type')!r})")
        rows = s.get("rows")
        _require(_is_rows(rows), f"{where}.rows는 문자열 배열의 배열이어야 합니다 (숫자도 문자열로)")
        _require(len(rows) <= MAX_SHEET_ROWS,
                 f"{where}.rows가 {len(rows)}행, 최대 {MAX_SHEET_ROWS}행입니다 (시트를 나누세요)")
        _require(s.get("total_row") in ("none", "sum"),
                 f"{where}.total_row는 none/sum 중 하나여야 합니다 (받은 값: {s.get('total_row')!r})")
    return sheets


def _sheet_title(name: str, index: int, used: set[str]) -> str:
    title = _SHEET_NAME_FORBIDDEN.sub("", name or "").strip().strip("'")[:31] or f"Sheet{index + 1}"
    base, n = title, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(title.lower())
    return title


def _prepare_sheet(sheet: dict, where: str, warnings: _Warnings) -> tuple[list[dict], list[list]]:
    """행 길이를 맞추고 셀을 타입 변환한다. 변환 실패 셀은 텍스트로 둔다."""
    cols = sheet["columns"]
    width = len(cols)
    converted_rows: list[list] = []
    failures: dict[int, int] = {}
    for r, row in enumerate(sheet["rows"]):
        if len(row) != width:
            warnings.add(f"{where}.rows[{r}]의 칸 수({len(row)})가 열 수({width})와 달라 맞춤")
            row = (row + [""] * width)[:width]
        out = []
        for c, value in enumerate(row):
            ctype = cols[c]["type"]
            converted = _convert_cell(value, ctype)
            if converted is None and value.strip() and ctype != "text":
                failures[c] = failures.get(c, 0) + 1
                converted = value  # 텍스트로 둠
            elif converted is None:
                converted = ""
            out.append(converted)
        converted_rows.append(out)
    for c, count in failures.items():
        warnings.add(f"{where}의 '{cols[c]['header']}'열({cols[c]['type']}) {count}칸을 숫자/날짜로 바꾸지 못해 텍스트로 둠")
    return cols, converted_rows


def _shown_text(value, ctype: str) -> str:
    """엑셀에 서식이 적용돼 실제로 보이는 문자열(열 너비 계산용)."""
    if isinstance(value, (date, datetime)):
        return "0000-00-00"
    if isinstance(value, (int, float)):
        if ctype == "percent":
            return f"{value * 100:,.1f}%"
        if ctype == "decimal":
            return f"{value:,.2f}"
        return f"{value:,.0f}"
    return str(value)


def _build_xlsx(sheets: list[dict], warnings: _Warnings) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    header_font = Font(name=OFFICE_FONT, bold=True)
    body_font = Font(name=OFFICE_FONT)
    header_fill = PatternFill("solid", fgColor="D9E2F3")

    for i, sheet in enumerate(sheets):
        where = f"sheets[{i}]"
        ws = wb.create_sheet(_sheet_title(sheet.get("name", ""), i, used))
        cols, rows = _prepare_sheet(sheet, where, warnings)
        widths = [_display_width(c["header"]) for c in cols]

        for c, col in enumerate(cols, start=1):
            cell = ws.cell(row=1, column=c, value=col["header"])
            cell.data_type = "s"  # 헤더도 수식으로 해석되지 않게
            cell.font, cell.fill = header_font, header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for r, row in enumerate(rows, start=2):
            for c, value in enumerate(row, start=1):
                ctype = cols[c - 1]["type"]
                if value == "":
                    continue  # 빈 칸은 셀을 만들지 않는다
                cell = ws.cell(row=r, column=c, value=value)
                cell.font = body_font
                if isinstance(value, str):
                    # 텍스트(또는 변환 실패) 셀은 =, +, -, @로 시작해도 수식으로
                    # 저장되지 않도록 문자열 타입을 강제한다(수식 주입 방지).
                    cell.data_type = "s"
                else:
                    cell.number_format = NUMBER_FORMATS[ctype]
                widths[c - 1] = max(widths[c - 1], _display_width(_shown_text(value, ctype)))

        if sheet["total_row"] == "sum" and rows:
            total_r = len(rows) + 2
            numeric_cols = [c for c, col in enumerate(cols, start=1) if col["type"] in NUMERIC_TYPES]
            if not numeric_cols:
                warnings.add(f"{where}.total_row=sum이지만 숫자형 열이 없어 합계 행을 만들지 않음")
            else:
                if cols[0]["type"] == "text":
                    label = ws.cell(row=total_r, column=1, value="합계")
                    label.font = header_font
                for c in numeric_cols:
                    letter = get_column_letter(c)
                    cell = ws.cell(row=total_r, column=c, value=f"=SUM({letter}2:{letter}{total_r - 1})")
                    cell.font = header_font
                    ctype = cols[c - 1]["type"]
                    cell.number_format = NUMBER_FORMATS[ctype]
                    # 합계는 각 값보다 자릿수가 커서 열 너비를 합계 기준으로도 맞춘다
                    # (안 그러면 엑셀에서 ###으로 보임).
                    total = sum(r[c - 1] for r in rows if isinstance(r[c - 1], (int, float)))
                    widths[c - 1] = max(widths[c - 1], _display_width(_shown_text(total, ctype)))

        ws.freeze_panes = "A2"
        for c, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(c)].width = min(max(w + 2, 8), 50)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _plain_number(value: float | int) -> str:
    """CSV용 숫자 문자열 - %g처럼 지수 표기(1.234e+06)가 나오지 않게 한다."""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def _csv_value(value, ctype: str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and ctype == "percent":
        return f"{_plain_number(round(value * 100, 10))}%"
    if isinstance(value, (int, float)):
        return _plain_number(value)
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        text = "'" + text  # 엑셀에서 열어도 수식으로 실행되지 않게
    return text


def _build_csv(sheets: list[dict], warnings: _Warnings) -> bytes:
    if len(sheets) > 1:
        warnings.add(f"CSV는 시트를 하나만 담을 수 있어 첫 시트만 저장함 (나머지 {len(sheets) - 1}개 생략)")
    sheet = sheets[0]
    cols, rows = _prepare_sheet(sheet, "sheets[0]", warnings)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([_csv_value(c["header"], "text") for c in cols])
    for row in rows:
        writer.writerow([_csv_value(v, cols[c]["type"]) for c, v in enumerate(row)])
    if sheet["total_row"] == "sum" and rows:
        total = []
        for c, col in enumerate(cols):
            if col["type"] in NUMERIC_TYPES:
                total.append(_csv_value(sum(v for v in (r[c] for r in rows) if isinstance(v, (int, float))), col["type"]))
            elif c == 0 and col["type"] == "text":
                total.append("합계")
            else:
                total.append("")
        writer.writerow(total)
    return buf.getvalue().encode("utf-8-sig")


def render_spreadsheet(args: dict) -> RenderResult:
    warnings = _Warnings()
    sheets = _validate_spreadsheet(args)
    fmt = args["format"]
    meta = {"sheets": len(sheets), "rows": sum(len(s["rows"]) for s in sheets)}
    if fmt == "xlsx":
        return RenderResult(_build_xlsx(sheets, warnings), "xlsx", None, warnings, meta)
    return RenderResult(_build_csv(sheets, warnings), "csv", None, warnings, meta)


# =====================================================================
# create_presentation: pptx
# =====================================================================

MAX_SLIDES = 30
SLIDE_LAYOUTS = ("title", "bullets", "two_column", "table", "section")
# 분량 예산 - 넘으면 렌더링은 하되 넘침 가능성을 warnings로 알린다(자동 축소 없음).
BUDGET_TITLE_CHARS = 40
BUDGET_BULLET_CHARS = 80
BUDGET_BULLETS = 6
BUDGET_TWO_COLUMN_BULLETS = 5
BUDGET_TABLE_ROWS = 8
BUDGET_TABLE_COLS = 5

# 디자인 상수 - 회사 템플릿으로 바꿀 때 이 dict만 고치면 된다.
PPT_THEME = {
    "font": OFFICE_FONT,
    "slide_w_in": 13.333,
    "slide_h_in": 7.5,
    "margin_in": 0.6,
    "title_size": 30,
    "cover_title_size": 40,
    "subtitle_size": 20,
    "body_size": 20,
    "bullet_space_pt": 10,
    "table_size": 14,
    "text_color": "222222",
    "muted_color": "666666",
    "accent_color": "2F5597",
    "inverse_color": "FFFFFF",
    "table_header_fill": "2F5597",
    "table_stripe_fill": "EEF2F8",
}


def _validate_presentation(args: dict, warnings: _Warnings) -> list[dict]:
    _require(isinstance(args.get("title"), str), "title은 문자열이어야 합니다")
    slides = args.get("slides")
    _require(isinstance(slides, list) and slides, "slides는 1장 이상의 배열이어야 합니다")
    _require(len(slides) <= MAX_SLIDES, f"slides가 {len(slides)}장, 최대 {MAX_SLIDES}장입니다")
    cleaned = []
    for i, s in enumerate(slides):
        where = f"slides[{i}]"
        _require(isinstance(s, dict), f"{where}는 객체여야 합니다")
        layout = s.get("layout")
        _require(layout in SLIDE_LAYOUTS,
                 f"{where}.layout은 {'/'.join(SLIDE_LAYOUTS)} 중 하나여야 합니다 (받은 값: {layout!r})")
        slide = {
            "layout": layout,
            "title": s.get("title") if isinstance(s.get("title"), str) else "",
            "subtitle": s.get("subtitle") if isinstance(s.get("subtitle"), str) else "",
            "bullets": [b for b in (s.get("bullets") if _is_str_list(s.get("bullets")) else []) if b.strip()],
            "right_bullets": [b for b in (s.get("right_bullets") if _is_str_list(s.get("right_bullets")) else []) if b.strip()],
            "table_rows": s.get("table_rows") if _is_rows(s.get("table_rows")) else [],
            "notes": s.get("notes") if isinstance(s.get("notes"), str) else "",
        }
        if layout == "table":
            _require(slide["table_rows"] and slide["table_rows"][0],
                     f"{where}.layout이 table인데 table_rows가 비어 있습니다")
            slide["table_rows"] = _normalize_rows(slide["table_rows"], f"{where}.table_rows", warnings)
            if len(slide["table_rows"]) > BUDGET_TABLE_ROWS or len(slide["table_rows"][0]) > BUDGET_TABLE_COLS:
                warnings.add(
                    f"{where}.table_rows가 {len(slide['table_rows'])}행×{len(slide['table_rows'][0])}열 "
                    f"(권장 {BUDGET_TABLE_ROWS}행×{BUDGET_TABLE_COLS}열 이하) - 표가 슬라이드를 넘칠 수 있음"
                )
        if not slide["title"].strip() and layout != "table":
            warnings.add(f"{where}.title이 비어 있음")
        if len(slide["title"]) > BUDGET_TITLE_CHARS:
            warnings.add(f"{where}.title이 {len(slide['title'])}자 (권장 {BUDGET_TITLE_CHARS}자 이하) - 줄바꿈/넘침 가능")
        limit = BUDGET_TWO_COLUMN_BULLETS if layout == "two_column" else BUDGET_BULLETS
        for key in ("bullets", "right_bullets") if layout == "two_column" else ("bullets",):
            if layout in ("bullets", "two_column"):
                if len(slide[key]) > limit:
                    warnings.add(f"{where}.{key}가 {len(slide[key])}개 (권장 {limit}개 이하) - 넘칠 수 있음")
                for j, b in enumerate(slide[key]):
                    if len(b) > BUDGET_BULLET_CHARS:
                        warnings.add(f"{where}.{key}[{j}]가 {len(b)}자 (권장 {BUDGET_BULLET_CHARS}자 이하)")
        if layout == "bullets" and not slide["bullets"]:
            warnings.add(f"{where}.layout이 bullets인데 bullets가 비어 있음")
        cleaned.append(slide)
    return cleaned


def _set_run_font(run, size_pt: int, color_hex: str, bold: bool = False) -> None:
    """런 글꼴을 라틴/동아시아 모두 테마 글꼴로 지정한다. python-pptx의
    font.name은 라틴(a:latin)만 바꾸므로 a:ea를 직접 넣는다(한글은 ea를 씀)."""
    from pptx.dml.color import RGBColor
    from pptx.oxml.ns import qn
    from pptx.util import Pt

    font = run.font
    font.size = Pt(size_pt)
    font.bold = bold
    font.color.rgb = RGBColor.from_string(color_hex)
    font.name = PPT_THEME["font"]
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = rpr.makeelement(qn(tag), {})
            rpr.append(el)
        el.set("typeface", PPT_THEME["font"])


def _add_textbox(slide, left, top, width, height, lines, size_pt, color_hex,
                 bold=False, align=None, anchor=None, para_space_pt=0):
    from pptx.util import Inches, Pt

    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    if anchor is not None:
        tf.vertical_anchor = anchor
    for k, text in enumerate(lines):
        p = tf.paragraphs[0] if k == 0 else tf.add_paragraph()
        if align is not None:
            p.alignment = align
        if para_space_pt:
            p.space_after = Pt(para_space_pt)
        run = p.add_run()
        run.text = text
        _set_run_font(run, size_pt, color_hex, bold)
    return box


def _bullet_lines(items: list[str]) -> list[str]:
    return [f"•  {it}" for it in items]


def _build_pptx(title: str, slides: list[dict]) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
    from pptx.util import Inches

    t = PPT_THEME
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(t["slide_w_in"]), Inches(t["slide_h_in"])
    blank = prs.slide_layouts[6]
    m = t["margin_in"]
    content_w = t["slide_w_in"] - 2 * m
    body_top = 1.65
    body_h = t["slide_h_in"] - body_top - 0.5

    def add_title(slide, text):
        _add_textbox(slide, m, 0.45, content_w, 1.0, [text], t["title_size"], t["accent_color"], bold=True,
                     anchor=MSO_ANCHOR.MIDDLE)
        bar = slide.shapes.add_shape(1, Inches(m), Inches(1.45), Inches(1.2), Inches(0.06))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor.from_string(t["accent_color"])
        bar.line.fill.background()

    for s in slides:
        slide = prs.slides.add_slide(blank)
        layout = s["layout"]
        if layout == "title":
            _add_textbox(slide, m, 2.4, content_w, 1.5, [s["title"] or title], t["cover_title_size"],
                         t["accent_color"], bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.BOTTOM)
            if s["subtitle"]:
                _add_textbox(slide, m, 4.05, content_w, 1.0, [s["subtitle"]], t["subtitle_size"],
                             t["muted_color"], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.TOP)
        elif layout == "section":
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(t["accent_color"])
            _add_textbox(slide, m, 2.75, content_w, 2.0, [s["title"]], t["cover_title_size"],
                         t["inverse_color"], bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
        elif layout == "bullets":
            add_title(slide, s["title"])
            _add_textbox(slide, m, body_top, content_w, body_h, _bullet_lines(s["bullets"]),
                         t["body_size"], t["text_color"], para_space_pt=t["bullet_space_pt"])
        elif layout == "two_column":
            add_title(slide, s["title"])
            gap = 0.4
            col_w = (content_w - gap) / 2
            _add_textbox(slide, m, body_top, col_w, body_h, _bullet_lines(s["bullets"]),
                         t["body_size"], t["text_color"], para_space_pt=t["bullet_space_pt"])
            _add_textbox(slide, m + col_w + gap, body_top, col_w, body_h, _bullet_lines(s["right_bullets"]),
                         t["body_size"], t["text_color"], para_space_pt=t["bullet_space_pt"])
        else:  # table
            if s["title"]:
                add_title(slide, s["title"])
            rows = s["table_rows"]
            n_rows, n_cols = len(rows), len(rows[0])
            row_h = 0.45
            top = body_top if s["title"] else m
            shape = slide.shapes.add_table(n_rows, n_cols, Inches(m), Inches(top),
                                           Inches(content_w), Inches(row_h * n_rows))
            table = shape.table
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    cell = table.cell(r, c)
                    cell.text = ""
                    run = cell.text_frame.paragraphs[0].add_run()
                    run.text = value
                    header = r == 0
                    _set_run_font(run, t["table_size"], t["inverse_color"] if header else t["text_color"], bold=header)
                    cell.fill.solid()
                    if header:
                        cell.fill.fore_color.rgb = RGBColor.from_string(t["table_header_fill"])
                    else:
                        cell.fill.fore_color.rgb = RGBColor.from_string(
                            t["table_stripe_fill"] if r % 2 == 0 else "FFFFFF"
                        )
        if s["notes"]:
            slide.notes_slide.notes_text_frame.text = s["notes"]

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def render_presentation(args: dict) -> RenderResult:
    warnings = _Warnings()
    slides = _validate_presentation(args, warnings)
    title = args.get("title") or ""
    content = _build_pptx(title, slides)
    return RenderResult(content, "pptx", len(slides), warnings, {"slides": len(slides)})
