"""모델이 호출할 수 있는 툴 레지스트리 + 실행기.

툴 하나 = ToolSpec 하나. providers.py는 여기 등록된 스펙을 회사별 tools
포맷으로 변환만 하고, conversations.py의 툴 루프가 모델이 요청한 호출을
run_tool()로 실행해 결과를 다시 모델에 돌려준다. providers.py가 이 모듈을
import하지 않도록(순환 import 방지) 스펙은 호출부가 인자로 넘긴다.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import UUID

from app import providers

logger = logging.getLogger("uvicorn.error")


@dataclass
class ToolContext:
    """툴 실행에 필요한 요청 단위 컨텍스트. emit은 실행 도중 SSE 이벤트를
    바로 내보낼 때 쓴다(예: 검색 시작 시점의 searching) - 실행이 끝난 뒤가
    아니라 실행 중에 프론트에 보여야 하는 진행 표시용."""

    conversation_id: UUID
    project_id: UUID | None
    user_id: UUID
    session_factory: Callable[[], Any]
    # 한 턴(요청) 안의 모든 툴 호출이 공유하는 상태 - 예: 생성 파일 개수 상한
    turn_state: dict
    emit: Callable[[dict], None]


@dataclass
class ToolResult:
    """content는 모델에게 돌려줄 결과 문자열. events는 실행이 끝난 뒤 내보낼
    SSE 이벤트, metadata는 툴 루프가 DB 기록 등에 쓰는 값(모델에는 안 감)."""

    content: str
    is_error: bool = False
    events: list[dict] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    executor: Callable[[dict, ToolContext], Awaitable[ToolResult]]
    strict: bool = False


# --- web_search ---

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_DESCRIPTION = "최신 정보, 실시간 정보, 특정 사실 확인이 필요할 때 웹을 검색한다."

# Tavily는 기본적으로 최신순 필터 없이 검색해서, "오늘 날씨"처럼 시의성 있는
# 질문에도 몇 년 전 글이 섞여 들어온다 (실제로 "2026년 9월 17일" 같은 날짜를
# 검색어에 넣었더니 날짜 숫자만 겹치는 옛날 글/무관한 글이 섞이는 걸 확인함).
# time_range로 무조건 최근으로 제한하면 이번엔 일반 지식 검색(예: "파이썬 문법")
# 결과가 씨가 마르므로, 검색이 필요한지와 마찬가지로 시간 범위도 모델이 질문
# 성격을 보고 직접 판단해서 선택하게 한다.
_WEB_SEARCH_PROPERTIES = {
    "query": {"type": "string", "description": "검색할 질의어 (간결한 핵심 키워드로 작성하고, 날짜를 문자열로 그대로 넣지 말 것)"},
    "time_range": {
        "type": "string",
        "enum": ["day", "week", "month", "year", "none"],
        "description": (
            "결과를 최신으로 좁힐 범위. 오늘/실시간/최근 이슈처럼 시의성이 "
            "중요하면 day나 week, 그렇지 않은 일반 지식/사실 확인이면 none."
        ),
    },
    "site_hint": {
        "type": "string",
        "enum": ["weather", "none"],
        "description": (
            "실시간 날씨/기온/강수확률/미세먼지 질문이면 'weather'로 지정한다 - "
            "일반 뉴스/블로그가 아니라 신뢰할 수 있는 기상 사이트로 검색을 좁힌다. "
            "그 외 질문은 'none'."
        ),
    },
}


def _normalize_time_range(value: str | None) -> str | None:
    """모델이 준 time_range 값을 Tavily가 받는 값으로 정리. "none"/빈 값/모델이
    스키마 밖의 값을 줬을 때는 전부 제한 없음(None)으로 취급한다."""
    if value in ("day", "week", "month", "year"):
        return value
    return None


def _normalize_site_hint(value: str | None) -> str | None:
    """site_hint도 time_range와 동일하게 스키마에 정의된 값만 인정한다."""
    if value in providers.SITE_HINT_DOMAINS:
        return value
    return None


async def _execute_web_search(arguments: dict, ctx: ToolContext) -> ToolResult:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolResult(content="query가 비어 있어 검색하지 못했습니다.", is_error=True)

    ctx.emit({"type": "searching", "query": query})
    # web_search_used/web_search_query는 검색을 실제로 시도한 시점 기준으로
    # 기록한다(예전 구조에서도 검색 결과가 0건이거나 실패해도 기록됐음).
    metadata = {"web_search_query": query}
    try:
        results = await providers.tavily_search(
            query,
            max_results=5,
            time_range=_normalize_time_range(arguments.get("time_range")),
            site_hint=_normalize_site_hint(arguments.get("site_hint")),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Tavily 검색 실패 (query=%s): %s", query, exc)
        return ToolResult(
            content=f"웹 검색에 실패했습니다: {exc}. 검색 없이 답하되, 최신 정보를 확인하지 못했다는 점을 사용자에게 알려줘.",
            is_error=True,
            metadata=metadata,
        )

    if not results:
        return ToolResult(
            content=(
                f"[웹 검색 결과: {query}]\n검색 결과가 0건입니다. 검색 결과 없이 "
                "답하되, 최신 정보를 확인하지 못했다는 점을 사용자에게 알려줘."
            ),
            metadata=metadata,
        )

    results_text = "\n\n".join(
        f"[{i + 1}] {r.get('title', '')}\n{r.get('url', '')}\n"
        f"{r.get('content', '')[:500]}"
        for i, r in enumerate(results)
    )
    return ToolResult(
        content=(
            f"[웹 검색 결과: {query}]\n{results_text}\n\n"
            "위 검색 결과를 참고해서 답변해. 답변 마지막에 실제로 "
            "참고한 출처만 아래 형식으로 표시해 (참고 안 한 결과는 빼고, "
            "위 검색 결과에 없는 URL은 지어내지 마):\n"
            "---\n출처:\n- [번호] 제목 (URL)"
        ),
        metadata=metadata,
    )


WEB_SEARCH = ToolSpec(
    name=WEB_SEARCH_TOOL_NAME,
    description=WEB_SEARCH_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": _WEB_SEARCH_PROPERTIES,
        "required": ["query"],
        "additionalProperties": False,
    },
    executor=_execute_web_search,
)


# --- 문서 생성 툴 3종 (렌더러는 docgen.py) ---

MAX_GENERATED_FILES_PER_TURN = 5

# strict 모드(OpenAI/Anthropic)를 위해 모든 필드를 required로, 객체마다
# additionalProperties:false, optional/union/재귀 없이 정의한다. 쓰지 않는 필드는
# 빈 값("" / [] / 0)으로 보내게 하고, 셀 주소/좌표는 받지 않는다.
_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}
_ROWS = {"type": "array", "items": _STR_LIST}


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


CREATE_DOCUMENT_PARAMS = _obj({
    "format": {"type": "string", "enum": ["docx", "pdf", "md", "txt"],
               "description": "만들 파일 형식"},
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
    "사용자가 워드(docx), PDF, 마크다운(md), 텍스트(txt) 파일로 받기를 원할 때 문서 파일을 만든다. "
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
    from app import attachments, docgen
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
    except docgen.DocValidationError as exc:
        return ToolResult(content=f"인자 검증 실패: {exc}. 고쳐서 다시 호출하세요.", is_error=True)

    filename = docgen.sanitize_filename(arguments.get("filename", ""), title_for_name)
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
        attachment = await attachments.save_generated_file(
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
    from app import docgen

    return await _render_and_save(
        "create_document", docgen.render_document, arguments, ctx,
        arguments.get("title") or "", "문서 생성 중...",
    )


async def _execute_create_spreadsheet(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app import docgen

    sheets = arguments.get("sheets") or []
    first_name = sheets[0].get("name", "") if sheets and isinstance(sheets[0], dict) else ""
    return await _render_and_save(
        "create_spreadsheet", docgen.render_spreadsheet, arguments, ctx,
        first_name, "스프레드시트 생성 중...",
    )


async def _execute_create_presentation(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app import docgen

    return await _render_and_save(
        "create_presentation", docgen.render_presentation, arguments, ctx,
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

DOC_TOOL_NAMES = (CREATE_DOCUMENT.name, CREATE_SPREADSHEET.name, CREATE_PRESENTATION.name)


# --- 레지스트리 ---

_REGISTRY: dict[str, ToolSpec] = {
    WEB_SEARCH.name: WEB_SEARCH,
    CREATE_DOCUMENT.name: CREATE_DOCUMENT,
    CREATE_SPREADSHEET.name: CREATE_SPREADSHEET,
    CREATE_PRESENTATION.name: CREATE_PRESENTATION,
}


def get_tool_specs(include_doc_tools: bool = False) -> list[ToolSpec]:
    """요청에 실을 툴 목록. 문서 툴은 파일 의도가 감지된 턴에만 노출한다
    (router.needs_file_tools) - 일반 질문에 툴 정의 토큰과 잘못된 호출을 줄이기 위함."""
    return [
        spec for spec in _REGISTRY.values()
        if include_doc_tools or spec.name not in DOC_TOOL_NAMES
    ]


async def run_tool(call: dict, ctx_base: dict) -> AsyncIterator[tuple[str, Any]]:
    """툴 호출 하나를 실행하면서 ("event", sse_dict)를 실행 도중 바로 yield하고,
    마지막에 ("result", ToolResult)를 한 번 yield한다.

    executor가 ctx.emit으로 넣은 이벤트를 실행이 끝나기 전에 내보내야 해서
    (예: 검색 중 표시) executor를 태스크로 띄우고 큐를 같이 기다린다. 이
    제너레이터가 중간에 닫히면(클라이언트 중단 등) finally에서 태스크를 취소한다.
    툴 실행 중 예외는 is_error 결과로 바꿔서 모델에 돌려준다(루프는 계속).
    """
    spec = _REGISTRY.get(call["name"])
    if spec is None:
        yield "result", ToolResult(content=f"알 수 없는 툴입니다: {call['name']}", is_error=True)
        return
    if call.get("truncated"):
        yield "result", ToolResult(
            content=(
                "출력이 길이 제한으로 잘렸습니다. 내용을 줄이거나 슬라이드/행 수를 "
                "줄여 다시 호출하세요."
            ),
            is_error=True,
        )
        return
    if call.get("parse_error") or call.get("arguments") is None:
        yield "result", ToolResult(
            content=(
                "툴 인자를 JSON으로 해석하지 못했습니다(출력이 잘렸거나 형식 오류). "
                f"받은 인자 앞부분: {call.get('raw_arguments', '')[:200]}"
            ),
            is_error=True,
        )
        return

    queue: asyncio.Queue[dict] = asyncio.Queue()
    ctx = ToolContext(**ctx_base, emit=queue.put_nowait)
    task = asyncio.create_task(spec.executor(call["arguments"], ctx))
    getter: asyncio.Task | None = None
    try:
        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait(
                {task, getter}, return_when=asyncio.FIRST_COMPLETED
            )
            if getter in done:
                yield "event", getter.result()
            else:
                getter.cancel()
            if task in done:
                break
        while not queue.empty():
            yield "event", queue.get_nowait()

        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001
            logger.error("툴 실행 실패 (%s): %s", call["name"], exc, exc_info=True)
            result = ToolResult(content=f"툴 실행 중 오류가 발생했습니다: {exc}", is_error=True)
        for event in result.events:
            yield "event", event
        yield "result", result
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
        if not task.done():
            task.cancel()
