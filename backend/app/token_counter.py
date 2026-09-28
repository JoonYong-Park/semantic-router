"""토큰 카운팅 (슬라이딩 윈도우 컨텍스트 관리용).

tiktoken은 "gpt-5.6-sol" 같은 우리 모델 ID를 모르므로 encoding_for_model()을
못 쓴다. cl100k_base로 고정해서 대략치를 구하고, 그마저 실패하면
len(text)//4로 폴백한다 (정확한 토큰 수가 아니라 컨텍스트 예산을 넘지 않기
위한 근사치면 충분).
"""

import tiktoken

_encoding = None

# 이미지/문서(PDF) 1개당 대략적인 토큰 비용 근사치 - 슬라이딩 윈도우 예산
# 계산 전용이라 정확한 청구 토큰일 필요는 없다(실제 청구 토큰은 각 provider
# 응답의 usage를 그대로 저장해서 쓰므로 이 값과 무관 - usage.py 참고).
# 회사별 실제 공식(Anthropic ~(w*h)/750, OpenAI 타일 기반, Gemini 해상도
# 구간별)은 서로 다르고, "auto" 모드에서는 다음 턴에 어느 회사 모델이
# 쓰일지도 매번 달라질 수 있어 회사별로 정확히 맞추는 것 자체가 의미가
# 없다 - 그래서 업로드 시점에 리사이즈 상한(1568px)을 맞춰두는 이 프로젝트의
# 이미지 기준으로 대략적인 대푯값 하나만 쓴다.
IMAGE_TOKEN_ESTIMATE = 1100
DOCUMENT_TOKEN_ESTIMATE = 1500


def count_tokens(text: str) -> int:
    global _encoding
    try:
        if _encoding is None:
            _encoding = tiktoken.get_encoding("cl100k_base")
        return len(_encoding.encode(text))
    except Exception:  # noqa: BLE001
        return len(text) // 4


def estimate_content_tokens(content: str | list[dict]) -> int:
    """멀티모달 content(문자열 또는 text/image/document 블록 리스트)의
    슬라이딩 윈도우 예산용 근사 토큰 수. 텍스트 블록은 count_tokens 그대로,
    이미지/문서는 위 고정 근사치를 더한다."""
    if isinstance(content, str):
        return count_tokens(content)
    total = 0
    for block in content:
        if block["type"] == "text":
            total += count_tokens(block["text"])
        elif block["type"] == "image":
            total += IMAGE_TOKEN_ESTIMATE
        else:  # document (PDF)
            total += DOCUMENT_TOKEN_ESTIMATE
    return total
