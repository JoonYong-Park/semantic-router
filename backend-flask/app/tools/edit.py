"""기존 파일 부분 수정 툴 (inspect_document / edit_document)."""

import asyncio
import json
import logging
import re

from app.tools.base import MAX_GENERATED_FILES_PER_TURN, ToolContext, ToolResult, ToolSpec, _obj

logger = logging.getLogger("uvicorn.error")


# --- 기존 파일 부분 수정 툴 (트랙 3, 구현은 editor.py) ---

EDIT_OPS = ["replace_text", "set_cell_value"]

INSPECT_DOCUMENT_PARAMS = _obj({
    "attachment_id": {"type": "string", "description": "확인할 파일 id - 대화 기록의 \"(id=...)\" 값"},
    "scope": {"type": "string", "description": "범위: all, sheet:시트이름(엑셀), slide:번호(PPT), section:제목 일부(워드)"},
})

EDIT_DOCUMENT_PARAMS = _obj({
    "attachment_id": {"type": "string", "description": "수정할 파일 id (inspect_document에 쓴 것과 같은 id)"},
    "doc_revision": {"type": "string", "description": "inspect_document 결과 맨 앞의 doc_revision 값"},
    "operations": {
        "type": "array",
        "description": "수정 목록(최대 50개)",
        "items": _obj({
            "op": {"type": "string", "enum": EDIT_OPS,
                   "description": "엑셀 셀은 set_cell_value, 워드/PPT 문단·표 셀·노트는 replace_text"},
            "target_id": {"type": "string", "description": "inspect_document가 준 요소 ID (예: e12)"},
            "expected_old_value": {"type": "string", "description": "그 요소의 현재 값(inspect 결과 그대로) - 다르면 거부됨"},
            "new_value": {"type": "string", "description": "바꿀 새 값(요소 전체 텍스트). 날짜 셀은 YYYY-MM-DD"},
        }),
    },
})

INSPECT_DOCUMENT_DESCRIPTION = (
    "사용자가 첨부했거나 앞에서 만든 엑셀(xlsx)/워드(docx)/파워포인트(pptx)/한글(hwpx) 파일의 일부를 고쳐 달라고 할 때, "
    "먼저 이 툴로 현재 내용을 확인한다. 결과는 요소 ID(e1, e2…), 위치(시트/셀, 문단, 슬라이드/도형, 표 행·열), "
    "현재 값, 서식, 수정 가능 여부 목록이며 맨 앞에 doc_revision이 있다. "
    "attachment_id는 대화 기록의 \"[첨부 파일: 이름 (id=...)]\" 또는 \"[생성된 파일: 이름 (id=...)]\"에 있는 id를 쓰고, "
    "결과가 길면 scope로 시트/슬라이드/섹션을 좁혀 다시 호출한다. 단순히 파일 내용을 설명해 달라는 요청에는 쓰지 않는다."
)
EDIT_DOCUMENT_DESCRIPTION = (
    "inspect_document로 확인한 요소를 수정해 새 파일을 만든다(원본은 그대로 두고 '원본이름_수정' 파일로 저장). "
    "반드시 직전 inspect_document 결과의 doc_revision, 요소 ID(target_id), 현재 값(expected_old_value)을 그대로 넣고, "
    "new_value에는 그 요소의 바뀐 전체 텍스트를 넣는다(문단 일부만 바꿀 때도 문단 전체를 씀). "
    "엑셀 셀은 set_cell_value, 워드/PPT는 replace_text만 쓸 수 있고 행/문단/슬라이드 추가·삭제는 할 수 없다. "
    "하나라도 검증에 실패하면 아무것도 바뀌지 않고 이유가 돌아오니, 필요하면 inspect_document를 다시 호출해 고친다. "
    "수정본은 doc_revision과 id가 새로 생기므로, 같은 파일을 이어서 또 고치려면 결과의 새 file_id로 inspect_document부터 다시 호출한다."
)


async def _resolve_file(ctx: ToolContext, attachment_id: str):
    """이 채팅의 첨부(업로드/생성) 또는 이 채팅이 속한 프로젝트의 참고자료만 허용한다.
    반환: (원본 바이트, 표시 이름, 확장자, 첨부 id 또는 None, 출처 설명)."""
    from uuid import UUID as _UUID

    from app import storage
    from app.db.repo import conversations as conv_repo
    from app.db.repo import projects as project_repo
    from app.documents import editor

    try:
        file_id = str(_UUID(str(attachment_id).strip()))
    except ValueError:
        raise editor.EditError(f"attachment_id {attachment_id!r}가 올바른 id 형식이 아닙니다. 대화 기록의 (id=...) 값을 그대로 쓰세요")
    row = conv_repo.get_attachment_in_conv(ctx.conversation_id, file_id)
    source = "attachment"
    if row is None:
        row = project_repo.get_project_file(ctx.user_id, file_id)
        source = "project_file"
        if row is None or ctx.project_id is None or row["project_id"] != str(ctx.project_id):
            raise editor.EditError(f"id {attachment_id}인 파일을 이 대화(또는 이 프로젝트 참고자료)에서 찾을 수 없습니다")
    name = row["original_name"]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "hwp":
        raise editor.EditError("hwp(구형 한글) 파일은 수정할 수 없습니다. 한글에서 hwpx로 저장(변환)한 뒤 다시 올려 주세요")
    if ext not in editor.supported_formats():
        raise editor.EditError(f"{name}은(는) 수정할 수 없는 형식입니다. 수정 가능 형식: {', '.join(editor.supported_formats())}")
    path = storage.path(row["storage_key"])
    if path.suffix.lower().lstrip(".") != ext:
        # 텍스트가 거의 없는 문서는 업로드 시 PDF로 변환돼 storage_key가 바뀌지만,
        # 원본은 같은 폴더에 "{id}.{원래 확장자}"로 남아 있다(services/files.delete_files 참고).
        original = path.with_name(f"{row['id']}.{ext}")
        if not original.exists():
            raise editor.EditError(f"{name}의 원본 파일을 찾을 수 없어 수정할 수 없습니다(스캔본으로 PDF 변환됨)")
        path = original
    data = await asyncio.to_thread(path.read_bytes)
    return data, name, ext, (row["id"] if source == "attachment" else None), source


