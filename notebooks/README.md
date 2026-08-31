# BERT / 분류기 실험 노트북

플랫폼(`backend` Docker)과 **분리**해서 HuggingFace 모델을 탐색하는 공간입니다.

## 환경 설정

프로젝트 루트에서:

```bash
python3.12 -m venv .venv-notebooks
source .venv-notebooks/bin/activate
pip install -r notebooks/requirements.txt
python -m ipykernel install --user --name semantic-router-notebooks
```

Cursor / VS Code / Jupyter에서 커널 **`semantic-router-notebooks`** 를 선택한 뒤 노트북을 실행하세요.

## 노트북

| 파일 | 내용 |
| --- | --- |
| `bert_model_tests.ipynb` | 플랫폼 mmBERT 카테고리 분류 + E5 복잡도 + 라우팅 시뮬레이션 |

## 참고

- 최초 실행 시 HuggingFace에서 모델을 받습니다 (수 분 소요 가능).
- 캐시는 로컬 `~/.cache/huggingface`에 저장됩니다 (Docker `backend_hf_cache`와 별도).
- 실험 결과 CSV는 `notebooks/outputs/`에 저장할 수 있습니다 (gitignore 대상).
