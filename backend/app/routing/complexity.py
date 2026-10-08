"""E5 임베딩 + easy/hard 예시 코사인 유사도로 0~1 복잡도 점수.

질문과 가장 가까운 easy 예시 TOP_K개, hard 예시 TOP_K개의 평균 유사도 차이를
sigmoid로 0~1에 펼친다. 예시 전체 평균을 쓰면 질문과 무관한 예시들까지 섞여
점수가 한쪽으로 쏠렸다(60문항 평가 기준 정확도 82% → top-5 87%)."""

import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoModel, AutoTokenizer

from app.routing.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES

MODEL_NAME = "intfloat/multilingual-e5-large"

_tokenizer = None
_model = None
_easy_ref: Tensor | None = None
_hard_ref: Tensor | None = None


def _average_pool(last_hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    last_hidden = last_hidden_states.masked_fill(
        ~attention_mask[..., None].bool(), 0.0
    )
    return last_hidden.sum(dim=1) / attention_mask.sum(dim=1)[..., None]


def _embed(texts: list[str]) -> Tensor:
    prefixed = [f"query: {t}" for t in texts]
    batch = _tokenizer(
        prefixed, max_length=512, padding=True, truncation=True, return_tensors="pt"
    )
    with torch.no_grad():
        outputs = _model(**batch)
    embeddings = _average_pool(outputs.last_hidden_state, batch["attention_mask"])
    return F.normalize(embeddings, p=2, dim=1)


def load_complexity_model() -> None:
    global _tokenizer, _model, _easy_ref, _hard_ref

    if _model is not None:
        return

    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    _model = AutoModel.from_pretrained(MODEL_NAME)
    _model.eval()

    _easy_ref = _embed(EASY_EXAMPLES)
    _hard_ref = _embed(HARD_EXAMPLES)


def is_ready() -> bool:
    return _model is not None


# E5는 이방성이 커서 easy/hard 평균 유사도 차이가 ±0.05~0.08 수준 — sigmoid 전에 증폭
DIFF_SCALE = 25.0

# 비교에 쓰는 가장 가까운 예시 수(easy/hard 각각).
TOP_K = 5


def complexity_score(text: str) -> float:
    if _model is None or _easy_ref is None or _hard_ref is None:
        raise RuntimeError("복잡도 모델이 아직 로드되지 않았습니다.")

    query_emb = _embed([text])

    easy_sim = (query_emb @ _easy_ref.T).squeeze(0)
    hard_sim = (query_emb @ _hard_ref.T).squeeze(0)
    easy_top = easy_sim.topk(min(TOP_K, easy_sim.numel())).values
    hard_top = hard_sim.topk(min(TOP_K, hard_sim.numel())).values

    diff = hard_top.mean().item() - easy_top.mean().item()
    return torch.sigmoid(torch.tensor(diff * DIFF_SCALE)).item()
