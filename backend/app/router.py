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
