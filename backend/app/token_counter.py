"""토큰 카운팅 (슬라이딩 윈도우 컨텍스트 관리용).

tiktoken은 "gpt-5.6-sol" 같은 우리 모델 ID를 모르므로 encoding_for_model()을
못 쓴다. cl100k_base로 고정해서 대략치를 구하고, 그마저 실패하면
len(text)//4로 폴백한다 (정확한 토큰 수가 아니라 컨텍스트 예산을 넘지 않기
위한 근사치면 충분).
"""

import tiktoken

_encoding = None


def count_tokens(text: str) -> int:
    global _encoding
    try:
        if _encoding is None:
            _encoding = tiktoken.get_encoding("cl100k_base")
        return len(_encoding.encode(text))
    except Exception:  # noqa: BLE001
        return len(text) // 4
