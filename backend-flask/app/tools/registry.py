"""툴 레지스트리 - 턴마다 노출할 스펙 선택(get_tool_specs)과 실행(run_tool)."""

import asyncio
import logging
from typing import Any, AsyncIterator

from app.tools.documents import CREATE_DOCUMENT, CREATE_PRESENTATION, CREATE_SPREADSHEET
from app.tools.edit import EDIT_DOCUMENT, INSPECT_DOCUMENT
from app.tools.image import GENERATE_IMAGE
from app.tools.web_search import WEB_SEARCH
from app.tools.base import ToolContext, ToolResult, ToolSpec

logger = logging.getLogger("uvicorn.error")


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
