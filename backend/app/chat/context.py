"""시스템 프롬프트에 들어가는 텍스트 조각 - 메모리 토픽 직렬화와 파일 툴 사용 지침."""

import json
import logging


from app.core.models import (
    ProjectMemory,
    UserMemory,
)

logger = logging.getLogger("uvicorn.error")


# 문서 툴이나 수정 툴이 노출된 턴에만 시스템 프롬프트 맨 끝에 붙는 사용 지침.
DOC_TOOLS_GUIDE = (
    "[파일 생성/수정 지침]\n"
    "- 사용자가 파일(워드/PDF/엑셀/CSV/파워포인트/마크다운/텍스트)로 받기를 원하면 "
    "create_document / create_spreadsheet / create_presentation 툴을 호출해.\n"
    "- 툴을 호출하기 전에 장황하게 설명하지 마.\n"
    "- 호출한 뒤에는 만든 파일을 한두 문장으로만 안내하고, 파일 내용을 채팅에 다시 쓰지 마 "
    "(파일은 다운로드 카드로 이미 표시됨).\n"
    "- 단순히 표나 글을 채팅에서 보여 달라는 요청이면 툴을 쓰지 말고 바로 답해.\n"
    "- 본문 문장에 합계/평균 같은 계산 결과를 쓸 때는 표의 합계 행 값과 일치시키고, "
    "직접 계산한 숫자는 한 번 더 검산해(실제로 합계를 틀리게 쓴 사례가 있었음).\n"
    "- 기존 파일을 수정하려면 반드시 inspect_document로 현재 내용을 확인한 뒤 edit_document를 호출해. "
    "수정하지 못했거나 찾지 못한 항목이 있으면 사실대로 알려.\n"
    "- 대화 기록의 \"[첨부 파일: ... (id=...)]\", \"[참고자료: ... (id=...)]\", "
    "\"[생성된 파일: ... (id=...)]\" 줄은 시스템이 붙인 기록이야. id를 툴에 쓰는 데만 참고하고 "
    "네 답변에는 절대 그 형식을 쓰지 마."
)


def _memory_topic_to_text(memory: UserMemory | ProjectMemory) -> str:
    """UserMemory/ProjectMemory 토픽(name/summary/details) 한 건을 시스템
    프롬프트에 넣을 한 줄(+하위 불릿) 텍스트로 변환한다."""
    try:
        details = json.loads(memory.details) if memory.details else []
    except (json.JSONDecodeError, TypeError):
        details = []
    text = f"{memory.name}: {memory.summary}"
    if details:
        detail_lines = "\n".join(f"  · {d}" for d in details)
        text = f"{text}\n{detail_lines}"
    return text
