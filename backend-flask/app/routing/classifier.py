"""mmBERT MMLU-Pro 14카테고리 분류."""

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL_NAME = "llm-semantic-router/mmbert32k-intent-classifier-merged"
MAX_TOKENS = 512

_tokenizer = None
_model = None


def load_classifier() -> None:
    global _tokenizer, _model

    if _model is not None:
        return

    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    _model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
    _model.eval()


def is_ready() -> bool:
    return _model is not None


def classify(text: str) -> str:
    if _model is None or _tokenizer is None:
        raise RuntimeError("분류기가 아직 로드되지 않았습니다.")

    encoded = _tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=MAX_TOKENS,
    )

    with torch.no_grad():
        logits = _model(**encoded).logits

    predicted_id = logits.argmax(dim=-1).item()
    return _model.config.id2label[predicted_id]
