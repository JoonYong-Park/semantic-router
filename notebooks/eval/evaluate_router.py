"""라우터 평가: 카테고리(mmBERT) 정확도 + 복잡도(크기) 판단 방식 비교.

평가셋 v2(results/eval_set_v2.csv) 기준. 복잡도 비교는 stratified 5-fold 교차검증을
10회 반복한 평균이며, kNN 참조 데이터에는 '학습 fold + 기준 예시 46개'만 넣는다
(평가 fold 문항이 참조로 새지 않도록).

주의: 서비스의 complexity_reference.json에는 이 100문항이 모두 들어 있으므로,
서비스 코드를 그대로 이 100문항에 돌리면 정답을 보고 푸는 셈이 된다. 서비스 성능을
다시 재려면 새 평가 문항이 필요하다.

backend 컨테이너 안에서 실행한다 (HF 모델 캐시 사용, API 키 불필요).

    docker cp notebooks/eval backend:/tmp/eval
    docker exec -w /app -e PYTHONPATH=/app -e HF_HUB_OFFLINE=1 backend \\
        python /tmp/eval/evaluate_router.py /tmp/eval/results/eval_set_v2.csv /tmp/eval/router_eval.md
    docker cp backend:/tmp/eval/router_eval.md notebooks/eval/results/router_eval.md
"""

import csv
import sys

import torch

from app.routing import classifier as K
from app.routing import complexity as C
from app.routing.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES

SIZES = ("small", "medium", "large")
REPEATS = 10
FOLDS = 5

# 설계상 카테고리 → 회사 매핑 (개발계획서 2.3). 데모 키 사정과 무관하게 이 기준으로 잰다.
DESIGN_COMPANY = {
    "computer science": "anthropic", "engineering": "anthropic", "law": "anthropic", "philosophy": "anthropic",
    "biology": "google", "chemistry": "google", "physics": "google", "health": "google",
}


def company(cat: str) -> str:
    return DESIGN_COMPANY.get(cat, "openai")


def metrics(pred: torch.Tensor, gold: torch.Tensor) -> dict:
    large = (gold == 2).sum().item()
    return {
        "3단계 일치율": (pred == gold).float().mean().item() * 100,
        "과잉 상향": ((gold == 0) & (pred >= 1)).sum().item(),
        "large 재현율": ((gold == 2) & (pred == 2)).sum().item() / max(large, 1) * 100,
        "large→small": ((gold == 2) & (pred == 0)).sum().item(),
    }


def stratified_folds(gold: torch.Tensor, seed: int) -> list[list[int]]:
    g = torch.Generator().manual_seed(seed)
    folds: list[list[int]] = [[] for _ in range(FOLDS)]
    for c in range(3):
        idx = (gold == c).nonzero().flatten()
        idx = idx[torch.randperm(len(idx), generator=g)]
        for j, i in enumerate(idx.tolist()):
            folds[j % FOLDS].append(i)
    return folds


def cross_validate(predict, gold: torch.Tensor) -> dict:
    """predict(train_idx, test_idx) -> test 예측. 반복 평균 지표."""
    n = len(gold)
    results = []
    for rep in range(REPEATS):
        pred = torch.zeros(n, dtype=torch.long)
        for fold in stratified_folds(gold, rep):
            te = torch.tensor(fold)
            tr = torch.tensor([i for i in range(n) if i not in set(fold)])
            pred[te] = predict(tr, te)
        results.append(metrics(pred, gold))
    return {k: sum(r[k] for r in results) / len(results) for k in results[0]}


