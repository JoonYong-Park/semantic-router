# 라우터 평가 (평가셋 v2)

Auto 모드 라우터의 두 판단을 100문항 평가셋으로 측정한다.

- **카테고리 → 회사**: mmBERT (`llm-semantic-router/mmbert32k-intent-classifier-merged`)
- **복잡도 → 크기**: multilingual-e5-large 임베딩

결과: [results/router_eval.md](results/router_eval.md)

## 1. 평가셋

`../complexity/results/complexity_eval_results_final.csv`의 100문항을 그대로 쓴다. 실패 사례를 찾을 수 있도록 다섯 출처로 나눠 구성했다.

| 출처 | 문항 | 의도 |
|---|---|---|
| curated | 42 | 14개 분야를 고르게 담은 기본 문항 |
| boundary | 20 | small과 medium 경계가 애매한 업무 요청 |
| real_usage | 14 | 직접 써 보며 나온 실제 질문 |
| extra | 14 | 부족한 유형 보강 |
| trap | 10 | 짧지만 어려운 질문 (길이 편향 점검) |

## 2. 라벨 v1 → v2

v1 라벨(소형 모델 judge, 기준표 없음)을 오류 분석해 보니, **정보가 부족한 요청**("이 코드 왜 안 돼?")과 **추론이 어려운 질문**이 같은 높은 점수를 받고 있었다. 그래서 기준표를 만들어 다시 채점했다.

- **기준표** (`rejudge_v2.py` 안에 있음)
  - 추론 깊이만 1~5점으로 평가한다. 주제의 전문성과 질문 길이는 기준이 아니다.
  - 정보가 부족한 요청은 `needs_context=true`로 따로 표시한다. 점수는 정보가 평범하게 주어졌다고 가정한 작업 난이도로 매긴다.
- **judge**: GPT-6.1 Sol로 **2회 독립 채점**했다. 두 번의 채점은 점수가 95/100, 3단계가 98/100 일치했다.
- **크기 라벨**: 1회차 점수를 쓴다. 1~2점은 small, 3점은 medium, 4~5점은 large다.
- **파일**
  - `results/eval_set_v2.csv`: v1과 v2 점수, needs_context, 판단 근거
  - `results/rejudge_raw.json`: judge 원본 응답

## 3. 결과 요약 (stratified 5-fold × 10회 교차검증)

| 방식 | 3단계 일치율 | large 재현율 | 과잉 상향 |
|---|---|---|---|
| 전부 small (기준선) | 69.0% | 0% | 0 |
| 기존 서비스 (sigmoid 점수, 0.4/0.80) | 71.0% | 16.7% | 13 |
| **kNN k=11 (현재 서비스)** | **80.5%** | **77.5%** | **1.2** |

- 기존 방식은 대형 경계가 지나치게 보수적이었다. 어려운 질문 대부분을 medium으로 보냈다.
- kNN은 라벨된 참조 문장들과의 유사도로 투표하므로 medium 라벨도 활용할 수 있다.
- 카테고리는 70문항 기준으로 회사 단위 77%, 14개 분야 top-3 81%다.

**한계**
- 짧지만 어려운 질문(trap)은 임베딩 유사도로 잡기 어렵다.
- judge가 LLM이라 라벨에 잡음이 있을 수 있다.

## 4. 길이 편향 발견과 평가셋 v3

kNN을 적용한 뒤 실제 업무 요청으로 시연해 보니, "예시 엑셀 만들어줘"처럼 **길지만 쉬운 요청 대부분이 large**로 갔다.

**원인 분석**
- 참조 데이터의 평균 길이가 small 11.5자, large 47자였다. 어려움 예시 22개는 전부 50자 이상이다.
- v2 평가셋에서도 30자 이상 문항 15개 중 small이 0개였다.
- 그래서 kNN이 난이도 대신 **질문 길이**를 배웠다. 평가셋에도 같은 치우침이 있어서 교차검증으로는 드러나지 않았다.

