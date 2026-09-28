"""complexity_evaluation.ipynb 통합 재생성 후 삭제해도 됨."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

OUT = Path(__file__).parent / "complexity_evaluation.ipynb"


def cid() -> str:
    return uuid.uuid4().hex[:8]


def md(text: str) -> dict:
    lines = text.split("\n")
    return {
        "cell_type": "markdown",
        "id": cid(),
        "metadata": {},
        "source": [l + "\n" for l in lines[:-1]] + ([lines[-1]] if lines else []),
    }


def code(text: str) -> dict:
    lines = text.split("\n")
    return {
        "cell_type": "code",
        "id": cid(),
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": [l + "\n" for l in lines[:-1]] + ([lines[-1]] if lines else []),
    }


cells: list[dict] = []

cells.append(md("""# Complexity 분석 평가 (통합)

복잡도 점수(`backend/app/complexity.py`)가 실제 난이도와 얼마나 맞는지
100문항으로 측정하고, 아래 **세 조합**을 한곳에서 비교한다.

| 조합 | 임베딩 모델 | 기준점 (EASY/HARD 40개) | 결과 |
| --- | --- | --- | --- |
| **A** | E5-small | 원본 (`complexity_examples.py`) | 기존 방식 |
| **B** | E5-large | 원본 (동일) | 모델만 키움 → **서비스 채택** |
| **C** | E5-large | 문체분리 (노트북 전용) | 길이/문체 편향 완화 시도 → 기각 |

> 초기에 노트북만으로 돌렸던 **V2 기준점**(카테고리 재설계)은 오분류가 늘어 폐기했다.
> 이후 B/C는 별도 스크립트로 돌렸고, 정리 과정에서 그 스크립트는 지웠다.
> 이 노트북에 B/C를 통합했고, 숫자는 `results/complexity_eval_results_final.csv`에 있다.

**사전 준비**
- 커널: `semantic-router-notebooks`
- 기본 실행은 CSV만 읽으므로 API 키/대용량 다운로드 불필요
- 재계산(`RECOMPUTE=True`) 시: torch/transformers + HF 모델 로드 (수 분)
"""))

cells.append(md("## 0. 셋업"))

cells.append(code("""from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# notebooks/complexity/ 에서 실행해도 repo root를 찾도록
REPO_ROOT = Path.cwd().resolve()
while REPO_ROOT != REPO_ROOT.parent and not (REPO_ROOT / "backend").is_dir():
    REPO_ROOT = REPO_ROOT.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

RESULTS_DIR = Path.cwd() / "results"
if not RESULTS_DIR.is_dir():
    RESULTS_DIR = Path.cwd() / "complexity" / "results"  # repo root에서 연 경우
FINAL_CSV = RESULTS_DIR / "complexity_eval_results_final.csv"

# True로 바꾸면 E5 모델을 다시 로드해 A/B/C 점수를 재계산한다 (수 분 소요).
RECOMPUTE = False

print(f"REPO_ROOT={REPO_ROOT}")
print(f"FINAL_CSV={FINAL_CSV} exists={FINAL_CSV.exists()}")
print(f"RECOMPUTE={RECOMPUTE}")
"""))

cells.append(md("""## 1. 저장된 결과 로드

평가셋 100문항 + LLM judge(1~5) + 세 조합 점수/티어가 CSV에 있다.
judge는 당시 openai small 모델로 붙인 라벨이며, CSV로 사람이 검수할 수 있다.
"""))

cells.append(code("""df = pd.read_csv(FINAL_CSV)
print(f"행 수: {len(df)}")
print(f"컬럼: {list(df.columns)}")
print()
print("source 분포:")
print(df["source"].value_counts())
print()
print("judge_score 분포:")
print(df["judge_score"].value_counts().sort_index())
df.head(3)
"""))

cells.append(md("""## 2. 문체분리 기준점 정의 (조합 C용, 서비스 미반영)

원본 EASY는 전부 짧은 구어체(5~13자), HARD는 전부 긴 복합지시(~50자)라
모델이 **내용이 아니라 길이/문체**로 나눌 가능성이 있었다.
문체분리는 그 편향을 깨려고 만든 노트북 전용 40개다.

- EASY: 짧은 전문 질문 14 + 길지만 쉬운 일상 3 + 단순 계산 3
- HARD: 긴 복합지시 16 + 짧지만 매우 어려운 질문 4 (`P=NP야?` 등)

