"""task_category + complexity_score -> ModelSpec."""

from app.models_config import (
    CATEGORY_TO_COMPANY,
    COMPLEXITY_MEDIUM_MAX,
    COMPLEXITY_SMALL_MAX,
    FALLBACK_COMPANY,
    Company,
    ModelSpec,
    Size,
    get_model,
)


def company_for_category(task_category: str) -> Company:
    return CATEGORY_TO_COMPANY.get(task_category, FALLBACK_COMPANY)


def size_for_complexity(complexity_score: float) -> Size:
    if complexity_score < COMPLEXITY_SMALL_MAX:
        return "small"
    if complexity_score < COMPLEXITY_MEDIUM_MAX:
        return "medium"
    return "large"


def select_model(task_category: str, complexity_score: float) -> ModelSpec:
    company = company_for_category(task_category)
    size = size_for_complexity(complexity_score)
    return get_model(company, size)


# --- 파일 생성 의도 감지 (문서 툴 노출 + auto 모드 크기 상향 판단) ---
#
# 모델이 실제로 툴을 부를지는 호출 시점에야 알 수 있으므로, 사용자 메시지로
# 미리 추정한다. 놓치면 문서 툴이 아예 안 실려서 파일을 못 만드므로 관대하게
# 잡고(오탐은 툴 정의 토큰이 조금 늘 뿐), 툴을 쓸지 말지는 모델이 판단한다.

# 단독으로 나와도 파일 의도로 보는 표현
_STRONG_FILE_KEYWORDS = (
    "엑셀", "워드 파일", "워드파일", "워드로", "파워포인트", "피피티", "ppt", "pptx", "docx", "xlsx",
    "csv", "pdf", "슬라이드", "발표자료", "발표 자료", "프레젠테이션", "다운로드", "내보내",
    "파일로", "문서로", "보고서로", "엑셀로", "시트로", "표로 정리", "마크다운 파일", "md 파일",
    "txt 파일", "텍스트 파일",
    # 영어 download/export는 코드 질문("export default" 등)에서 오탐이 많아 뺐다.
    "excel", "spreadsheet", "powerpoint", "slide deck", "word document",
)
# 만들기 계열 동사 + 문서성 명사가 함께 나올 때만 파일 의도로 본다
_CREATE_VERBS = ("만들어", "작성해", "생성해", "뽑아", "저장해", "만들어줘", "써줘", "정리해")
_DOC_NOUNS = ("파일", "문서", "보고서", "기획서", "제안서", "회의록", "계획서", "이력서", "명세서",
              "발표", "자료", "시트", "양식", "템플릿")
# 오피스/PDF 첨부가 있을 때 함께 나오면 변환/정리 요청으로 보는 동사
_ATTACHMENT_VERBS = ("변환", "바꿔", "정리", "요약", "추출", "옮겨", "만들어", "작성")
_OFFICE_MIME_MARKERS = ("officedocument", "pdf", "csv", "hwp", "markdown", "text/plain")


def needs_file_tools(content: str, attachments: list | None = None) -> tuple[bool, str]:
    """사용자 메시지에 파일 생성/변환 의도가 있는지 추정한다. (감지 여부, 사유)를
    돌려주며 사유에는 걸린 키워드만 담는다(로그용 - 메시지 본문은 안 남김).
    attachments는 이번 턴 첨부(mime_type 속성을 가진 객체)."""
    text = (content or "").lower()

    for kw in _STRONG_FILE_KEYWORDS:
        if kw in text:
            return True, f"keyword:{kw}"

    verb = next((v for v in _CREATE_VERBS if v in text), None)
    noun = next((n for n in _DOC_NOUNS if n in text), None)
    if verb and noun:
        return True, f"verb+noun:{verb}+{noun}"

    has_office_attachment = any(
        any(marker in (getattr(a, "mime_type", "") or "") for marker in _OFFICE_MIME_MARKERS)
        for a in attachments or []
    )
    if has_office_attachment:
        att_verb = next((v for v in _ATTACHMENT_VERBS if v in text), None)
        if att_verb:
            return True, f"attachment+verb:{att_verb}"

    return False, ""


_SIZE_ORDER: tuple[Size, ...] = ("small", "medium", "large")


def bump_size(spec: ModelSpec, min_size: Size = "medium") -> ModelSpec:
    """spec의 크기가 min_size보다 작으면 같은 회사의 min_size 모델로 올린다.
    긴 구조화 인자(문서 JSON)를 small 모델이 안정적으로 만들지 못하는 문제 대비."""
    if _SIZE_ORDER.index(spec.size) >= _SIZE_ORDER.index(min_size):
        return spec
    return get_model(spec.company, min_size)
