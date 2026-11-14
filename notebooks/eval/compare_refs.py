"""길이 편향 수정안 비교: kNN 참조 데이터 구성별 성능.

- v3 test (실사용형, 서비스 참조에 넣지 않는 검증 전용)에서 측정
- v2는 5-fold × 10회 교차검증 (평가 fold는 참조에서 제외)

    docker exec -w /app -e PYTHONPATH=/app -e HF_HUB_OFFLINE=1 backend \\
        python /tmp/eval/compare_refs.py /tmp/eval/results/eval_set_v2.csv /tmp/eval/results/eval_set_v3.csv /tmp/eval/compare_refs.md
"""

import csv
import sys

import torch

from app.routing import complexity as C
from app.routing.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES

SIZES = ("small", "medium", "large")


def metrics(pred, gold, lengths=None):
    m = {
        "일치율": (pred == gold).float().mean().item() * 100,
        "과잉 상향": int(((gold == 0) & (pred >= 1)).sum()),
        "large 재현율": ((gold == 2) & (pred == 2)).sum().item() / max(int((gold == 2).sum()), 1) * 100,
        "large→small": int(((gold == 2) & (pred == 0)).sum()),
    }
    if lengths is not None:
        long_easy = (lengths >= 30) & (gold == 0)
        m["긴 쉬운 질문 small 비율"] = (pred[long_easy] == 0).float().mean().item() * 100 if long_easy.any() else float("nan")
    return m


def knn_predict(q, ref, lab, k):
    sims = q @ ref.T
    top = sims.topk(min(k, ref.shape[0]), 1)
    votes = torch.zeros(q.shape[0], 3)
    for i in range(q.shape[0]):
        votes[i].index_add_(0, lab[top.indices[i]], top.values[i].clamp(min=0))
    return votes.argmax(1)


def main(v2_path, v3_path, dst):
    C.load_complexity_model()
    embed = lambda texts: torch.cat([C._embed(texts[i : i + 16]) for i in range(0, len(texts), 16)])  # noqa: E731

    v2 = list(csv.DictReader(open(v2_path, encoding="utf-8")))
    v3 = list(csv.DictReader(open(v3_path, encoding="utf-8")))
    aug = [r for r in v3 if r["split"] == "aug"]
    test = [r for r in v3 if r["split"] == "test"]

    q2 = embed([r["question"] for r in v2]); y2 = torch.tensor([SIZES.index(r["tier_v2"]) for r in v2])
    qa = embed([r["question"] for r in aug]); ya = torch.tensor([SIZES.index(r["tier"]) for r in aug])
    qt = embed([r["question"] for r in test]); yt = torch.tensor([SIZES.index(r["tier"]) for r in test])
    lt = torch.tensor([len(r["question"]) for r in test])
    l2 = torch.tensor([len(r["question"]) for r in v2])
    qe = embed(EASY_EXAMPLES + HARD_EXAMPLES)
    ye = torch.tensor([0] * len(EASY_EXAMPLES) + [2] * len(HARD_EXAMPLES))

    variants = {
        "현재: v2 + 예시46": (True, False),
        "v2만": (False, False),
        "v2 + 실사용 보강(aug)": (False, True),
        "v2 + 예시46 + aug": (True, True),
    }

    def refs(base_q, base_y, use_ex, use_aug):
        rq, ry = [base_q], [base_y]
        if use_ex:
            rq.append(qe); ry.append(ye)
        if use_aug:
            rq.append(qa); ry.append(ya)
        return torch.cat(rq), torch.cat(ry)

    lines = ["# 참조 데이터 구성 비교 (길이 편향 수정)", "",
             f"- v3: 생성 {len(v3)}문항 → aug {len(aug)} / test {len(test)} (test는 참조에 넣지 않음)",
             f"- v3 test 크기 분포 small/medium/large = " + " / ".join(str(int((yt == c).sum())) for c in range(3)),
             f"- v3 judge 2회 3단계 일치: {sum(r['judge_run1'] and r['judge_run2'] and (int(r['judge_run1'])<=2)==(int(r['judge_run2'])<=2) for r in v3)}/{len(v3)} (small 여부 기준)",
             ""]
    lines += ["## v3 test (실사용형, held-out)", "",
              "| 참조 구성 | k | 일치율 | 긴 쉬운 질문 → small | large 재현율 | 과잉 상향 | large→small |", "|---|---|---|---|---|---|---|"]
    base = metrics(torch.zeros(len(test), dtype=torch.long), yt, lt)
    lines.append(f"| 전부 small (기준선) | - | {base['일치율']:.1f}% | {base['긴 쉬운 질문 small 비율']:.0f}% | 0% | 0 | {base['large→small']} |")
    for name, (ue, ua) in variants.items():
        for k in (7, 11, 15):
            rq, ry = refs(q2, y2, ue, ua)
            m = metrics(knn_predict(qt, rq, ry, k), yt, lt)
            lines.append(f"| {name} | {k} | {m['일치율']:.1f}% | {m['긴 쉬운 질문 small 비율']:.0f}% | {m['large 재현율']:.1f}% | {m['과잉 상향']} | {m['large→small']} |")

    lines += ["", "## v2 교차검증 (5-fold × 10회, 평가 fold는 참조에서 제외)", "",
              "| 참조 구성 | k | 일치율 | large 재현율 | 과잉 상향 |", "|---|---|---|---|---|"]
    n = len(v2)
    for name, (ue, ua) in variants.items():
        for k in (7, 11, 15):
            res = []
            for rep in range(10):
                g = torch.Generator().manual_seed(rep)
                folds = [[] for _ in range(5)]
                for c in range(3):
                    idx = (y2 == c).nonzero().flatten()
                    idx = idx[torch.randperm(len(idx), generator=g)]
                    for j, i in enumerate(idx.tolist()):
                        folds[j % 5].append(i)
                pred = torch.zeros(n, dtype=torch.long)
                for f in folds:
                    te = torch.tensor(f)
                    tr = torch.tensor([i for i in range(n) if i not in set(f)])
                    rq, ry = refs(q2[tr], y2[tr], ue, ua)
                    pred[te] = knn_predict(q2[te], rq, ry, k)
                res.append(metrics(pred, y2))
            avg = {key: sum(r[key] for r in res) / len(res) for key in res[0]}
            lines.append(f"| {name} | {k} | {avg['일치율']:.1f}% | {avg['large 재현율']:.1f}% | {avg['과잉 상향']:.1f} |")
    open(dst, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main(*sys.argv[1:4])
