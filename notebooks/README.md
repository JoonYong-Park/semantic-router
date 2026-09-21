# 실험 노트북

플랫폼(`backend` Docker)과 **분리**해서 HuggingFace 모델·복잡도 분석을 탐색하는 공간입니다.

## 환경 설정

프로젝트 루트에서:

```bash
python3.12 -m venv .venv-notebooks
source .venv-notebooks/bin/activate
pip install -r notebooks/requirements.txt
python -m ipykernel install --user --name semantic-router-notebooks
```

커널 **`semantic-router-notebooks`** 를 선택한 뒤, 각 하위 폴더의 노트북을 실행하세요.

## 폴더 구조

```
notebooks/
├── README.md
├── requirements.txt
├── bert/                              # 분류기 + 라우팅 실험
│   ├── bert_model_tests.ipynb
│   └── results/routing_simulation.csv
└── complexity/                        # 복잡도 점수 정량 평가
    ├── complexity_evaluation.ipynb    # A/B/C 세 조합 통합
    └── results/complexity_eval_results_final.csv
```

---

## 1. `bert/` — 분류·라우팅 실험

### `bert/bert_model_tests.ipynb`

| 섹션 | 내용 |
| --- | --- |
| mmBERT 분류 | 14개 카테고리 + top-k |
| E5 복잡도 | 당시 e5-small + easy/hard 예시 |
| Auto 라우팅 | 카테고리→회사, 복잡도→**small/medium/large** |
| 추가 BERT | 슬롯만 있음 (비어 있음) |

### `bert/results/routing_simulation.csv`

샘플 10개: openai 9 / google 1, small 2 / medium 8 / large 0.

---

## 2. `complexity/` — 복잡도 정량 평가 (통합)

### `complexity/complexity_evaluation.ipynb`

한 노트북에서 **세 조합**을 비교한다.

| 조합 | 모델 | 기준점 | 결과 |
| --- | --- | --- | --- |
| A | E5-small | 원본 40개 | 기존 방식 |
| **B** | **E5-large** | **원본 40개** | **서비스 채택** |
| C | E5-large | 문체분리 40개 | 기각 |

기본 실행은 CSV만 읽어 바로 표를 보여 준다. 재현하려면 노트북에서 `RECOMPUTE=True`.

### `complexity/results/complexity_eval_results_final.csv`

| 조합 | 오분류 (judge≤2→medium+) | large 재현율 | 3단계 일치율 |
| --- | ---: | ---: | ---: |
| A. E5-small + 원본 | 39 | 42.1% | 44.0% |
| **B. E5-large + 원본** | **29** | **57.9%** | **55.0%** |
| C. E5-large + 문체분리 | 53 | 79.0% | 37.0% |

대표: `이진 탐색이 뭐야?` (judge=1) → A medium / **B small** / C small.

---

## 참고

- HF 캐시: `~/.cache/huggingface` (Docker 캐시와 별도)
- 현재 서비스 복잡도 모델: `intfloat/multilingual-e5-large`
- 기준점 파일(`complexity_examples.py`)은 원본 40개 유지