서비스의 `complexity_examples.py`는 건드리지 않는다.
"""))

cells.append(code("""EASY_STYLE_SEP: list[str] = [
    # (a) 짧은 전문 — 14 카테고리
    "이진 탐색이 뭐야?",
    "행렬이 뭐야?",
    "중력이 뭐야?",
    "ROI가 뭐야?",
    "퀵소트가 뭐야?",
    "행복이 뭐야?",
    "DNA가 뭐야?",
    "인플레이션이 뭐야?",
    "계약서가 뭐야?",
    "DB가 뭐야?",
    "원자가 뭐야?",
    "경사하강법이 뭐야?",
    "르네상스가 뭐야?",
    "분산이 뭐야?",
    # (b) 길지만 쉬운 일상
    "주말에 날씨가 좋으면 한강 가서 자전거 타려고 하는데 근처에서 간단하게 점심 먹을 만한 곳 있으면 추천해줘",
    "오늘 오후 회의가 있어서 점심은 편의점 도시락으로 때울까 고민 중이야 너는 어떻게 생각해?",
    "친구 생일 선물로 뭐가 좋을지 물어봐서 요즘 사람들이 많이 사는 거 몇 개만 알려줘",
    # (c) 단순 계산
    "1 더하기 1은?",
    "15 곱하기 4는 얼마야?",
    "100에서 37을 빼면?",
]

HARD_STYLE_SEP: list[str] = [
    # (a) 복합 지시문 16
    "이 분산 시스템에서 발생하는 데이터 정합성 문제를 CAP 정리 관점에서 분석하고 해결 방안을 제시해줘",
    "양자역학의 불확정성 원리를 일반 상대성이론과 연결지어 설명하고, 둘 사이의 이론적 긴장 관계를 논해줘",
    "이 회사의 재무제표를 분석해서 향후 3년간 현금흐름을 예측하고, 인수합병 시 적정 기업가치를 산출해줘",
    "다음 알고리즘의 시간복잡도를 증명하고, 더 효율적인 대안 알고리즘을 설계해서 성능을 비교해줘",
    "칸트의 정언명령과 공리주의를 비교하면서, 트롤리 문제에 각 이론을 적용했을 때의 결론 차이를 논증해줘",
    "이 유전자 변이가 단백질 접힘 구조에 미치는 영향을 분자생물학적으로 설명하고 관련 질병 기전을 서술해줘",
    "여러 국가의 통화정책 차이가 환율과 무역수지에 미치는 상호작용을 거시경제 모델로 설명해줘",
    "이 법률 조항의 위헌 여부를 헌법상 비례원칙에 따라 단계별로 검토하고 판례를 근거로 논증해줘",
    "복잡한 마이크로서비스 아키텍처에서 분산 트랜잭션의 일관성을 보장하는 사가 패턴을 설계하고 장단점을 비교해줘",
    "이 화학 반응의 반응 메커니즘을 단계별로 추론하고, 속도결정단계를 실험 데이터로 뒷받침해서 설명해줘",
    "여러 개의 상충하는 제약 조건 하에서 이 최적화 문제를 라그랑주 승수법으로 풀고 해의 존재 조건을 증명해줘",
    "이 역사적 사건이 이후 국제질서 재편에 미친 장기적 영향을 여러 사료를 교차 검증해서 서술해줘",
    "신경망의 그래디언트 소실 문제를 수학적으로 분석하고, 여러 해결 기법의 이론적 근거를 비교해줘",
    "이 임상시험 데이터를 통계적으로 검정하고, 다중비교 문제를 보정한 뒤 인과관계를 신중하게 해석해줘",
    "행동경제학 관점에서 소비자의 비합리적 의사결정 편향을 게임이론 모델에 통합해서 설명해줘",
    "이 데이터베이스의 동시성 제어 기법(2PL, MVCC)을 비교 분석하고, 특정 워크로드에 맞는 최적 전략을 설계해줘",
    # (b) 짧지만 매우 어려운 질문 4
    "P=NP야?",
    "괴델의 불완전성 정리를 증명해줘",
    "리만 가설이 맞아?",
    "의식의 하드 문제를 해결해줘",
]

assert len(EASY_STYLE_SEP) == 20 and len(HARD_STYLE_SEP) == 20
print(
    f"문체분리 기준점: EASY {len(EASY_STYLE_SEP)} / HARD {len(HARD_STYLE_SEP)} | "
    f"EASY 길이 {min(map(len, EASY_STYLE_SEP))}~{max(map(len, EASY_STYLE_SEP))} | "
    f"HARD 길이 {min(map(len, HARD_STYLE_SEP))}~{max(map(len, HARD_STYLE_SEP))}"
)
"""))

cells.append(md("""## 3. (선택) 세 조합 재계산

