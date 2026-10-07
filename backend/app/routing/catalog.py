"""9개 모델 카탈로그(models.json)와 라우팅 정책."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Company = Literal["openai", "google", "anthropic"]
Size = Literal["small", "medium", "large"]


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    company: Company
    size: Size
    display_name: str


_MODELS_FILE = Path(__file__).with_name("models.json")


def _load_catalog() -> dict[tuple[Company, Size], ModelSpec]:
    """models.json에서 9개 모델을 읽는다. 회사 x 크기 조합이 하나라도 빠지거나
    중복되면 기동 시점에 바로 실패시킨다(잘못된 설정으로 요청 중에 KeyError가
    나는 것보다 낫다)."""
    entries = json.loads(_MODELS_FILE.read_text(encoding="utf-8"))["models"]
    catalog: dict[tuple[Company, Size], ModelSpec] = {}
    for e in entries:
        key = (e["company"], e["size"])
        if key[0] not in get_args(Company) or key[1] not in get_args(Size):
            raise ValueError(f"models.json: 알 수 없는 회사/크기 {key}")
        if key in catalog:
            raise ValueError(f"models.json: 중복된 회사/크기 {key}")
        catalog[key] = ModelSpec(e["model_id"], e["company"], e["size"], e["display_name"])
    missing = [(c, sz) for c in get_args(Company) for sz in get_args(Size) if (c, sz) not in catalog]
    if missing:
        raise ValueError(f"models.json: 빠진 회사/크기 {missing}")
    return catalog


MODEL_CATALOG: dict[tuple[Company, Size], ModelSpec] = _load_catalog()

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

COMPLEXITY_SMALL_MAX = 0.4
COMPLEXITY_MEDIUM_MAX = 0.75

DEFAULT_MANUAL_SIZE: Size = "medium"


def get_model(company: Company, size: Size) -> ModelSpec:
    return MODEL_CATALOG[(company, size)]
