"""web_search 툴."""

import logging

from datetime import datetime
from zoneinfo import ZoneInfo

from app.websearch.providers import search_web
from app.tools.base import ToolContext, ToolResult, ToolSpec

logger = logging.getLogger("uvicorn.error")


# --- web_search (Tavily → Perplexity → Brave 폴백, search_web) ---

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_DESCRIPTION = "최신 정보, 실시간 정보, 특정 사실 확인이 필요할 때 웹을 검색한다. 날씨는 get_weather를 쓴다."
# 검색 결과 본문을 모델에 넘기는 길이. 메뉴 글자가 앞에 많은 페이지는 500자에서 본문이 잘렸다.
SNIPPET_CHARS = 1500
KST = ZoneInfo("Asia/Seoul")

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
}


def _normalize_time_range(value: str | None) -> str | None:
    """모델이 준 time_range 값을 Tavily가 받는 값으로 정리. "none"/빈 값/모델이
    스키마 밖의 값을 줬을 때는 전부 제한 없음(None)으로 취급한다."""
    if value in ("day", "week", "month", "year"):
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
        results, _provider = await search_web(
            query,
            max_results=5,
            time_range=_normalize_time_range(arguments.get("time_range")),
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
        f"{r.get('content', '')[:SNIPPET_CHARS]}"
        for i, r in enumerate(results)
    )
    return ToolResult(
        content=(
            f"[웹 검색 결과: {query}] (오늘: {datetime.now(KST):%Y-%m-%d} KST)\n{results_text}\n\n"
            "검색 결과는 검색 엔진이 저장해 둔 페이지라 오래됐을 수 있다. 실시간·최근 정보가 필요한 "
            "질문인데 결과의 날짜가 오늘과 다르거나 날짜를 알 수 없으면, 그 점을 답에 밝혀. "
            "글자가 깨졌거나 메뉴뿐인 결과는 무시해.\n"
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