def main(src: str, dst: str) -> None:
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    gold = torch.tensor([SIZES.index(r["tier_v2"]) for r in rows])
    n = len(rows)

    C.load_complexity_model()
    K.load_classifier()
    questions = [r["question"] for r in rows]
    q = torch.cat([C._embed(questions[i : i + 16]) for i in range(0, n, 16)])
    ex = C._embed(EASY_EXAMPLES + HARD_EXAMPLES)
    ex_label = torch.tensor([0] * len(EASY_EXAMPLES) + [2] * len(HARD_EXAMPLES))
    easy, hard = ex[: len(EASY_EXAMPLES)], ex[len(EASY_EXAMPLES) :]

    # --- 기존 방식: easy/hard top-5 평균 유사도 차이 sigmoid ---
    se, sh = q @ easy.T, q @ hard.T
    old_score = torch.sigmoid((sh.topk(5, 1).values.mean(1) - se.topk(5, 1).values.mean(1)) * 25.0)

    def tier(s, a, b):
        return torch.where(s < a, 0, torch.where(s < b, 1, 2))

    def threshold_search(tr, te):
        best = max(
            ((tier(old_score[tr], a / 100, b / 100) == gold[tr]).float().mean().item(), a / 100, b / 100)
            for a in range(10, 90, 2) for b in range(20, 100, 2) if b > a
        )
        return tier(old_score[te], best[1], best[2])

    def knn(k):
        def predict(tr, te):
            ref = torch.cat([q[tr], ex])
            lab = torch.cat([gold[tr], ex_label])
            sims = q[te] @ ref.T
            top = sims.topk(k, 1)
            votes = torch.zeros(len(te), 3)
            for row in range(len(te)):
                votes[row].index_add_(0, lab[top.indices[row]], top.values[row].clamp(min=0))
            return votes.argmax(1)
        return predict

    table = [
        ("전부 small (기준선)", metrics(torch.zeros(n, dtype=torch.long), gold)),
        ("기존: sigmoid 점수 + 임계값 0.4/0.80", metrics(tier(old_score, 0.4, 0.8), gold)),
        ("기존 점수 + 임계값 교차검증 탐색", cross_validate(threshold_search, gold)),
    ] + [(f"kNN k={k} (참조: 학습 fold + 예시 46개)", cross_validate(knn(k), gold)) for k in (5, 7, 11)]

    # --- 카테고리 ---
    labeled = [(r["category_hint"], r["question"]) for r in rows if r["category_hint"]]
    top1 = top3 = comp = 0
    for cat, text in labeled:
        enc = K._tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            probs = K._model(**enc).logits.softmax(-1)[0]
        ids = probs.topk(3).indices.tolist()
        labels = [K._model.config.id2label[i] for i in ids]
        top1 += labels[0] == cat
        top3 += cat in labels
        comp += company(labels[0]) == company(cat)

    # --- judge 자기 일치율 ---
    agree = sum(r["judge_v2_run1"] == r["judge_v2_run2"] for r in rows)
    t = lambda s: 0 if int(s) <= 2 else (1 if int(s) == 3 else 2)  # noqa: E731
    agree_tier = sum(t(r["judge_v2_run1"]) == t(r["judge_v2_run2"]) for r in rows)

    lines = ["# 라우터 평가 결과 (평가셋 v2)", ""]
    lines.append(f"- 문항 {n}개, 크기 분포 small/medium/large = "
                 + " / ".join(str((gold == c).sum().item()) for c in range(3)))
    lines.append(f"- judge 2회 채점 일치: 점수 {agree}/{n}, 3단계 {agree_tier}/{n}")
    lines.append(f"- 교차검증: stratified {FOLDS}-fold × {REPEATS}회 평균")
    lines += ["", "## 복잡도(크기) 판단", "", "| 방식 | 3단계 일치율 | large 재현율 | 과잉 상향 | large→small |", "|---|---|---|---|---|"]
    for name, m in table:
        lines.append(f"| {name} | {m['3단계 일치율']:.1f}% | {m['large 재현율']:.1f}% | {m['과잉 상향']:.1f} | {m['large→small']:.1f} |")
    lines += ["", "## 카테고리 분류 (category_hint가 있는 문항)", ""]
    lines.append(f"- 문항 {len(labeled)}개: 14개 분야 top-1 {top1} ({top1 / len(labeled) * 100:.0f}%), "
                 f"top-3 {top3} ({top3 / len(labeled) * 100:.0f}%), 회사 단위 {comp} ({comp / len(labeled) * 100:.0f}%)")
    open(dst, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