`RECOMPUTE=False`(기본)면 아래 셀은 CSV 값을 그대로 쓴다.
`True`로 바꾸면:

1. E5-small을 잠깐 로드해 조합 A 재계산
2. E5-large를 로드해 조합 B(원본 기준점) · C(문체분리) 재계산

서비스 파일은 수정하지 않고, 모듈 전역만 메모리에서 바꿔치기한다.
"""))

cells.append(code("""from app import complexity as complexity_module
from app.complexity_examples import EASY_EXAMPLES, HARD_EXAMPLES
from app.models_config import COMPLEXITY_MEDIUM_MAX, COMPLEXITY_SMALL_MAX
import torch


def size_tier(score: float) -> str:
    if score < COMPLEXITY_SMALL_MAX:
        return "small"
    if score < COMPLEXITY_MEDIUM_MAX:
        return "medium"
    return "large"


def score_with_refs(text: str, easy_ref, hard_ref) -> float:
    query = complexity_module._embed([text])
    easy_sim = (query @ easy_ref.T).squeeze(0)
    hard_sim = (query @ hard_ref.T).squeeze(0)
    diff = hard_sim.mean().item() - easy_sim.mean().item()
    return torch.sigmoid(torch.tensor(diff * complexity_module.DIFF_SCALE)).item()


def reset_model(model_name: str) -> None:
    \"\"\"complexity 모듈에 다른 HF 모델을 강제로 다시 로드.\"\"\"
    complexity_module._tokenizer = None
    complexity_module._model = None
    complexity_module._easy_ref = None
    complexity_module._hard_ref = None
    complexity_module.MODEL_NAME = model_name
    complexity_module.load_complexity_model()


if RECOMPUTE:
    questions = df["question"].tolist()

    print("=== A: E5-small + 원본 기준점 ===")
    reset_model("intfloat/multilingual-e5-small")
    # load_complexity_model()이 EASY_EXAMPLES/HARD_EXAMPLES로 ref를 만든다
    assert len(EASY_EXAMPLES) == 20 and len(HARD_EXAMPLES) == 20
    easy_a, hard_a = complexity_module._easy_ref, complexity_module._hard_ref
    scores_a = [score_with_refs(q, easy_a, hard_a) for q in questions]

    print("=== B/C: E5-large ===")
    reset_model("intfloat/multilingual-e5-large")
    easy_b, hard_b = complexity_module._easy_ref, complexity_module._hard_ref
    scores_b = [score_with_refs(q, easy_b, hard_b) for q in questions]

    easy_c = complexity_module._embed(EASY_STYLE_SEP)
    hard_c = complexity_module._embed(HARD_STYLE_SEP)
    scores_c = [score_with_refs(q, easy_c, hard_c) for q in questions]

    df = df.copy()
    df["score_small_orig"] = scores_a
    df["tier_small_orig"] = [size_tier(s) for s in scores_a]
    df["score_large_orig"] = scores_b
    df["tier_large_orig"] = [size_tier(s) for s in scores_b]
    df["score_large_sep"] = scores_c
    df["tier_large_sep"] = [size_tier(s) for s in scores_c]
    # 호환 컬럼
    df["score_original"] = df["score_small_orig"]
    df["tier_original"] = df["tier_small_orig"]
    df["score_e5large"] = df["score_large_orig"]
    df["tier_e5large"] = df["tier_large_orig"]

    out = RESULTS_DIR / "complexity_eval_results_final.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"재계산 저장: {out}")
else:
    print("RECOMPUTE=False — CSV 저장값을 그대로 사용")
"""))

cells.append(md("""## 4. 세 조합 비교표

지표 정의:

- **오분류**: judge≤2(쉬움)인데 시스템이 medium/large로 올린 건수
- **large 재현율**: judge≥4인 질문 중 시스템이 large로 맞춘 비율
- **3단계 일치율**: judge를 small(≤2)/medium(3)/large(≥4)로 나눈 뒤 시스템 tier와 일치한 비율
"""))

cells.append(code("""COMBOS = [
    ("A. E5-small + 원본", "score_small_orig", "tier_small_orig"),
    ("B. E5-large + 원본", "score_large_orig", "tier_large_orig"),
    ("C. E5-large + 문체분리", "score_large_sep", "tier_large_sep"),
]

# 구버전 CSV 호환
if "score_small_orig" not in df.columns and "score_original" in df.columns:
    df["score_small_orig"] = df["score_original"]
    df["tier_small_orig"] = df["tier_original"]
