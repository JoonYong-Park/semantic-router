"""모델이 호출할 수 있는 툴 레지스트리 + 실행기.

툴 하나 = ToolSpec 하나. providers.py는 여기 등록된 스펙을 회사별 tools
포맷으로 변환만 하고, conversations.py의 툴 루프가 모델이 요청한 호출을
run_tool()로 실행해 결과를 다시 모델에 돌려준다. providers.py가 이 모듈을
import하지 않도록(순환 import 방지) 스펙은 호출부가 인자로 넘긴다.
"""

import asyncio
import io
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
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


# --- web_search (Tavily → Perplexity → Brave 폴백, providers.search_web) ---

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
        # Tavily가 에러면 Perplexity, 그것도 실패하면 Brave로 자동 폴백한다.
        results, _provider = await providers.search_web(
            query,
            max_results=5,
            time_range=_normalize_time_range(arguments.get("time_range")),
            site_hint=_normalize_site_hint(arguments.get("site_hint")),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("웹 검색 실패 (query=%s): %s", query, exc)
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

# --- 이미지 생성 툴 ---

MAX_IMAGES_PER_TURN = 2
IMAGE_MODEL = "gpt-image-2.5-flare"
IMAGE_SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
IMAGE_QUALITIES = {"standard": "medium", "high": "high"}

GENERATE_IMAGE_PARAMS = _obj({
    "prompt": {
        "type": "string",
        "description": (
            "만들 이미지 설명. 영어 또는 한국어 모두 가능. 이미지 안에 넣을 글자가 있으면 "
            "따옴표로 정확히 명시. 실존 인물이나 상표/로고를 그리는 요청은 쓰지 말 것"
        ),
    },
    "aspect_ratio": {"type": "string", "enum": ["square", "landscape", "portrait"],
                     "description": "square=정사각형, landscape=가로형(슬라이드/배너), portrait=세로형(포스터)"},
    "quality": {"type": "string", "enum": ["standard", "high"],
                "description": "보통은 standard, 사용자가 고화질을 원할 때만 high"},
    "format": {"type": "string", "enum": ["png", "jpg"], "description": "파일 형식, 특별한 요청이 없으면 png"},
})

GENERATE_IMAGE_DESCRIPTION = (
    "사용자가 이미지, 그림, 일러스트, 썸네일, 배너, 포스터, 배경 이미지 등을 새로 만들어 달라고 할 때 이미지 1장을 생성한다. "
    "차트·표·도표는 이 툴이 아니라 문서 툴이나 채팅 답변으로 처리하고, 코드나 SVG를 요청한 경우에도 호출하지 않는다. "
    "한 번의 답변에서는 1장이 기본이며 사용자가 여러 장을 명시적으로 원할 때만 더 호출한다(최대 2장). "
    "quality는 사용자가 고화질/고해상도를 명시하지 않으면 항상 standard로 한다(high는 비용이 큼). "
    "만든 이미지는 사용자에게 카드로 바로 표시되므로 답변에 이미지 URL이나 마크다운 이미지 링크를 만들지 말고 "
    "어떤 이미지를 만들었는지 한두 문장으로만 안내한다."
)


def _encode_image(png_bytes: bytes, fmt: str) -> bytes:
    """API가 준 PNG를 Pillow로 열어 검증하고, jpg면 RGB로 변환해 저장한다."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    img.load()
    if fmt == "png":
        return png_bytes
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _execute_generate_image(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app import attachments
    from app.schemas import AttachmentOut

    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return ToolResult(content="prompt가 비어 있습니다. 만들 이미지를 구체적으로 설명해 다시 호출하세요.", is_error=True)
    size = IMAGE_SIZES.get(arguments.get("aspect_ratio"))
    quality = IMAGE_QUALITIES.get(arguments.get("quality"))
    fmt = arguments.get("format")
    if size is None or quality is None or fmt not in ("png", "jpg"):
        return ToolResult(
            content=(
                "인자 검증 실패: aspect_ratio는 square/landscape/portrait, quality는 standard/high, "
                f"format은 png/jpg 중 하나여야 합니다 (받은 값: {arguments.get('aspect_ratio')!r}, "
                f"{arguments.get('quality')!r}, {fmt!r})."
            ),
            is_error=True,
        )

    count = ctx.turn_state.get("image_count", 0)
    if count >= MAX_IMAGES_PER_TURN:
        return ToolResult(
            content=f"한 번의 답변에서 이미지는 최대 {MAX_IMAGES_PER_TURN}장까지 만들 수 있습니다. 더 만들지 말고 만든 이미지를 안내하세요.",
            is_error=True,
        )
    model = IMAGE_MODEL
    ctx.emit({"type": "processing", "message": "이미지 생성 중..."})
    started = time.monotonic()
    try:
        png = await providers.generate_image(model, prompt, size, quality)
    except providers.ImageRateLimitError:
        return ToolResult(
            content="이미지 생성 요청이 많아 잠시 제한되었습니다(429). 이미지를 만들지 못했다고 알리고 잠시 후 다시 시도해 달라고 안내하세요.",
            is_error=True,
        )
    except providers.ImageContentPolicyError as exc:
        return ToolResult(
            content=f"이미지 생성이 콘텐츠 정책에 따라 거부되었습니다. 사유: {exc}. 이 사유를 사용자에게 그대로 알리고, 다른 표현으로 요청해 달라고 안내하세요.",
            is_error=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("이미지 생성 실패 (model=%s, size=%s): %s", model, size, exc, exc_info=True)
        return ToolResult(
            content="이미지 생성 중 일시적인 오류가 발생했습니다. 이미지를 만들지 못했다고 알리고 잠시 후 다시 시도해 달라고 안내하세요.",
            is_error=True,
        )
    elapsed = time.monotonic() - started
    logger.info("이미지 생성 완료 (model=%s, size=%s, quality=%s, %.1fs)", model, size, quality, elapsed)

    data = await asyncio.to_thread(_encode_image, png, fmt)
    display_name = f"이미지_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.{fmt}"
    meta = {"kind": "image", "tool": "generate_image", "model": model, "size": size, "quality": quality, "format": fmt}
    try:
        attachment = await attachments.save_generated_file(ctx.conversation_id, display_name, fmt, data, meta)
    except ValueError as exc:  # 25MB 상한
        return ToolResult(content=str(exc), is_error=True)

    ctx.turn_state["image_count"] = count + 1
    ctx.emit({"type": "file", "attachment": AttachmentOut.model_validate(attachment).model_dump(mode="json")})
    payload = {
        "ok": True,
        "file_id": str(attachment.id),
        "filename": display_name,
        "size": size,
        "note": "이미지는 이미 사용자에게 카드로 표시됨. URL이나 이미지 링크를 만들지 말고 한두 문장으로만 안내할 것.",
    }
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        metadata={"generated_attachment_id": attachment.id},
    )


GENERATE_IMAGE = ToolSpec(
    name="generate_image",
    description=GENERATE_IMAGE_DESCRIPTION,
    parameters=GENERATE_IMAGE_PARAMS,
    executor=_execute_generate_image,
    strict=True,
)


# --- 기존 파일 부분 수정 툴 (트랙 3, 구현은 docedit.py) ---

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
    반환: (원본 바이트, 표시 이름, 확장자, Attachment id 또는 None, 출처 설명)."""
    from pathlib import Path
    from uuid import UUID as _UUID

    from app import attachments, docedit
    from app.db_models import Attachment, ProjectFile

    try:
        file_id = _UUID(str(attachment_id).strip())
    except ValueError:
        raise docedit.EditError(f"attachment_id {attachment_id!r}가 올바른 id 형식이 아닙니다. 대화 기록의 (id=...) 값을 그대로 쓰세요")
    async with ctx.session_factory() as session:
        row = await session.get(Attachment, file_id)
        source = "attachment"
        if row is None or row.chat_id != ctx.conversation_id:
            row = await session.get(ProjectFile, file_id)
            source = "project_file"
            if row is None or ctx.project_id is None or row.project_id != ctx.project_id:
                raise docedit.EditError(f"id {attachment_id}인 파일을 이 대화(또는 이 프로젝트 참고자료)에서 찾을 수 없습니다")
    name = row.original_name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext == "hwp":
        raise docedit.EditError("hwp(구형 한글) 파일은 수정할 수 없습니다. 한글에서 hwpx로 저장(변환)한 뒤 다시 올려 주세요")
    if ext not in docedit.supported_formats():
        raise docedit.EditError(f"{name}은(는) 수정할 수 없는 형식입니다. 수정 가능 형식: {', '.join(docedit.supported_formats())}")
    path = attachments._resolve_under_upload_root(Path(row.stored_path))
    if path.suffix.lower().lstrip(".") != ext:
        # 텍스트가 거의 없는 문서는 업로드 시 PDF로 변환돼 stored_path가 바뀌지만,
        # 원본은 같은 폴더에 "{id}.{원래 확장자}"로 남아 있다(attachments._delete_stored_files 참고).
        original = path.with_name(f"{row.id}.{ext}")
        if not original.exists():
            raise docedit.EditError(f"{name}의 원본 파일을 찾을 수 없어 수정할 수 없습니다(스캔본으로 PDF 변환됨)")
        path = attachments._resolve_under_upload_root(original)
    data = await asyncio.to_thread(path.read_bytes)
    return data, name, ext, (row.id if source == "attachment" else None), source


async def _execute_inspect_document(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app import docedit

    try:
        data, name, ext, _, _ = await _resolve_file(ctx, arguments.get("attachment_id", ""))
        insp = await asyncio.to_thread(docedit.inspect_bytes, data, ext)
        text = docedit.render_inspection(insp, arguments.get("scope", "all"), name)
    except docedit.EditError as exc:
        return ToolResult(content=f"inspect 실패: {exc}", is_error=True)
    return ToolResult(content=text)


async def _unique_edit_name(ctx: ToolContext, original_name: str) -> str:
    from sqlalchemy import select

    from app.db_models import Attachment

    base, _, ext = original_name.rpartition(".")
    base = base or original_name
    # 수정본을 또 수정하면 "_수정_수정"이 되지 않게 기존 접미사는 뗀다
    base = re.sub(r"_수정\d*$", "", base)
    async with ctx.session_factory() as session:
        result = await session.execute(
            select(Attachment.original_name).where(Attachment.chat_id == ctx.conversation_id)
        )
        existing = set(result.scalars().all())
    candidate, n = f"{base}_수정.{ext}", 2
    while candidate in existing:
        candidate = f"{base}_수정{n}.{ext}"
        n += 1
    return candidate


async def _execute_edit_document(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app import attachments, docedit
    from app.schemas import AttachmentOut

    generated = ctx.turn_state.setdefault("generated_attachment_ids", [])
    if len(generated) >= MAX_GENERATED_FILES_PER_TURN:
        return ToolResult(content=f"한 번의 답변에서 파일은 최대 {MAX_GENERATED_FILES_PER_TURN}개까지 만들 수 있습니다.", is_error=True)
    try:
        data, name, ext, source_id, source = await _resolve_file(ctx, arguments.get("attachment_id", ""))
        ctx.emit({"type": "processing", "message": "파일 수정 중..."})
        new_bytes, summary, warnings, changed = await asyncio.to_thread(
            docedit.apply_edits, data, ext, arguments.get("doc_revision", ""), arguments.get("operations") or []
        )
    except docedit.EditError as exc:
        return ToolResult(content=f"edit 실패: {exc}", is_error=True)

    display_name = await _unique_edit_name(ctx, name)
    meta = {
        "kind": "edit",
        "tool": "edit_document",
        "format": ext,
        "base_revision": docedit.revision_of(data),
        "source": source,
        "source_id": str(arguments.get("attachment_id", "")),
        "ops": [{"target_id": s["target_id"], "location": s["location"]} for s in summary],
        "changed_parts": changed,
        "warnings": warnings,
    }
    try:
        attachment = await attachments.save_generated_file(
            ctx.conversation_id, display_name, ext, new_bytes, meta, source_attachment_id=source_id
        )
    except ValueError as exc:
        return ToolResult(content=str(exc), is_error=True)
    generated.append(attachment.id)
    ctx.emit({"type": "file", "attachment": AttachmentOut.model_validate(attachment).model_dump(mode="json")})
    payload = {
        "ok": True,
        "file_id": str(attachment.id),
        "filename": display_name,
        "new_doc_revision": docedit.revision_of(new_bytes),
        "applied": summary,
        "warnings": warnings,
        "note": ("수정본은 이미 사용자에게 다운로드 카드로 표시됨(원본은 그대로). 무엇을 바꿨는지 간단히 안내할 것. "
                 "이 수정본을 또 고치려면 새 file_id로 inspect_document부터 다시 호출할 것."),
    }
    return ToolResult(content=json.dumps(payload, ensure_ascii=False),
                      metadata={"generated_attachment_id": attachment.id})


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


# --- 레지스트리 ---

_REGISTRY: dict[str, ToolSpec] = {
    WEB_SEARCH.name: WEB_SEARCH,
    CREATE_DOCUMENT.name: CREATE_DOCUMENT,
    CREATE_SPREADSHEET.name: CREATE_SPREADSHEET,
    CREATE_PRESENTATION.name: CREATE_PRESENTATION,
    GENERATE_IMAGE.name: GENERATE_IMAGE,
    INSPECT_DOCUMENT.name: INSPECT_DOCUMENT,
    EDIT_DOCUMENT.name: EDIT_DOCUMENT,
}


def get_tool_specs(
    include_doc_tools: bool = False,
    include_image_tools: bool = False,
    include_edit_tools: bool = False,
) -> list[ToolSpec]:
    """요청에 실을 툴 목록. web_search는 항상, 나머지는 의도가 감지된 턴에만
    노출한다(router.needs_file_tools / needs_image_tools / needs_edit_tools) -
    일반 질문에 툴 정의 토큰과 잘못된 호출을 줄이기 위함."""
    specs = [WEB_SEARCH]
    if include_doc_tools:
        specs += [CREATE_DOCUMENT, CREATE_SPREADSHEET, CREATE_PRESENTATION]
    if include_image_tools:
        specs.append(GENERATE_IMAGE)
    if include_edit_tools:
        specs += [INSPECT_DOCUMENT, EDIT_DOCUMENT]
    return specs


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
        if result.is_error:
            # 모델이 에러를 사용자에게 다르게 풀어 말할 수 있어서 원문을 남긴다(파일 내용은 없음).
            logger.warning("툴 에러 결과 (%s): %s", call["name"], result.content[:200].replace("\n", " "))
        for event in result.events:
            yield "event", event
        yield "result", result
    finally:
        if getter is not None and not getter.done():
            getter.cancel()
        if not task.done():
            task.cancel()
