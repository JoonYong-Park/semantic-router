"""단일 테이블 `joomini`의 키 규칙. 설계 문서: docs/ko/DYNAMODB_DESIGN.md 3번.

키 문자열은 이 파일에서만 만든다. 다른 곳에서 "USER#" 같은 접두사를 직접 쓰지 않는다.
"""

# 파티션 키
def user_pk(uid) -> str:
    return f"USER#{uid}"


def conv_pk(cid) -> str:
    return f"CONV#{cid}"


def proj_pk(pid) -> str:
    return f"PROJ#{pid}"


# 정렬 키
PROFILE = "PROFILE"
SETTINGS = "SETTINGS"


def user_mem_sk(category: str, mem_id) -> str:
    return f"MEM#{category}#{mem_id}"


def proj_sk(pid) -> str:
    return f"PROJ#{pid}"


def conv_sk(cid) -> str:
    return f"CONV#{cid}"


def msg_sk(created_at: str, mid) -> str:
    # created_at이 앞에 있어서 SK 정렬이 곧 시간순이다.
    return f"MSG#{created_at}#{mid}"


def att_sk(aid) -> str:
    return f"ATT#{aid}"


def file_sk(created_at: str, fid) -> str:
    return f"FILE#{created_at}#{fid}"


def proj_mem_sk(mem_id) -> str:
    return f"MEM#{mem_id}"


def usage_day_sk(day: str, model: str) -> str:
    return f"USAGE#D#{day}#{model}"        # day: YYYY-MM-DD (KST)


def usage_hour_sk(hour: str, model: str) -> str:
    return f"USAGE#H#{hour}#{model}"       # hour: YYYY-MM-DDTHH (KST)


# 접두사 (begins_with 용)
MEM_PREFIX = "MEM#"
PROJ_PREFIX = "PROJ#"
CONV_PREFIX = "CONV#"
MSG_PREFIX = "MSG#"
ATT_PREFIX = "ATT#"
FILE_PREFIX = "FILE#"
USAGE_DAY_PREFIX = "USAGE#D#"
USAGE_HOUR_PREFIX = "USAGE#H#"

# GSI 이름
GSI_BY_UPDATED = "GSI1"   # 사용자 대화 최신순
GSI_BY_PROJECT = "GSI2"   # 프로젝트 소속 대화
GSI_BY_ID = "GSI3"        # id로 첨부, 파일, 메모리 찾기