if "score_large_orig" not in df.columns and "score_e5large" in df.columns:
    df["score_large_orig"] = df["score_e5large"]
    df["tier_large_orig"] = df["tier_e5large"]


def judge_tier(score: float) -> str:
    s = int(score)
    if s <= 2:
        return "small"
    if s == 3:
        return "medium"
    return "large"


def metrics(tier_col: str) -> dict:
    jt = df["judge_score"].map(judge_tier)
    st = df[tier_col]
    mis = int(((jt == "small") & st.isin(["medium", "large"])).sum())
    large_mask = jt == "large"
    large_recall = float(((st == "large") & large_mask).sum() / large_mask.sum())
    agree = float((jt == st).mean())
    return {
        "오분류(judge≤2→medium+)": mis,
        "large 재현율": round(large_recall, 4),
        "3단계 일치율": round(agree, 4),
    }


summary_rows = []
for label, _score_col, tier_col in COMBOS:
    m = metrics(tier_col)
    summary_rows.append({"조합": label, **m})

summary = pd.DataFrame(summary_rows)
summary
"""))

cells.append(code("""print("=== Confusion: judge_tier vs A(small+원본) ===")
display(pd.crosstab(df["judge_score"].map(judge_tier), df["tier_small_orig"]))
print("\\n=== Confusion: judge_tier vs B(large+원본) ===")
display(pd.crosstab(df["judge_score"].map(judge_tier), df["tier_large_orig"]))
print("\\n=== Confusion: judge_tier vs C(large+문체분리) ===")
display(pd.crosstab(df["judge_score"].map(judge_tier), df["tier_large_sep"]))
"""))

cells.append(md("""## 5. 대표 케이스

기존에 문제가 됐던 쉬운 질문과, 짧지만 어려운 trap 질문을 세 조합으로 나란히 본다.
"""))

cells.append(code("""FOCUS = [
    "이진 탐색이 뭐야?",
    "물의 화학식이 뭐야?",
    "중력이 뭐야?",
    "인플레이션이 뭐야?",
    "행복이 뭐야?",
    "P=NP야?",
    "괴델의 불완전성 정리가 뭐야?",
]
# CSV에 실제로 있는 문자열과 일치해야 한다 (아래 셀 실행 시 0행이면 철자 확인).

cols = [
    "question", "judge_score",
    "score_small_orig", "tier_small_orig",
    "score_large_orig", "tier_large_orig",
    "score_large_sep", "tier_large_sep",
]

focus_df = df[df["question"].isin(FOCUS)][cols].copy()
focus_df["_ord"] = focus_df["question"].map({q: i for i, q in enumerate(FOCUS)})
focus_df = focus_df.sort_values("_ord").drop(columns="_ord")
focus_df
"""))

cells.append(code("""print("=== trap(짧지만 고난도) 전체 ===")
trap_cols = [
    "question", "judge_score",
    "tier_small_orig", "tier_large_orig", "tier_large_sep",
    "score_small_orig", "score_large_orig", "score_large_sep",
]
df[df["source"] == "trap"][trap_cols].sort_values("judge_score", ascending=False)
"""))

cells.append(md("""## 6. 결론

| 조합 | 판단 |
| --- | --- |
| A. E5-small + 원본 | 쉬운 전문 질문(`이진 탐색이 뭐야?` 등)을 medium으로 올리는 오분류가 많음 |
| **B. E5-large + 원본** | 오분류↓·일치율↑ → **서비스 `complexity.py`에 반영** |
| C. E5-large + 문체분리 | large 재현율은 오르지만 쉬운 질문 오분류가 더 늘어 **기각**. 원본 40개 유지 |

임계값(`COMPLEXITY_SMALL_MAX=0.3`, `COMPLEXITY_MEDIUM_MAX=0.7`)은 이번 실험에서 바꾸지 않았다.
"""))

cells.append(code("""print(summary.to_string(index=False))
print()
print("채택: B (E5-large + 원본 기준점)")
print("미채택: C (문체분리) — complexity_examples.py 원본 유지")
"""))

nb = {
    "nbformat": 4,
    "nbformat_minor": 5,
    "metadata": {
        "kernelspec": {
            "display_name": "semantic-router-notebooks",
            "language": "python",
            "name": "semantic-router-notebooks",
        },
        "language_info": {"name": "python", "pygments_lexer": "ipython3"},
    },
    "cells": cells,
}

OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n")
print(f"wrote {OUT} ({len(cells)} cells)")