**평가셋 v3** (`build_v3.py`, 결과 `results/eval_set_v3.csv`)
- 6개 칸(짧음/김 × 쉬움/보통/어려움)에서 업무 요청 144개를 생성했다.
- 라벨은 같은 기준표로 judge가 2회 채점한 점수다. 생성할 때 붙인 칸 이름은 라벨로 쓰지 않는다.
- 무작위로 나눠 **aug 80개**는 참조 보강용, **test 64개**는 검증 전용으로 쓴다. test는 서비스 참조에 넣지 않는다.

**참조 구성 비교** (`compare_refs.py`, 결과 `results/compare_refs.md`)

| 참조 구성 (k=11) | v3 test 일치율 | 긴 쉬운 요청 → small | v3 과잉 상향 | v3 large 재현율 | v2 CV 일치율 | v2 CV large 재현율 |
|---|---|---|---|---|---|---|
| 전부 small (기준선) | 39.1% | 100% | 0 | 0% | 69.0% | 0% |
| v2 + 예시46 (이전) | 50.0% | 17% | 12 | 100% | 80.5% | 77.5% |
| v2 + aug | 60.9% | 92% | 1 | 83.3% | 80.9% | 50.8% |
| **v2 + 예시46 + aug (적용)** | **59.4%** | **83%** | **3** | **88.9%** | **80.8%** | **79.2%** |

`v2 + aug`가 v3에서는 조금 더 좋지만, v2의 large 재현율이 51%로 떨어진다. 그래서 두 평가셋에서 모두 균형이 맞는 `v2 + 예시46 + aug`를 적용했다.

**한계**
- v3 일치율이 59%다. 실사용형 요청에서는 medium 판별이 여전히 어렵다.
- 짧지만 어려운 질문은 계속 small로 간다.

## 5. 서비스 반영과 주의

`backend/app/routing/complexity_reference.json`에는 v2 100문항(`eval_v2`)과 v3 aug 80문항(`v3_aug`)이 들어 있다. 서비스는 여기에 `complexity_examples.py` 예시 46개를 더해 kNN 참조로 쓴다.

따라서 **서비스 코드를 v2나 v3 aug에 그대로 돌린 점수는 성능이 아니다.** 성능은 v2 교차검증과 v3 test로만 잰다. 다음 개선은 v3 test를 건드리지 않고 새 문항(v4)으로 확인한다.

## 재현

```bash
# 1) 라벨링 (OPENAI_API_KEY 필요, 약 200회 호출)
docker cp notebooks/eval backend:/tmp/eval
docker cp notebooks/complexity/results/complexity_eval_results_final.csv backend:/tmp/eval/eval_v1.csv
docker exec -w /app -e PYTHONPATH=/app backend python /tmp/eval/rejudge_v2.py /tmp/eval/eval_v1.csv /tmp/eval/rejudge_raw.json

# 2) 평가 (API 키 불필요)
docker exec -w /app -e PYTHONPATH=/app -e HF_HUB_OFFLINE=1 backend \
  python /tmp/eval/evaluate_router.py /tmp/eval/results/eval_set_v2.csv /tmp/eval/router_eval.md
docker cp backend:/tmp/eval/router_eval.md notebooks/eval/results/router_eval.md

# 3) 평가셋 v3 생성·채점 (OPENAI_API_KEY 필요, 약 300회 호출)
docker exec -w /app -e PYTHONPATH=/app backend python /tmp/eval/build_v3.py /tmp/eval/results/eval_set_v3.csv

# 4) 참조 구성 비교 (API 키 불필요)
docker exec -w /app -e PYTHONPATH=/app -e HF_HUB_OFFLINE=1 backend \
  python /tmp/eval/compare_refs.py /tmp/eval/results/eval_set_v2.csv /tmp/eval/results/eval_set_v3.csv /tmp/eval/compare_refs.md
```

`eval_set_v2.csv`는 `rejudge_raw.json`과 v1 CSV를 합쳐 만든다. 열은 question, source, category_hint, judge_v1, judge_v2_run1, judge_v2_run2, needs_context, tier_v1, tier_v2, reason_v2다.
