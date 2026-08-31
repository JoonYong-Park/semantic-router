# Semantic Router

An experiment that routes each question to one of **9 models** (OpenAI / Google / Anthropic × small / medium / large) based on **category** and **complexity**.

In the `feat: add phase-1 chat demo with model auto-routing` stage, classification and routing used [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) + Envoy — picking one of three models from a single domain label. We now need a **complexity score** as well (company × size = 9 models). Semantic Router is built for single-category routing, so that second axis did not fit cleanly. We dropped the Envoy path and select the model inside FastAPI with mmBERT (category) + E5 embeddings (complexity), then call each provider API directly.

```
browser(:3000)  →  frontend (Next.js)
                      ↓
                 backend (FastAPI, :8000)
                      ↓
         auto: category → company, complexity → size
         manual: pick company & size
                      ↓
              OpenAI / Google / Anthropic
```

| Company | Small | Medium | Large |
| --- | --- | --- | --- |
| OpenAI | gpt-5.6-luna | gpt-5.6-terra | gpt-5.6-sol |
| Google | gemini-3.5-flash-lite | gemini-3.6-flash | gemini-3.1-pro-preview |
| Anthropic | claude-haiku-4-5-20251001 | claude-sonnet-5 | claude-opus-5 |

Model IDs and routing maps live in [backend/app/models_config.py](backend/app/models_config.py).

## Run

```bash
cp .env.example .env
# Fill in OPENAI_API_KEY / GEMINI_API_KEY / ANTHROPIC_API_KEY

docker compose up -d --build
```

- Frontend: http://localhost:3000
- Backend: http://localhost:8000/health

On first start, classifier models are downloaded from Hugging Face and cached in the `backend_hf_cache` volume. Manual model selection works before the classifiers finish loading.

```bash
# Status / logs
docker compose ps
docker compose logs -f backend

# Rebuild after code changes
docker compose up -d --build
docker compose up -d --build backend   # backend only

# Stop
docker compose stop          # stop containers (volumes kept)
docker compose down          # remove containers (model cache kept)
docker compose down -v      # also delete volumes (models re-download next run)
```
