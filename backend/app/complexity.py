"""E5 임베딩 + easy/hard 예시 코사인 유사도로 0~1 복잡도 점수."""

import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoModel, AutoTokenizer

from app.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES

MODEL_NAME = "intfloat/multilingual-e5-small"

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


def complexity_score(text: str) -> float:
    if _model is None or _easy_ref is None or _hard_ref is None:
        raise RuntimeError("복잡도 모델이 아직 로드되지 않았습니다.")

    query_emb = _embed([text])

    easy_sim = (query_emb @ _easy_ref.T).squeeze(0)
    hard_sim = (query_emb @ _hard_ref.T).squeeze(0)

    diff = hard_sim.mean().item() - easy_sim.mean().item()
    return torch.sigmoid(torch.tensor(diff * DIFF_SCALE)).item()
