"""E5 임베딩 + 라벨된 참조 문장 kNN으로 질문 크기(small/medium/large)를 판단한다.

참조 데이터:
- complexity_reference.json
  - eval_v2: 평가셋 v2 100문항(기준표로 재채점한 small/medium/large 라벨)
  - v3_aug: 실사용형 업무 요청 80문항(길이·난이도를 고르게 섞은 보정용)
- complexity_examples.py: EASY(small) 24개 / HARD(large) 22개 예시

질문과 코사인 유사도가 가장 높은 참조 문장 K_NEIGHBORS개가 유사도만큼 자기 크기에
투표하고, 득표가 가장 많은 크기를 고른다.

이전 방식(easy/hard top-5 평균 유사도 차이를 sigmoid)은 medium 라벨을 쓸 수 없고
대형 경계가 지나치게 보수적이었다(v2 교차검증 일치율 71%, large 재현율 17%).

v3_aug를 넣은 이유: v2와 예시에는 '길지만 쉬운' 문장이 없어서 kNN이 질문 길이를
난이도로 배웠다(긴 업무 요청이 거의 다 large로 감). 실사용형 검증셋(v3 test,
참조에 넣지 않음)에서 긴 쉬운 요청의 small 판정 17% → 83%, 과잉 상향 12 → 3건.
v2 교차검증 일치율은 80.8%로 유지(notebooks/eval/README.md).

반환값은 기존 인터페이스(0~1 점수 + catalog.py 임계값 0.4/0.80)를 유지하도록
고른 크기의 구간 안으로 매핑한다. 구간 안 위치는 득표 비율로 정해, 같은 크기
안에서도 '더 어려울수록 큰 점수'가 된다."""

import json
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import Tensor
from transformers import AutoModel, AutoTokenizer

from app.routing.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES

MODEL_NAME = "intfloat/multilingual-e5-large"

# 투표에 참여하는 최근접 참조 문장 수. 교차검증에서 k=5/7/11 중 11이 가장 좋았다.
K_NEIGHBORS = 11

_SIZES = ("small", "medium", "large")
_REFERENCE_FILE = Path(__file__).with_name("complexity_reference.json")

_tokenizer = None
_model = None
_ref_emb: Tensor | None = None
_ref_label: Tensor | None = None


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


def _load_reference() -> tuple[list[str], list[int]]:
    """참조 문장과 크기 인덱스(0 small / 1 medium / 2 large)."""
    items = json.loads(_REFERENCE_FILE.read_text(encoding="utf-8"))["items"]
    texts = [it["text"] for it in items]
    labels = [_SIZES.index(it["size"]) for it in items]
    texts += EASY_EXAMPLES + HARD_EXAMPLES
    labels += [0] * len(EASY_EXAMPLES) + [2] * len(HARD_EXAMPLES)
    return texts, labels


def load_complexity_model() -> None:
    global _tokenizer, _model, _ref_emb, _ref_label

    if _model is not None:
        return

    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    _model = AutoModel.from_pretrained(MODEL_NAME)
    _model.eval()

    texts, labels = _load_reference()
    _ref_emb = torch.cat([_embed(texts[i : i + 32]) for i in range(0, len(texts), 32)])
    _ref_label = torch.tensor(labels)


def is_ready() -> bool:
    # 모델 객체만 보면 참조 문장 임베딩(_ref_emb) 계산 중에도 준비됨으로 보여서,
    # 그 사이 들어온 auto 요청이 complexity_score()에서 실패했다. 마지막 단계까지 확인한다.
    return _model is not None and _ref_label is not None


def _votes(query_emb: Tensor) -> Tensor:
    """크기별 유사도 가중 득표 비율 (small, medium, large)."""
    sims = (query_emb @ _ref_emb.T).squeeze(0)
    top = sims.topk(min(K_NEIGHBORS, sims.numel()))
    votes = torch.zeros(len(_SIZES))
    votes.index_add_(0, _ref_label[top.indices], top.values.clamp(min=0))
    total = votes.sum()
    return votes / total if total > 0 else torch.tensor([1.0, 0.0, 0.0])


def complexity_score(text: str) -> float:
    """kNN으로 고른 크기를 catalog.py 임계값(0.4 / 0.80) 구간 안의 0~1 점수로 반환."""
    if _model is None or _ref_emb is None or _ref_label is None:
        raise RuntimeError("복잡도 모델이 아직 로드되지 않았습니다.")

    p_small, p_medium, p_large = _votes(_embed([text])).tolist()
    size = max(range(3), key=lambda i: (p_small, p_medium, p_large)[i])

    if size == 0:
        # small 득표가 높을수록 0에 가깝게. p_small >= 1/3이라 0.4 미만이 보장된다.
        return 0.4 * (1.0 - p_small)
    if size == 1:
        # medium 구간 안에서 large 쪽 득표 비중만큼 위로.
        ratio = p_large / (p_small + p_large) if (p_small + p_large) > 0 else 0.5
        return 0.4 + 0.399 * ratio
    return 0.8 + 0.2 * p_large
