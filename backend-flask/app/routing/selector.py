"""task_category + complexity_score -> ModelSpec."""

from app.routing.catalog import (
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


_SIZE_ORDER: tuple[Size, ...] = ("small", "medium", "large")


def bump_size(spec: ModelSpec, min_size: Size = "medium") -> ModelSpec:
    """spec의 크기가 min_size보다 작으면 같은 회사의 min_size 모델로 올린다.
    긴 구조화 인자(문서 JSON)를 small 모델이 안정적으로 만들지 못하는 문제 대비."""
    if _SIZE_ORDER.index(spec.size) >= _SIZE_ORDER.index(min_size):
        return spec
    return get_model(spec.company, min_size)
