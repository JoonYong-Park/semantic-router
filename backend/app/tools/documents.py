"""문서 생성 툴 3종 (create_document / create_spreadsheet / create_presentation)."""

import asyncio
import json
import logging
from typing import Any, Callable

from app.tools.base import MAX_GENERATED_FILES_PER_TURN, ToolContext, ToolResult, ToolSpec, _obj, _STR_LIST, _ROWS

logger = logging.getLogger("uvicorn.error")


# --- 문서 생성 툴 3종 (렌더러는 generator.py) ---


CREATE_DOCUMENT_PARAMS = _obj({
    "format": {"type": "string", "enum": ["docx", "pdf", "md", "txt", "hwpx"],
               "description": "만들 파일 형식 (hwpx는 한글 문서)"},
    "filename": {"type": "string", "description": "확장자 없는 파일명(한글 가능). 비우면 title을 씀"},
    "title": {"type": "string", "description": "문서 맨 위 제목. 없으면 빈 문자열"},
    "blocks": {
        "type": "array",
        "description": "본문 블록을 위에서 아래 순서로 나열",
        "items": _obj({
            "type": {"type": "string", "enum": ["heading", "paragraph", "bullets", "numbered", "table"]},
            "level": {"type": "integer", "description": "heading이면 1~3, 그 외 0"},
            "text": {"type": "string", "description": "heading/paragraph의 본문, 그 외 빈 문자열"},
            "items": {**_STR_LIST, "description": "bullets/numbered의 항목들, 그 외 []"},
            "rows": {**_ROWS, "description": "table의 행들(첫 행이 헤더, 모든 값은 문자열), 그 외 []"},
        }),
    },
})

CREATE_SPREADSHEET_PARAMS = _obj({
    "format": {"type": "string", "enum": ["xlsx", "csv"], "description": "만들 파일 형식"},
    "filename": {"type": "string", "description": "확장자 없는 파일명(한글 가능)"},
    "sheets": {
        "type": "array",
        "description": "시트 목록 (csv는 첫 시트만 저장됨)",
        "items": _obj({
            "name": {"type": "string", "description": "시트 이름(31자 이내)"},
            "columns": {
                "type": "array",
                "items": _obj({
                    "header": {"type": "string", "description": "열 제목"},
                    "type": {"type": "string",
                             "enum": ["text", "integer", "decimal", "percent", "date", "currency_krw"],
                             "description": (
                                 "열 데이터 형식 - 서버가 이 형식으로 변환/서식 지정. percent 열 값은 "
                                 "12.5%를 \"12.5\" 또는 \"12.5%\"처럼 퍼센트 숫자로 쓰고 0.125 같은 소수 비율로 쓰지 말 것"
                             )},
                }),
            },
            "rows": {**_ROWS, "description": "데이터 행들(헤더 제외). 숫자/날짜도 전부 문자열로, 칸 수는 columns와 같게"},
            "total_row": {"type": "string", "enum": ["none", "sum"],
                          "description": "sum이면 숫자형 열 합계 행을 서버가 수식으로 추가"},
        }),
    },
})

CREATE_PRESENTATION_PARAMS = _obj({
    "filename": {"type": "string", "description": "확장자 없는 파일명(한글 가능). 비우면 title을 씀"},
    "title": {"type": "string", "description": "발표 자료 전체 제목"},
    "slides": {
        "type": "array",
        "description": "슬라이드를 순서대로 나열",
        "items": _obj({
            "layout": {"type": "string", "enum": ["title", "bullets", "two_column", "table", "section"]},
            "title": {"type": "string", "description": "슬라이드 제목(40자 이내 권장)"},
            "subtitle": {"type": "string", "description": "title 레이아웃의 부제목, 그 외 빈 문자열"},
            "bullets": {**_STR_LIST, "description": "bullets 레이아웃 항목 / two_column의 왼쪽 열, 그 외 []"},
            "right_bullets": {**_STR_LIST, "description": "two_column의 오른쪽 열, 그 외 []"},
            "table_rows": {**_ROWS, "description": "table 레이아웃의 행들(첫 행이 헤더), 그 외 []"},
            "notes": {"type": "string", "description": "발표자 노트, 없으면 빈 문자열"},
        }),
    },
})

