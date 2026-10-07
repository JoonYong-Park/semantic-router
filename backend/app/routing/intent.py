"""사용자 메시지로 이번 턴에 노출할 툴 그룹(문서 생성/이미지 생성/파일 수정)을
키워드 기반으로 추정한다. 모델 선택(selector.py)과는 독립이다."""


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
# 한글(hwpx) 문서 요청 표현
_HWPX_KEYWORDS = ("hwpx", "한글 파일", "한글파일", "한글 문서로", "한글로 만들", "아래아한글")
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

    for kw in _STRONG_FILE_KEYWORDS + _HWPX_KEYWORDS:
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


# --- 이미지 생성 의도 감지 ---
#
# "이미지"는 "도커 이미지", "이미지 분석", "이미지 파일 형식"처럼 생성과 무관한
# 용례가 많아서, 명사 단독으로는 감지하지 않고 생성 동사와 함께 나올 때만 본다.
# 문서 툴 의도와는 독립이며, 이미지 요청은 작은 모델로 충분해서 크기 상향은 없다.
_IMAGE_NOUNS = ("이미지", "그림", "일러스트", "썸네일", "배너", "포스터", "삽화", "배경화면",
                "아이콘", "캐릭터", "image", "picture", "illustration", "thumbnail")
_IMAGE_VERBS = ("만들어", "생성해", "생성", "그려", "제작해", "만들", "뽑아", "디자인해",
                "generate", "create", "make", "draw")
# 단독으로도 생성 요청이 분명한 표현
_IMAGE_STRONG = ("그림 그려", "그려줘", "그려 줘", "generate an image", "draw a", "draw me")
# 명사+동사가 함께 있어도 생성 요청이 아닌 대표 용례
_IMAGE_NEGATIVE = ("도커 이미지", "docker image", "이미지 분석", "이미지 파일 형식", "이미지 용량",
                   "이미지 크기 줄", "컨테이너 이미지", "이미지 빌드", "이미지를 분석", "이미지 인식")


def needs_image_tools(content: str) -> tuple[bool, str]:
    """사용자 메시지에 이미지 생성 의도가 있는지 추정한다. (감지 여부, 사유 키워드)."""
    text = (content or "").lower()
    negative = next((n for n in _IMAGE_NEGATIVE if n in text), None)
    if negative:
        return False, ""
    for kw in _IMAGE_STRONG:
        if kw in text:
            return True, f"keyword:{kw}"
    noun = next((n for n in _IMAGE_NOUNS if n in text), None)
    verb = next((v for v in _IMAGE_VERBS if v in text), None)
    if noun and verb:
        return True, f"noun+verb:{noun}+{verb}"
    return False, ""


# --- 기존 파일 수정 의도 감지 (트랙 3) ---
#
# 수정할 대상 파일이 이번 턴 첨부에 있거나, 이 대화/프로젝트에 이미 파일(업로드·생성·
# 참고자료)이 있을 때만 본다 - "방금 만든 파일에서 제목 바꿔줘"처럼 첨부 없는 후속
# 턴도 잡기 위함. 파일이 전혀 없으면 수정 툴을 노출할 이유가 없다.
_EDIT_VERBS = ("바꿔", "바꾸", "수정", "고쳐", "고치", "변경", "교체", "업데이트", "반영", "대체", "정정",
               "edit", "update", "replace", "change")
_EDIT_TARGETS = ("파일", "문서", "엑셀", "시트", "셀", "슬라이드", "표", "워드", "제목", "ppt", "pptx",
                 "docx", "xlsx", "발표자료", "보고서", "한글", "hwpx", "노트", "행", "열", "문단", "날짜",
                 "file", "sheet", "slide", "cell", "title")
_EDITABLE_MIME_MARKERS = ("officedocument", "hwp")


def needs_edit_tools(content: str, attachments: list | None = None, has_files_in_context: bool = False) -> tuple[bool, str]:
    """기존 파일 부분 수정 의도 추정. (감지 여부, 사유 키워드)."""
    text = (content or "").lower()
    verb = next((v for v in _EDIT_VERBS if v in text), None)
    if not verb:
        return False, ""
    has_editable_attachment = any(
        any(m in (getattr(a, "mime_type", "") or "") for m in _EDITABLE_MIME_MARKERS)
        for a in attachments or []
    )
    if not (has_editable_attachment or has_files_in_context):
        return False, ""
    target = next((t for t in _EDIT_TARGETS if t in text), None)
    if target or has_editable_attachment:
        return True, f"verb+target:{verb}+{target or 'attachment'}"
    return False, ""
