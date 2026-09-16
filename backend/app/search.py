"""대화 검색 - 채팅방 제목/메시지 본문에서 검색어를 부분 매칭으로 찾는다.

conversations.py/usage.py와 마찬가지로 로그인이 없는 데모라 DEMO_USER_ID
하나를 기준으로 검색한다. Semantic Router/스트리밍/컨텍스트 유지/메모리/
토큰 사용량 로직은 건드리지 않는다.

한국어는 형태소 분석 없이 to_tsvector('simple', ...)를 써도 여전히
띄어쓰기/구두점 단위로만 토큰화되어 "렌탈"로 "렌탈팀"을 못 찾는 문제가
남는다. 이 인프라(Alpine 공식 postgres 이미지)엔 mecab류 형태소 분석
확장이 없어서, 대신 pg_trgm 확장을 쓴다. CREATE EXTENSION/GIN 인덱스는
기존 컬럼 추가와 동일하게 psql로 수동 실행해야 한다 (create_all()은
기존 테이블을 안 건드림).

주의: 필터링에는 pg_trgm의 `%`(similarity) 연산자가 아니라 ILIKE를 쓴다.
`%`는 두 문자열 "전체"의 트라이그램 유사도라서, 짧은 검색어를 긴 메시지
본문과 비교하면 겹치는 트라이그램 비율이 낮아 기본 임계값(0.3)에 한참
못 미쳐 매칭 자체가 안 되는 문제가 있었다 (예: "제육덮밥" 검색 시 그
단어가 포함된 500자짜리 답변의 similarity가 0.018에 불과해 매칭 실패).
ILIKE는 진짜 "포함 여부"를 보고, pg_trgm의 GIN 인덱스가 LIKE/ILIKE
패턴도 가속해준다. similarity()는 매칭된 결과들의 정렬(관련도순) 용도로만 쓴다.
"""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db_models import DEMO_USER_ID, Conversation, Message
from app.schemas import SearchResultOut

router = APIRouter(prefix="/search", tags=["search"])

# 채팅방당 1건으로 묶기 전, pg_trgm 매칭 원시 행 수 상한 (데모 규모에 충분).
MAX_RAW_MATCHES = 200
SNIPPET_CONTEXT_CHARS = 30


def _like_pattern(query: str) -> str:
    """사용자 입력을 ILIKE 패턴으로 안전하게 감싼다. LIKE의 와일드카드 문자
    (%, _)와 이스케이프 문자(\\)를 리터럴로 취급하도록 이스케이프해야,
    검색어에 %/_가 들어 있어도 오작동(과다 매칭)하지 않는다."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(content: str, query: str) -> str:
    """검색어가 실제로 나오는 위치 앞뒤로 잘라서 발췌한다. 트라이그램 유사도는
    부분 문자열이 아니어도 매칭될 수 있어서(오탈자 허용), 못 찾으면 그냥
    본문 앞부분을 보여준다."""
    idx = content.lower().find(query.lower())
    if idx == -1:
        return content[:80] + ("..." if len(content) > 80 else "")

    start = max(0, idx - SNIPPET_CONTEXT_CHARS)
    end = min(len(content), idx + len(query) + SNIPPET_CONTEXT_CHARS)
    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."
    return snippet


@router.get("", response_model=list[SearchResultOut])
async def search_conversations(
    query: str, session: AsyncSession = Depends(get_session)
) -> list[SearchResultOut]:
    query = query.strip()
    if not query:
        return []

    title_similarity = func.similarity(func.coalesce(Conversation.title, ""), query)
    content_similarity = func.similarity(Message.content, query)
    relevance = func.greatest(title_similarity, content_similarity)

    pattern = _like_pattern(query)

    result = await session.execute(
        select(
            Conversation.id,
            Conversation.title,
            Message.role,
            Message.content,
        )
        .select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == DEMO_USER_ID,
            Message.content.ilike(pattern, escape="\\")
            | Conversation.title.ilike(pattern, escape="\\"),
        )
        .order_by(relevance.desc())
        .limit(MAX_RAW_MATCHES)
    )

    # 이미 관련도 순으로 정렬돼 있으므로, 채팅방마다 처음 나오는 행(=가장
    # 관련도 높은 메시지)만 남기면 "채팅방당 1건, 관련도순" 그룹핑이 된다.
    seen: set[UUID] = set()
    results: list[SearchResultOut] = []
    for conv_id, title, role, content in result.all():
        if conv_id in seen:
            continue
        seen.add(conv_id)
        results.append(
            SearchResultOut(
                conversation_id=conv_id,
                title=title,
                snippet=_snippet(content, query),
                matched_role=role,
            )
        )

    return results