async def _execute_inspect_document(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.documents import editor

    try:
        data, name, ext, _, _ = await _resolve_file(ctx, arguments.get("attachment_id", ""))
        insp = await asyncio.to_thread(editor.inspect_bytes, data, ext)
        text = editor.render_inspection(insp, arguments.get("scope", "all"), name)
    except editor.EditError as exc:
        return ToolResult(content=f"inspect 실패: {exc}", is_error=True)
    return ToolResult(content=text)


async def _unique_edit_name(ctx: ToolContext, original_name: str) -> str:
    from app.db.repo import conversations as conv_repo

    base, _, ext = original_name.rpartition(".")
    base = base or original_name
    # 수정본을 또 수정하면 "_수정_수정"이 되지 않게 기존 접미사는 뗀다
    base = re.sub(r"_수정\d*$", "", base)
    existing = {a["original_name"] for a in conv_repo.list_attachments(ctx.conversation_id)}
    candidate, n = f"{base}_수정.{ext}", 2
    while candidate in existing:
        candidate = f"{base}_수정{n}.{ext}"
        n += 1
    return candidate


async def _execute_edit_document(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.services import generated as attachment_service
    from app.documents import editor
    from app.serializers import attachment_out

    generated = ctx.turn_state.setdefault("generated_attachment_ids", [])
    if len(generated) >= MAX_GENERATED_FILES_PER_TURN:
        return ToolResult(content=f"한 번의 답변에서 파일은 최대 {MAX_GENERATED_FILES_PER_TURN}개까지 만들 수 있습니다.", is_error=True)
    try:
        data, name, ext, source_id, source = await _resolve_file(ctx, arguments.get("attachment_id", ""))
        ctx.emit({"type": "processing", "message": "파일 수정 중..."})
        new_bytes, summary, warnings, changed = await asyncio.to_thread(
            editor.apply_edits, data, ext, arguments.get("doc_revision", ""), arguments.get("operations") or []
        )
    except editor.EditError as exc:
        return ToolResult(content=f"edit 실패: {exc}", is_error=True)

    display_name = await _unique_edit_name(ctx, name)
    meta = {
        "kind": "edit",
        "tool": "edit_document",
        "format": ext,
        "base_revision": editor.revision_of(data),
        "source": source,
        "source_id": str(arguments.get("attachment_id", "")),
        "ops": [{"target_id": s["target_id"], "location": s["location"]} for s in summary],
        "changed_parts": changed,
        "warnings": warnings,
    }
    try:
        attachment = await attachment_service.save_generated_file(
            ctx.user_id, ctx.conversation_id, display_name, ext, new_bytes, meta, source_attachment_id=source_id
        )
    except ValueError as exc:
        return ToolResult(content=str(exc), is_error=True)
    generated.append(attachment["id"])
    ctx.emit({"type": "file", "attachment": attachment_out(attachment)})
    payload = {
        "ok": True,
        "file_id": str(attachment["id"]),
        "filename": display_name,
        "new_doc_revision": editor.revision_of(new_bytes),
        "applied": summary,
        "warnings": warnings,
        "note": ("수정본은 이미 사용자에게 다운로드 카드로 표시됨(원본은 그대로). 무엇을 바꿨는지 간단히 안내할 것. "
                 "이 수정본을 또 고치려면 새 file_id로 inspect_document부터 다시 호출할 것."),
    }
    return ToolResult(content=json.dumps(payload, ensure_ascii=False),
                      metadata={"generated_attachment_id": attachment["id"]})


INSPECT_DOCUMENT = ToolSpec(
    name="inspect_document",
    description=INSPECT_DOCUMENT_DESCRIPTION,
    parameters=INSPECT_DOCUMENT_PARAMS,
    executor=_execute_inspect_document,
    strict=True,
)
EDIT_DOCUMENT = ToolSpec(
    name="edit_document",
    description=EDIT_DOCUMENT_DESCRIPTION,
    parameters=EDIT_DOCUMENT_PARAMS,
    executor=_execute_edit_document,
    strict=True,
)