CREATE_DOCUMENT_DESCRIPTION = (
    "사용자가 워드(docx), PDF, 마크다운(md), 텍스트(txt), 한글(hwpx) 파일로 받기를 원할 때 문서 파일을 만든다. "
    "채팅 안에서 글이나 표를 보여주기만 하면 되는 요청에는 호출하지 말고 그냥 답변으로 작성한다. "
    "blocks는 위에서 아래 순서의 본문이며, 각 블록은 type에 맞는 필드만 채우고 나머지는 빈 값(\"\", [], 0)으로 둔다: "
    "heading은 level(1~3)과 text, paragraph는 text, bullets/numbered는 items, table은 rows(첫 행이 헤더). "
    "한글을 그대로 써도 되고, 분량은 블록 500개·문단 5000자·표 200행 이내로 하되 보통은 A4 몇 장 분량이면 충분하다. "
    "첨부 파일을 다른 형식으로 바꿔 달라는 요청이면 첨부 내용을 바탕으로 이 툴을 호출한다."
)
CREATE_SPREADSHEET_DESCRIPTION = (
    "사용자가 엑셀(xlsx)이나 CSV 파일로 받기를 원할 때 표 데이터 파일을 만든다. "
    "채팅에 표를 보여주기만 하면 되는 요청에는 호출하지 말고 마크다운 표로 답한다. "
    "sheets마다 columns(열 제목과 형식)와 rows(데이터 행)를 주며, 모든 셀 값은 숫자·날짜도 문자열로 보내고 "
    "형식(integer/decimal/percent/date(YYYY-MM-DD)/currency_krw/text)에 맞는 변환과 서식은 서버가 한다 "
    "(percent는 12.5%를 \"12.5\"처럼 퍼센트 숫자로 쓰고 0.125처럼 쓰지 않는다). "
    "합계가 필요하면 셀 주소나 수식을 쓰지 말고 total_row를 sum으로 지정한다. "
    "한글 사용 가능, 시트 10개·시트당 2000행 이내이며 csv는 첫 시트만 저장된다."
)
CREATE_PRESENTATION_DESCRIPTION = (
    "사용자가 파워포인트(pptx) 발표 자료나 슬라이드를 원할 때 16:9 프레젠테이션 파일을 만든다. "
    "슬라이드 내용을 채팅에 글로 정리해 달라는 요청에는 호출하지 않는다. "
    "layout별로 쓰는 필드만 채우고 나머지는 빈 값으로 둔다: title(title, subtitle), bullets(title, bullets), "
    "two_column(title, bullets=왼쪽, right_bullets=오른쪽), table(title, table_rows), section(title); notes는 발표자 노트다. "
    "한글 사용 가능, 슬라이드는 30장 이내이고 제목 40자·불릿 6개(two_column은 열마다 5개)·불릿당 80자·표 8행×5열을 넘기면 "
    "슬라이드 밖으로 넘칠 수 있으니 내용을 나눠 여러 장으로 만든다."
)


async def _render_and_save(
    tool_name: str,
    render_fn: Callable[[dict], Any],
    arguments: dict,
    ctx: ToolContext,
    title_for_name: str,
    processing_label: str,
) -> ToolResult:
    """문서 툴 공통 흐름: 개수 상한 → processing 이벤트 → 렌더링(스레드) →
    저장 + Attachment 등록 → file 이벤트 → ToolResult. 검증 실패와 렌더 예외는
    모델이 고쳐서 다시 부를 수 있게 구체적인 is_error 결과로 돌려준다."""
    from app.attachments import service as attachment_service
    from app.documents import generator
    from app.schemas import AttachmentOut

    generated = ctx.turn_state.setdefault("generated_attachment_ids", [])
    if len(generated) >= MAX_GENERATED_FILES_PER_TURN:
        return ToolResult(
            content=f"한 번의 답변에서 파일은 최대 {MAX_GENERATED_FILES_PER_TURN}개까지 만들 수 있습니다. 더 만들지 말고 지금까지 만든 파일을 안내하세요.",
            is_error=True,
        )

    ctx.emit({"type": "processing", "message": processing_label})
    try:
        result = await asyncio.to_thread(render_fn, arguments)
    except generator.DocValidationError as exc:
        return ToolResult(content=f"인자 검증 실패: {exc}. 고쳐서 다시 호출하세요.", is_error=True)

    filename = generator.sanitize_filename(arguments.get("filename", ""), title_for_name)
    display_name = f"{filename}.{result.ext}"
    meta = {
        "tool": tool_name,
        "format": result.ext,
        "title": title_for_name,
        "pages_or_slides": result.pages_or_slides,
        "warnings": result.warnings,
        **result.meta,
    }
    try:
        attachment = await attachment_service.save_generated_file(
            ctx.conversation_id, display_name, result.ext, result.content, meta
        )
    except ValueError as exc:  # 용량 상한 초과
        return ToolResult(content=str(exc), is_error=True)

    generated.append(attachment.id)
    ctx.emit({
        "type": "file",
        "attachment": AttachmentOut.model_validate(attachment).model_dump(mode="json"),
    })
    payload = {
        "ok": True,
        "file_id": str(attachment.id),
        "filename": display_name,
        "format": result.ext,
        "size_bytes": attachment.size_bytes,
        "pages_or_slides": result.pages_or_slides,
        "warnings": result.warnings,
        "note": "파일은 이미 사용자에게 다운로드 카드로 표시됨. 내용을 채팅에 반복하지 말고 간단히 안내만 할 것.",
    }
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        metadata={"generated_attachment_id": attachment.id},
    )


async def _execute_create_document(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.documents import generator

    return await _render_and_save(
        "create_document", generator.render_document, arguments, ctx,
        arguments.get("title") or "", "문서 생성 중...",
    )


async def _execute_create_spreadsheet(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.documents import generator

    sheets = arguments.get("sheets") or []
    first_name = sheets[0].get("name", "") if sheets and isinstance(sheets[0], dict) else ""
    return await _render_and_save(
        "create_spreadsheet", generator.render_spreadsheet, arguments, ctx,
        first_name, "스프레드시트 생성 중...",
    )


async def _execute_create_presentation(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.documents import generator

    return await _render_and_save(
        "create_presentation", generator.render_presentation, arguments, ctx,
        arguments.get("title") or "", "발표 자료 생성 중...",
    )


CREATE_DOCUMENT = ToolSpec(
    name="create_document",
    description=CREATE_DOCUMENT_DESCRIPTION,
    parameters=CREATE_DOCUMENT_PARAMS,
    executor=_execute_create_document,
    strict=True,
)
CREATE_SPREADSHEET = ToolSpec(
    name="create_spreadsheet",
    description=CREATE_SPREADSHEET_DESCRIPTION,
    parameters=CREATE_SPREADSHEET_PARAMS,
    executor=_execute_create_spreadsheet,
    strict=True,
)
CREATE_PRESENTATION = ToolSpec(
    name="create_presentation",
    description=CREATE_PRESENTATION_DESCRIPTION,
    parameters=CREATE_PRESENTATION_PARAMS,
    executor=_execute_create_presentation,
    strict=True,
)
