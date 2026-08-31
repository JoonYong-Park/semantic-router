"""9개 모델 카탈로그와 라우팅 정책."""

from dataclasses import dataclass
from typing import Literal

Company = Literal["openai", "google", "anthropic"]
Size = Literal["small", "medium", "large"]


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    company: Company
    size: Size
    display_name: str


MODEL_CATALOG: dict[tuple[Company, Size], ModelSpec] = {
    ("openai", "large"): ModelSpec("gpt-5.6-sol", "openai", "large", "GPT-5.6 Sol"),
    ("openai", "medium"): ModelSpec("gpt-5.6-terra", "openai", "medium", "GPT-5.6 Terra"),
    ("openai", "small"): ModelSpec("gpt-5.6-luna", "openai", "small", "GPT-5.6 Luna"),
    ("google", "large"): ModelSpec(
        "gemini-3.1-pro-preview", "google", "large", "Gemini 3.1 Pro"
    ),
    ("google", "medium"): ModelSpec(
        "gemini-3.6-flash", "google", "medium", "Gemini 3.6 Flash"
    ),
    ("google", "small"): ModelSpec(
        "gemini-3.5-flash-lite", "google", "small", "Gemini 3.5 Flash Lite"
    ),
    ("anthropic", "large"): ModelSpec(
        "claude-opus-5", "anthropic", "large", "Claude Opus 5"
    ),
    ("anthropic", "medium"): ModelSpec(
        "claude-sonnet-5", "anthropic", "medium", "Claude Sonnet 5"
    ),
    ("anthropic", "small"): ModelSpec(
        "claude-haiku-4-5-20251001", "anthropic", "small", "Claude Haiku 4.5"
    ),
}

# --- 라우팅 정책 1: task_category(14개 MMLU-Pro 카테고리) -> 회사 ---
# 분류기(llm-semantic-router/mmbert32k-intent-classifier-merged)가 반환하는
# id2label 문자열을 그대로 키로 쓴다.
# CATEGORY_TO_COMPANY: dict[str, Company] = {
#     # Claude 강점: 코딩, 글쓰기, 논리적 분석, 법률
#     "computer science": "anthropic",   # SWE-bench 1위, 코딩 최강
#     "engineering": "anthropic",        # 코드·기술 문서 품질 우수
#     "philosophy": "anthropic",         # 논리 분해·인문 추론 강점
#     "law": "anthropic",                # 인문학 계열 MMLU 최고점

#     # Gemini 강점: 과학, 멀티모달, STEM 추론
#     "biology": "google",               # GPQA(생물·화학·물리) 94.3%
#     "chemistry": "google",             # 과학 벤치마크 최강
#     "physics": "google",               # STEM 추론·다중 홉 강점
#     "health": "google",                # 의학·생명과학 멀티모달 우수

#     # GPT 강점: 일반 지식, 경영, 사회과학, 수학
#     "math": "openai",                  # GSM8K 96.8%, 구조적 추론
#     "economics": "openai",             # 사회과학 MMLU 고점 + 추론
#     "business": "openai",              # 일반 지식 MMLU 94.2% 선두
#     "history": "openai",               # 인문사회 지식 폭 넓음
#     "psychology": "openai",            # 사회과학 계열 GPT 강세
#     "other": "openai",                 # 범용 일반 지식 최강자
# }

# 데모버전용 모델 매핑
CATEGORY_TO_COMPANY: dict[str, Company] = {
    "computer science": "openai",
    "engineering": "openai",
    "philosophy": "openai",
    "law": "openai",

    "biology": "google",               # GPQA(생물·화학·물리) 94.3%
    "chemistry": "google",             # 과학 벤치마크 최강
    "physics": "google",               # STEM 추론·다중 홉 강점
    "health": "google",                # 의학·생명과학 멀티모달 우수

    "math": "openai",                  # GSM8K 96.8%, 구조적 추론
    "economics": "openai",             # 사회과학 MMLU 고점 + 추론
    "business": "openai",              # 일반 지식 MMLU 94.2% 선두
    "history": "openai",               # 인문사회 지식 폭 넓음
    "psychology": "openai",            # 사회과학 계열 GPT 강세
    "other": "openai",                 # 범용 일반 지식 최강자
}

FALLBACK_COMPANY: Company = "openai"

COMPLEXITY_SMALL_MAX = 0.3
COMPLEXITY_MEDIUM_MAX = 0.7

DEFAULT_MANUAL_SIZE: Size = "medium"


def get_model(company: Company, size: Size) -> ModelSpec:
    return MODEL_CATALOG[(company, size)]
