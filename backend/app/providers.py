"""수동 모드(GPT / Gemini / Claude)에서 라우터를 거치지 않고 각 provider API를 직접 호출."""

import os

import httpx
import yaml

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# 실제 호출할 모델 ID는 여기서 따로 관리하지 않고 router가 쓰는
# config/config.yaml(providers.models[].provider_model_id)에서 그대로 읽는다.
# (예전엔 .env에도 OPENAI_MODEL 등을 따로 뒀었는데, config.yaml과 두 곳을 손으로
# 맞춰야 해서 모델 바꿀 때마다 한쪽만 고치는 실수가 났음 - config.yaml 하나만 출처로 통일함)
CONFIG_PATH = os.environ.get("ROUTER_CONFIG_PATH", "/app/config/config.yaml")

_FALLBACK_MODEL_IDS = {
    "gpt-4": "gpt-4",
    "gemini-2.5-pro": "gemini-3.6-flash",
    "claude-opus": "claude-opus-5",
}


def _load_provider_model_ids() -> dict[str, str]:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return {m["name"]: m["provider_model_id"] for m in cfg["providers"]["models"]}
    except (OSError, KeyError, TypeError):
        # config.yaml이 마운트 안 된 상태로 실행되는 경우를 위한 안전망
        return _FALLBACK_MODEL_IDS


_MODEL_IDS = _load_provider_model_ids()
OPENAI_MODEL = _MODEL_IDS.get("gpt-4", _FALLBACK_MODEL_IDS["gpt-4"])
GEMINI_MODEL = _MODEL_IDS.get("gemini-2.5-pro", _FALLBACK_MODEL_IDS["gemini-2.5-pro"])
ANTHROPIC_MODEL = _MODEL_IDS.get("claude-opus", _FALLBACK_MODEL_IDS["claude-opus"])

REQUEST_TIMEOUT = httpx.Timeout(60.0)


async def call_openai(message: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다 (.env 확인).")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": OPENAI_MODEL,
                "messages": [{"role": "user", "content": message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def call_gemini(message: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다 (.env 확인).")

    # Gemini의 OpenAI 호환 엔드포인트를 사용 (Chat Completions 포맷)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            headers={"Authorization": f"Bearer {GEMINI_API_KEY}"},
            json={
                "model": GEMINI_MODEL,
                "messages": [{"role": "user", "content": message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def call_claude(message: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다 (.env 확인).")

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
