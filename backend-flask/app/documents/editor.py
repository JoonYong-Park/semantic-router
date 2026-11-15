"""inspect_document / edit_document 구현 - 기존 파일의 같은 포맷 부분 수정(트랙 3).

python-docx/openpyxl/python-pptx로 열었다가 다시 저장하면 손대지 않은 파트까지
재직렬화되고(실측: openpyxl 재저장 시 drawing1.xml까지 바뀜) 라이브러리가 모르는
요소는 잃을 수 있어서, zip + lxml로 "수정한 XML 파트만" 다시 쓴다. 나머지 파트는
원본 바이트를 그대로 복사한다.

- 요소 ID("e12")는 같은 파일(=같은 doc_revision)이면 항상 같다: scope와 무관하게
  파일 전체를 정해진 순서로 순회하며 번호를 매기고, scope는 출력만 거른다.
  edit는 같은 바이트를 다시 inspect해서 ID → XML 노드를 찾는다.
- 텍스트 교체는 대상의 첫 텍스트 런에 새 텍스트를 넣고 나머지 런의 텍스트만
  비운다(런/서식 요소는 지우지 않음).
- 검증은 전부 먼저 하고, 하나라도 실패하면 아무것도 적용하지 않는다.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from lxml import etree

MAX_OPERATIONS = 50
MAX_INSPECT_CHARS = 8000
MAX_ELEMENTS_PER_GROUP = 400
VALUE_PREVIEW_CHARS = 200

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
}
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def supported_formats() -> tuple[str, ...]:
    return ("xlsx", "docx", "pptx", "hwpx")


class EditError(ValueError):
    """모델이 inspect를 다시 하거나 인자를 고쳐 재시도해야 하는 실패."""


@dataclass
class Element:
    eid: str
    location: str
    value: str
    fmt: str
    editable: bool
    reason: str
    kind: str  # "cell" (xlsx) | "text"
    part: str
    node: object
    group: str  # scope 매칭용 - 시트명 / 슬라이드 번호 / 섹션 제목


@dataclass
class Inspection:
    fmt: str
    revision: str
    elements: list[Element]
    notes: list[str]
    trees: dict[str, etree._Element]
    infos: list[zipfile.ZipInfo]
    raw: dict[str, bytes]
    extra: dict = field(default_factory=dict)


def revision_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _rels(raw: dict[str, bytes], part: str) -> dict[str, tuple[str, str]]:
    """part의 관계 파일을 읽어 {rId: (type 끝부분, 절대 파트 경로)}."""
    d, base = posixpath.split(part)
    rel_path = posixpath.join(d, "_rels", base + ".rels")
    if rel_path not in raw:
        return {}
    root = etree.fromstring(raw[rel_path])
    out = {}
    for rel in root.findall("pr:Relationship", NS):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(d, target))
        out[rel.get("Id")] = (rel.get("Type", "").rsplit("/", 1)[-1], path)
    return out


def _open(data: bytes) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise EditError("파일이 올바른 Office/HWPX(zip) 형식이 아닙니다") from exc
    infos = zf.infolist()
    return infos, {i.filename: zf.read(i.filename) for i in infos}


def _tree(insp_trees: dict, raw: dict[str, bytes], part: str) -> etree._Element:
    if part not in insp_trees:
        insp_trees[part] = etree.fromstring(raw[part])
    return insp_trees[part]


def _closest(node, tag: str):
    parent = node.getparent()
    while parent is not None:
        if parent.tag == tag:
            return parent
        parent = parent.getparent()
    return None


def _own_text_nodes(container, text_tag: str, para_tag: str, stop_tags: tuple[str, ...] = ()) -> list:
    """container 안의 텍스트 노드 중, 가장 가까운 문단 조상이 container 자신(또는
    container 바로 아래 문단)인 것만 - 텍스트 상자/중첩 표 안의 텍스트는 제외."""
    out = []
    for t in container.iter(text_tag):
        anc = t.getparent()
        blocked = False
        while anc is not None and anc is not container:
            if anc.tag in stop_tags:
                blocked = True
                break
            anc = anc.getparent()
        if blocked:
            continue
        if container.tag == para_tag:
            if _closest(t, para_tag) is container:
                out.append(t)
        else:
            out.append(t)
    return out


def _text_of(nodes: list) -> str:
    return "".join("".join(t.itertext()) for t in nodes)


# =====================================================================
# xlsx
# =====================================================================

_BUILTIN_NUMFMT = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%",
    11: "0.00E+00", 14: "yyyy-mm-dd", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
    18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "yyyy-mm-dd h:mm", 49: "@",
}
_EXCEL_EPOCH = date(1899, 12, 30)


def _is_date_format(code: str) -> bool:
    cleaned = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code.lower())
    return bool(re.search(r"[ymd]", cleaned)) and "general" not in cleaned


def _col_letters(ref: str) -> str:
    return re.match(r"[A-Z]+", ref).group(0)


def _row_number(ref: str) -> int:
    return int(re.search(r"\d+", ref).group(0))


def _inspect_xlsx(data: bytes) -> Inspection:
    infos, raw = _open(data)
    trees: dict = {}
    if "xl/workbook.xml" not in raw:
        raise EditError("xl/workbook.xml이 없어 엑셀 파일로 읽을 수 없습니다")
    wb = _tree(trees, raw, "xl/workbook.xml")
    rels = _rels(raw, "xl/workbook.xml")

    shared: list[str] = []
    if "xl/sharedStrings.xml" in raw:
        for si in etree.fromstring(raw["xl/sharedStrings.xml"]).findall("s:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['s']}}}t")
                                  if _closest(t, f"{{{NS['s']}}}rPh") is None))

    numfmt_of_style: list[str] = []
    if "xl/styles.xml" in raw:
        st = etree.fromstring(raw["xl/styles.xml"])
        custom = {int(n.get("numFmtId")): n.get("formatCode", "") for n in st.findall("s:numFmts/s:numFmt", NS)}
        for xf in st.findall("s:cellXfs/s:xf", NS):
            fid = int(xf.get("numFmtId", "0"))
            numfmt_of_style.append(custom.get(fid, _BUILTIN_NUMFMT.get(fid, f"서식{fid}")))

    elements: list[Element] = []
    notes: list[str] = []
    has_formula = False
    counter = 0
    for sheet in wb.findall("s:sheets/s:sheet", NS):
        name = sheet.get("name")
        rid = sheet.get(f"{{{NS['r']}}}id")
        if rid not in rels:
            continue
        part = rels[rid][1]
        root = _tree(trees, raw, part)
        hyperlinked = {h.get("ref") for h in root.findall("s:hyperlinks/s:hyperlink", NS)}
        merges = [m.get("ref") for m in root.findall("s:mergeCells/s:mergeCell", NS)]
        if merges:
            notes.append(f"시트 '{name}' 병합 셀: {', '.join(merges[:30])}" + (" …" if len(merges) > 30 else ""))

        cells = []
        for c in root.iter(f"{{{NS['s']}}}c"):
            ref = c.get("r")
            if not ref:
                continue
            t = c.get("t", "n")
            f = c.find("s:f", NS)
            v = c.find("s:v", NS)
            style = int(c.get("s", "0"))
            fmt_code = numfmt_of_style[style] if style < len(numfmt_of_style) else "General"
            if t == "s" and v is not None:
                value = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
            elif t == "inlineStr":
                value = "".join(c.find("s:is", NS).itertext()) if c.find("s:is", NS) is not None else ""
            elif v is not None and v.text is not None:
                value = v.text
                if t == "n" and _is_date_format(fmt_code):
                    try:
                        value = (_EXCEL_EPOCH + timedelta(days=float(v.text))).isoformat()[:10]
                    except (ValueError, OverflowError):
                        pass
                elif t == "b":
                    value = "TRUE" if v.text == "1" else "FALSE"
            else:
                value = ""
            if f is not None:
                has_formula = True
            if not value and f is None:
                continue
            cells.append((c, ref, t, f, value, fmt_code))

        # 위치 설명용 - 1행(열 제목)과 A열(행 이름) 값
        header = {_col_letters(ref): val for _, ref, _, _, val, _ in cells if _row_number(ref) == 1}
        rowname = {_row_number(ref): val for _, ref, _, _, val, _ in cells if _col_letters(ref) == "A"}
        for c, ref, t, f, value, fmt_code in cells:
            counter += 1
            ctx = []
            col, row = _col_letters(ref), _row_number(ref)
            if row != 1 and header.get(col):
                ctx.append(f"열 '{header[col][:20]}'")
            if col != "A" and row != 1 and rowname.get(row):
                ctx.append(f"행 '{rowname[row][:20]}'")
            location = f"시트 '{name}' {ref}" + (f" ({', '.join(ctx)})" if ctx else "")
            editable, reason = True, ""
            if f is not None:
                editable, reason = False, "수식 셀"
                value = f"={f.text or ''} (계산값 {value})"
            elif t in ("b", "e"):
                editable, reason = False, "불리언/오류 셀"
            elif ref in hyperlinked:
                editable, reason = False, "하이퍼링크 셀"
            if t in ("s", "inlineStr", "str"):
                fmt = "텍스트"
            elif _is_date_format(fmt_code):
                fmt = f"날짜({fmt_code})"
            else:
                fmt = "숫자" + (f"({fmt_code})" if fmt_code != "General" else "")
            elements.append(Element(f"e{counter}", location, value, fmt, editable, reason,
                                    "cell", part, c, f"sheet:{name}"))
    insp = Inspection("xlsx", revision_of(data), elements, notes, trees, infos, raw)
    insp.extra["has_formula"] = has_formula
    return insp


def _parse_number(text: str) -> Decimal | None:
    cleaned = re.sub(r"[,\s원₩$]", "", text or "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _xlsx_values_equal(el: Element, expected: str) -> bool:
    if _norm(el.value) == _norm(expected):
        return True
    if el.fmt.startswith("숫자"):
        a, b = _parse_number(el.value), _parse_number(expected.rstrip("%"))
        if a is not None and b is not None:
            if expected.strip().endswith("%"):
                b = b / 100
            return a == b
    return False


def _set_xlsx_cell(el: Element, new_value: str) -> None:
    c = el.node
    s_ns = NS["s"]
    for child in c.findall("s:is", NS) + c.findall("s:v", NS):
        c.remove(child)
    if el.fmt.startswith("숫자") or el.fmt.startswith("날짜"):
        if el.fmt.startswith("날짜"):
            parsed = datetime.strptime(new_value.strip(), "%Y-%m-%d").date()
            number = str((parsed - _EXCEL_EPOCH).days)
        else:
            raw_value = new_value.strip()
            num = _parse_number(raw_value.rstrip("%"))
            if raw_value.endswith("%"):
                num = num / 100
            number = format(num.normalize(), "f") if num != num.to_integral_value() else str(int(num))
        if "t" in c.attrib:
            del c.attrib["t"]
        v = etree.SubElement(c, f"{{{s_ns}}}v")
        v.text = number
    else:
        # 공유 문자열(sharedStrings)은 다른 셀도 같이 쓰므로 건드리지 않고 이 셀만
        # inlineStr로 바꾼다 - sheet XML 하나만 바뀌고, 수식으로 해석될 일도 없다.
        c.set("t", "inlineStr")
        is_el = etree.SubElement(c, f"{{{s_ns}}}is")
        t = etree.SubElement(is_el, f"{{{s_ns}}}t")
        t.set(XML_SPACE, "preserve")
        t.text = new_value


# =====================================================================
# docx
# =====================================================================

def _w(tag: str) -> str:
    return f"{{{NS['w']}}}{tag}"


def _inspect_docx(data: bytes) -> Inspection:
    infos, raw = _open(data)
    trees: dict = {}
    part = "word/document.xml"
    if part not in raw:
        raise EditError("word/document.xml이 없어 워드 파일로 읽을 수 없습니다")
    root = _tree(trees, raw, part)
    body = root.find("w:body", NS)

    style_names = {}
    if "word/styles.xml" in raw:
        for st in etree.fromstring(raw["word/styles.xml"]).findall("w:style", NS):
            name = st.find("w:name", NS)
            style_names[st.get(_w("styleId"))] = name.get(_w("val")) if name is not None else st.get(_w("styleId"))

    notes = []
    if root.find(".//w:ins", NS) is not None or root.find(".//w:del", NS) is not None:
        notes.append("⚠ 변경 추적(삽입/삭제 표시)이 남아 있는 문서입니다. 변경 추적이 걸린 문단은 수정할 수 없습니다.")
    if "word/comments.xml" in raw or root.find(".//w:commentRangeStart", NS) is not None:
        notes.append("⚠ 메모(댓글)가 있는 문서입니다. 메모는 그대로 두고 본문 텍스트만 바뀝니다.")
    for rid, (rtype, rpart) in _rels(raw, part).items():
        if rtype in ("header", "footer") and rpart in raw:
            text = _norm("".join(etree.fromstring(raw[rpart]).itertext()))
            if text:
                notes.append(f"{'머리글' if rtype == 'header' else '바닥글'}({rpart.split('/')[-1]}): '{text[:80]}' - 이번 범위에서 수정 불가")

    elements: list[Element] = []
    counter = 0
    section = ""
    para_no = 0
    table_no = 0

    def para_info(p):
        nodes = _own_text_nodes(p, _w("t"), _w("p"), stop_tags=(_w("txbxContent"),))
        text = _text_of(nodes)
        ppr = p.find("w:pPr", NS)
        style_id = ppr.find("w:pStyle", NS).get(_w("val")) if ppr is not None and ppr.find("w:pStyle", NS) is not None else "Normal"
        style = style_names.get(style_id, style_id)
        tags = []
        if ppr is not None and ppr.find("w:numPr", NS) is not None:
            tags.append("목록")
        if p.find(".//w:drawing", NS) is not None or p.find(".//w:pict", NS) is not None:
            tags.append("이미지 포함")
        reason = ""
        if p.find(".//w:hyperlink", NS) is not None:
            reason = "하이퍼링크 포함"
        elif p.find(".//w:fldSimple", NS) is not None or p.find(".//w:instrText", NS) is not None:
            reason = "필드 포함"
        elif p.find(".//w:ins", NS) is not None or p.find(".//w:del", NS) is not None:
            reason = "변경 추적 포함"
        return nodes, text, style, tags, reason

    for child in body:
        if child.tag == _w("p"):
            para_no += 1
            nodes, text, style, tags, reason = para_info(child)
            if style.lower().startswith(("heading", "title", "제목")) and text.strip():
                section = text.strip()
            for tb in child.iter(_w("txbxContent")):
                tb_text = _norm("".join(tb.itertext()))
                if tb_text:
                    counter += 1
                    elements.append(Element(f"e{counter}", f"문단 {para_no} 안 텍스트 상자", tb_text, "텍스트 상자",
                                            False, "텍스트 상자(이번 범위에서 수정 불가)", "text", part, None, section))
            if not text.strip():
                continue
            counter += 1
            elements.append(Element(f"e{counter}", f"문단 {para_no}", text, " · ".join([f"스타일 '{style}'"] + tags),
                                    not reason, reason, "text", part, nodes, section))
        elif child.tag == _w("tbl"):
            table_no += 1
            rows = child.findall("w:tr", NS)
            header_cells = []
            for r_i, tr in enumerate(rows, start=1):
                for c_i, tc in enumerate(tr.findall("w:tc", NS), start=1):
                    paras = tc.findall("w:p", NS)
                    nodes = [t for p in paras for t in _own_text_nodes(p, _w("t"), _w("p"), (_w("txbxContent"),))]
                    text = "\n".join(_text_of(_own_text_nodes(p, _w("t"), _w("p"), (_w("txbxContent"),))) for p in paras).strip()
                    if r_i == 1:
                        header_cells.append(text)
                    if not text:
                        continue
                    reason = ""
                    if tc.find(".//w:tbl", NS) is not None:
                        reason = "중첩 표"
                    elif tc.find(".//w:hyperlink", NS) is not None:
                        reason = "하이퍼링크 포함"
                    elif tc.find(".//w:fldSimple", NS) is not None or tc.find(".//w:instrText", NS) is not None:
                        reason = "필드 포함"
                    elif tc.find(".//w:ins", NS) is not None or tc.find(".//w:del", NS) is not None:
                        reason = "변경 추적 포함"
                    hdr = header_cells[c_i - 1] if r_i > 1 and c_i - 1 < len(header_cells) and header_cells[c_i - 1] else ""
                    loc = f"표{table_no} 행{r_i} 열{c_i}" + (f" (열 '{hdr[:20]}')" if hdr else "")
                    counter += 1
                    elements.append(Element(f"e{counter}", loc, text, "표 셀", not reason, reason,
                                            "text", part, nodes, section))
        elif child.tag == _w("sdt"):
            text = _norm("".join(child.itertext()))
            if text:
                counter += 1
                elements.append(Element(f"e{counter}", "콘텐츠 컨트롤", text, "콘텐츠 컨트롤", False,
                                        "콘텐츠 컨트롤(이번 범위에서 수정 불가)", "text", part, None, section))
    return Inspection("docx", revision_of(data), elements, notes, trees, infos, raw)


# =====================================================================
# pptx
# =====================================================================

def _a(tag: str) -> str:
    return f"{{{NS['a']}}}{tag}"


def _p(tag: str) -> str:
    return f"{{{NS['p']}}}{tag}"


_PH_KIND = {"title": "제목", "ctrTitle": "제목", "subTitle": "부제목", "body": "본문", "obj": "본문"}


def _pptx_paragraph_reason(para) -> str:
    if para.find("a:fld", NS) is not None:
        return "필드(슬라이드 번호/날짜 등) 포함"
    if para.find(".//a:hlinkClick", NS) is not None:
        return "하이퍼링크 포함"
    return ""


def _pptx_shape_elements(shape_root, slide_no: int, label_prefix: str, part: str, counter_ref: list,
                         elements: list, group: str) -> None:
    for node in shape_root:
        tag = node.tag
        if tag == _p("sp"):
            nv = node.find("p:nvSpPr", NS)
            name = nv.find("p:cNvPr", NS).get("name", "도형") if nv is not None else "도형"
            ph = nv.find("p:nvPr/p:ph", NS) if nv is not None else None
            kind = _PH_KIND.get(ph.get("type", "body"), "본문") if ph is not None else (
                "텍스트 상자" if nv is not None and nv.find("p:cNvSpPr", NS) is not None
                and nv.find("p:cNvSpPr", NS).get("txBox") == "1" else "도형")
            tx = node.find("p:txBody", NS)
            if tx is None:
                continue
            for k, para in enumerate(tx.findall("a:p", NS), start=1):
                nodes = [r.find("a:t", NS) for r in para.findall("a:r", NS) if r.find("a:t", NS) is not None]
                text = _text_of(nodes) + "".join(f.findtext("a:t", "", NS) for f in para.findall("a:fld", NS))
                if not text.strip():
                    continue
                reason = _pptx_paragraph_reason(para)
                counter_ref[0] += 1
                elements.append(Element(f"e{counter_ref[0]}", f"{label_prefix} '{name}'({kind}) 문단 {k}", text,
                                        kind, not reason, reason, "text", part, nodes, group))
        elif tag == _p("graphicFrame"):
            name = node.find("p:nvGraphicFramePr/p:cNvPr", NS)
            name = name.get("name", "개체") if name is not None else "개체"
            tbl = node.find(".//a:tbl", NS)
            if tbl is not None:
                for r_i, tr in enumerate(tbl.findall("a:tr", NS), start=1):
                    for c_i, tc in enumerate(tr.findall("a:tc", NS), start=1):
                        paras = tc.findall("a:txBody/a:p", NS)
                        nodes = [r.find("a:t", NS) for para in paras for r in para.findall("a:r", NS)
                                 if r.find("a:t", NS) is not None]
                        text = "\n".join(_text_of([r.find("a:t", NS) for r in para.findall("a:r", NS)
                                                   if r.find("a:t", NS) is not None]) for para in paras).strip()
                        if not text:
                            continue
                        reason = next((_pptx_paragraph_reason(para) for para in paras if _pptx_paragraph_reason(para)), "")
                        counter_ref[0] += 1
                        elements.append(Element(f"e{counter_ref[0]}", f"{label_prefix} 표 '{name}' 행{r_i} 열{c_i}",
                                                text, "표 셀", not reason, reason, "text", part, nodes, group))
            else:
                uri = node.find(".//a:graphicData", NS)
                uri = uri.get("uri", "") if uri is not None else ""
                kind = "차트" if "chart" in uri else ("SmartArt" if "diagram" in uri else "개체")
                text = _norm("".join(node.itertext()))
                counter_ref[0] += 1
                elements.append(Element(f"e{counter_ref[0]}", f"{label_prefix} {kind} '{name}'", text or "(텍스트 없음)",
                                        kind, False, f"{kind} 내부(수정 불가)", "text", part, None, group))
        elif tag == _p("grpSp"):
            name = node.find("p:nvGrpSpPr/p:cNvPr", NS)
            name = name.get("name", "그룹") if name is not None else "그룹"
            text = _norm(" ".join(t.text or "" for t in node.iter(_a("t"))))
            if text:
                counter_ref[0] += 1
                elements.append(Element(f"e{counter_ref[0]}", f"{label_prefix} 그룹 '{name}'", text, "그룹",
                                        False, "그룹 도형 내부(수정 불가)", "text", part, None, group))


def _inspect_pptx(data: bytes) -> Inspection:
    infos, raw = _open(data)
    trees: dict = {}
    pres_part = "ppt/presentation.xml"
    if pres_part not in raw:
        raise EditError("ppt/presentation.xml이 없어 파워포인트 파일로 읽을 수 없습니다")
    pres = _tree(trees, raw, pres_part)
    rels = _rels(raw, pres_part)
    elements: list[Element] = []
    counter = [0]
    for n, sld in enumerate(pres.findall("p:sldIdLst/p:sldId", NS), start=1):
        rid = sld.get(f"{{{NS['r']}}}id")
        if rid not in rels:
            continue
        part = rels[rid][1]
        root = _tree(trees, raw, part)
        tree = root.find("p:cSld/p:spTree", NS)
        if tree is not None:
            _pptx_shape_elements(tree, n, f"슬라이드 {n}", part, counter, elements, f"slide:{n}")
        for rtype, npart in _rels(raw, part).values():
            if rtype == "notesSlide" and npart in raw:
                nroot = _tree(trees, raw, npart)
                for sp in nroot.iter(_p("sp")):
                    ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
                    if ph is None or ph.get("type") != "body":
                        continue
                    for k, para in enumerate(sp.findall("p:txBody/a:p", NS), start=1):
                        nodes = [r.find("a:t", NS) for r in para.findall("a:r", NS) if r.find("a:t", NS) is not None]
                        text = _text_of(nodes)
                        if not text.strip():
                            continue
                        reason = _pptx_paragraph_reason(para)
                        counter[0] += 1
                        elements.append(Element(f"e{counter[0]}", f"슬라이드 {n} 노트 문단 {k}", text, "노트",
                                                not reason, reason, "text", npart, nodes, f"slide:{n}"))
    return Inspection("pptx", revision_of(data), elements, [], trees, infos, raw)


# =====================================================================
# hwpx
# =====================================================================

def _hp(tag: str) -> str:
    return f"{{{NS['hp']}}}{tag}"


def _inspect_hwpx(data: bytes) -> Inspection:
    infos, raw = _open(data)
    trees: dict = {}
    sections = sorted(
        (n for n in raw if re.fullmatch(r"Contents/section\d+\.xml", n)),
        key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group(0)),
    )
    if not sections:
        raise EditError("Contents/section*.xml이 없어 HWPX 문서로 읽을 수 없습니다")
    elements: list[Element] = []
    counter = 0
    for s_i, part in enumerate(sections):
        root = _tree(trees, raw, part)
        para_no = 0
        table_no = 0
        for p in root.findall("hp:p", NS):
            para_no += 1
            nodes = _own_text_nodes(p, _hp("t"), _hp("p"))
            text = _text_of(nodes)
            if text.strip():
                counter += 1
                reason = "필드 포함" if p.find(".//hp:fieldBegin", NS) is not None else ""
                elements.append(Element(f"e{counter}", f"구역{s_i + 1} 문단 {para_no}", text, "문단",
                                        not reason, reason, "text", part, nodes, f"section{s_i + 1}"))
            for tbl in p.iter(_hp("tbl")):
                if _closest(tbl, _hp("tbl")) is not None:
                    continue  # 중첩 표는 바깥 표 셀에서 수정 불가로 처리
                table_no += 1
                for r_i, tr in enumerate(tbl.findall("hp:tr", NS), start=1):
                    for c_i, tc in enumerate(tr.findall("hp:tc", NS), start=1):
                        paras = tc.findall("hp:subList/hp:p", NS)
                        nodes = [t for cp in paras for t in _own_text_nodes(cp, _hp("t"), _hp("p"))]
                        text = "\n".join(_text_of(_own_text_nodes(cp, _hp("t"), _hp("p"))) for cp in paras).strip()
                        if not text:
                            continue
                        reason = "중첩 표" if tc.find(".//hp:tbl", NS) is not None else ""
                        counter += 1
                        elements.append(Element(f"e{counter}", f"구역{s_i + 1} 표{table_no} 행{r_i} 열{c_i}", text,
                                                "표 셀", not reason, reason, "text", part, nodes, f"section{s_i + 1}"))
    return Inspection("hwpx", revision_of(data), elements, [], trees, infos, raw)


# =====================================================================
# 공통: inspect 출력 / edit 적용
# =====================================================================

_INSPECTORS = {"xlsx": _inspect_xlsx, "docx": _inspect_docx, "pptx": _inspect_pptx, "hwpx": _inspect_hwpx}


def inspect_bytes(data: bytes, ext: str) -> Inspection:
    if ext not in supported_formats():
        raise EditError(f"{ext} 형식은 수정할 수 없습니다. 지원 형식: {', '.join(supported_formats())}")
    return _INSPECTORS[ext](data)


def _scope_filter(insp: Inspection, scope: str):
    scope = (scope or "all").strip()
    if scope in ("", "all"):
        return lambda el: True
    kind, _, value = scope.partition(":")
    value = value.strip()
    groups = sorted({el.group for el in insp.elements})
    if insp.fmt == "xlsx" and kind == "sheet" and value:
        if f"sheet:{value}" not in groups:
            raise EditError(f"scope '{scope}'의 시트를 찾을 수 없습니다. 사용 가능한 시트: "
                            + ", ".join(g[6:] for g in groups if g.startswith("sheet:")))
        return lambda el: el.group == f"sheet:{value}"
    if insp.fmt == "pptx" and kind == "slide" and value.isdigit():
        if f"slide:{value}" not in groups:
            raise EditError(f"scope '{scope}'의 슬라이드가 없습니다. 이 파일의 슬라이드 수: "
                            f"{len([g for g in groups if g.startswith('slide:')])}")
        return lambda el: el.group == f"slide:{value}"
    if insp.fmt in ("docx", "hwpx") and kind == "section" and value:
        matched = [g for g in groups if value.lower() in g.lower()]
        if not matched:
            heads = [g for g in groups if g][:20]
            raise EditError(f"scope '{scope}'와 맞는 제목(섹션)이 없습니다. 문서의 제목들: {', '.join(heads) or '(제목 없음)'}")
        return lambda el: value.lower() in (el.group or "").lower()
    allowed = {"xlsx": "all 또는 sheet:시트이름", "pptx": "all 또는 slide:번호",
               "docx": "all 또는 section:제목 일부", "hwpx": "all 또는 section:구역"}[insp.fmt]
    raise EditError(f"scope '{scope}'는 이 파일({insp.fmt})에 쓸 수 없습니다. 사용 가능: {allowed}")


def render_inspection(insp: Inspection, scope: str, filename: str) -> str:
    keep = _scope_filter(insp, scope)
    op = "set_cell_value" if insp.fmt == "xlsx" else "replace_text"
    lines = [
        f"doc_revision: {insp.revision}",
        f"파일: {filename} ({insp.fmt}) / scope: {scope or 'all'} / 수정 연산: {op}",
        "형식: ID | 위치 | 현재 값 | 서식 | 수정 가능 여부",
    ]
    lines += insp.notes
    per_group: dict[str, int] = {}
    body = []
    truncated_groups = set()
    for el in insp.elements:
        if not keep(el):
            continue
        per_group[el.group] = per_group.get(el.group, 0) + 1
        if per_group[el.group] > MAX_ELEMENTS_PER_GROUP:
            truncated_groups.add(el.group)
            continue
        value = el.value if len(el.value) <= VALUE_PREVIEW_CHARS else el.value[:VALUE_PREVIEW_CHARS] + "…(생략)"
        value = value.replace("\n", "⏎")
        status = "수정 가능" if el.editable else f"수정 불가({el.reason})"
        body.append(f"{el.eid} | {el.location} | {value} | {el.fmt} | {status}")
    for g in sorted(truncated_groups):
        lines.append(f"(안내) '{g}' 범위의 요소가 {per_group[g]}개라 앞 {MAX_ELEMENTS_PER_GROUP}개만 표시함 - scope를 좁혀 다시 확인하세요")
    if not body:
        lines.append("(이 범위에 텍스트가 있는 요소가 없습니다)")
    out = "\n".join(lines) + "\n" + "\n".join(body)
    if len(out) > MAX_INSPECT_CHARS:
        cut = out[:MAX_INSPECT_CHARS].rsplit("\n", 1)[0]
        hint = {"xlsx": "sheet:시트이름", "pptx": "slide:번호", "docx": "section:제목", "hwpx": "section:구역"}[insp.fmt]
        out = cut + f"\n…(결과가 {MAX_INSPECT_CHARS}자를 넘어 잘렸습니다. scope를 '{hint}'처럼 좁혀 다시 inspect_document를 호출하세요)"
    return out


def _replace_text_nodes(nodes: list, new_text: str) -> None:
    """첫 텍스트 노드에 새 텍스트를 넣고(서식 유지) 나머지 노드의 텍스트만 비운다."""
    first, rest = nodes[0], nodes[1:]
    for child in list(first):
        first.remove(child)  # hwpx의 <hp:tab/> 같은 인라인 자식 정리
    first.text = new_text
    first.set(XML_SPACE, "preserve")
    for t in rest:
        for child in list(t):
            t.remove(child)
        t.text = ""


def apply_edits(data: bytes, ext: str, doc_revision: str, operations: list[dict]) -> tuple[bytes, list[dict], list[str], list[str]]:
    """검증 → 적용 → 새 zip 바이트. 반환: (새 파일, 연산 요약, 경고, 바뀐 파트 목록)."""
    insp = inspect_bytes(data, ext)
    if (doc_revision or "").strip() != insp.revision:
        raise EditError(
            f"doc_revision이 현재 파일과 다릅니다(받은 값 {doc_revision!r}, 현재 {insp.revision}). "
            "inspect_document를 다시 호출해 최신 내용과 doc_revision을 확인한 뒤 다시 시도하세요"
        )
    if not isinstance(operations, list) or not operations:
        raise EditError("operations가 비어 있습니다")
    if len(operations) > MAX_OPERATIONS:
        raise EditError(f"operations가 {len(operations)}개, 한 번에 최대 {MAX_OPERATIONS}개입니다. 나눠서 호출하세요")

    allowed_op = "set_cell_value" if ext == "xlsx" else "replace_text"
    by_id = {el.eid: el for el in insp.elements}
    errors, seen, plan = [], set(), []
    for i, op in enumerate(operations):
        where = f"operations[{i}]"
        if not isinstance(op, dict):
            errors.append(f"{where}: 객체가 아닙니다")
            continue
        kind, tid = op.get("op"), op.get("target_id", "")
        expected, new_value = op.get("expected_old_value", ""), op.get("new_value", "")
        el = by_id.get(tid)
        if kind != allowed_op:
            errors.append(f"{where} ({tid}): {ext} 파일은 op='{allowed_op}'만 쓸 수 있습니다 (받은 값 {kind!r})")
        elif el is None:
            errors.append(f"{where}: target_id '{tid}'가 이 파일에 없습니다")
        elif tid in seen:
            errors.append(f"{where} ({tid}): 같은 요소를 한 번에 두 번 수정할 수 없습니다")
        elif not el.editable:
            errors.append(f"{where} ({tid}, {el.location}): 수정 불가 요소입니다 - {el.reason}")
        elif not isinstance(new_value, str):
            errors.append(f"{where} ({tid}): new_value는 문자열이어야 합니다")
        else:
            same = _xlsx_values_equal(el, expected) if ext == "xlsx" else _norm(el.value) == _norm(expected)
            if not same:
                errors.append(f"{where} ({tid}, {el.location}): expected_old_value가 현재 값과 다릅니다 "
                              f"(현재 값: {el.value[:120]!r})")
            elif ext == "xlsx" and el.fmt.startswith("날짜"):
                try:
                    datetime.strptime(new_value.strip(), "%Y-%m-%d")
                except ValueError:
                    errors.append(f"{where} ({tid}): 날짜 셀에는 YYYY-MM-DD 형식으로 넣어야 합니다 (받은 값 {new_value!r})")
            elif ext == "xlsx" and el.fmt.startswith("숫자") and _parse_number(new_value.strip().rstrip("%")) is None:
                errors.append(f"{where} ({tid}, {el.location}): 숫자 셀에 숫자로 바꿀 수 없는 값입니다 ({new_value!r}). "
                              "숫자만 넣거나(예: 1200, 1,200, 12.5%) 다른 셀을 고르세요")
        seen.add(tid)
        if el is not None:
            plan.append((el, new_value))
    if errors:
        raise EditError("아무것도 수정하지 않았습니다. 아래 문제를 고친 뒤 다시 호출하세요"
                        "(필요하면 inspect_document로 최신 값을 다시 확인):\n- " + "\n- ".join(errors))

    warnings, summary, changed = [], [], set()
    for el, new_value in plan:
        if ext == "xlsx":
            _set_xlsx_cell(el, new_value)
        else:
            _replace_text_nodes(el.node, new_value)
            if ext == "pptx" and len(_norm(new_value)) > 1.5 * max(len(_norm(el.value)), 1):
                warnings.append(f"{el.eid} ({el.location}): 새 텍스트가 원문보다 1.5배 넘게 길어 도형 밖으로 넘칠 수 있음")
        changed.add(el.part)
        summary.append({"target_id": el.eid, "location": el.location, "old": el.value[:80], "new": new_value[:80]})

    # 숫자를 바꾼 엑셀에 수식이 있으면, 열 때 다시 계산하도록 표시한다(캐시된 합계가
    # 옛 값으로 보이는 것 방지). workbook.xml의 calcPr만 바뀐다.
    if ext == "xlsx" and insp.extra.get("has_formula"):
        wb = insp.trees["xl/workbook.xml"]
        calc = wb.find("s:calcPr", NS)
        if calc is None:
            # 스키마 순서상 calcPr 뒤에 와야 하는 요소가 있으면 그 앞에 넣는다
            # (끝에 붙이면 extLst 뒤로 가서 엑셀이 파일 손상으로 볼 수 있음).
            calc = etree.Element(f"{{{NS['s']}}}calcPr")
            after = ("oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes",
                     "webPublishing", "fileRecoveryPr", "webPublishObjects", "extLst")
            anchor = next((ch for ch in wb if etree.QName(ch).localname in after), None)
            if anchor is not None:
                anchor.addprevious(calc)
            else:
                wb.append(calc)
        calc.set("fullCalcOnLoad", "1")
        changed.add("xl/workbook.xml")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for info in insp.infos:
            payload = insp.raw[info.filename]
            if info.filename in changed:
                payload = etree.tostring(insp.trees[info.filename], xml_declaration=True,
                                         encoding="UTF-8", standalone=True)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zout.writestr(zi, payload)
    return out.getvalue(), summary, warnings, sorted(changed)
