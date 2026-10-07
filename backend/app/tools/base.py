"""툴 공통 타입(ToolSpec / ToolContext / ToolResult)과 strict 스키마 헬퍼.

툴 하나 = ToolSpec 하나. llm/tool_format.py는 등록된 스펙을 회사별 tools 포맷으로
변환만 하고, chat의 툴 루프가 모델이 요청한 호출을 registry.run_tool()로 실행해
결과를 다시 모델에 돌려준다. llm 패키지가 tools를 import하지 않도록(순환 import 방지)
스펙은 호출부가 인자로 넘긴다."""

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from uuid import UUID


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


# 한 턴에 만들 수 있는 파일 수 상한 - 문서 생성과 수정본 생성이 같이 센다.
MAX_GENERATED_FILES_PER_TURN = 5


# strict 모드 스키마 헬퍼 - 문서/이미지/수정 툴이 같이 쓴다.
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
