"""Provider API 호출 (OpenAI / Gemini OpenAI-compat / Anthropic)."""

import json
import os
from typing import AsyncIterator

import httpx

from app.models_config import ModelSpec

API_KEYS = {
    "openai": os.environ.get("OPENAI_API_KEY", ""),
    "google": os.environ.get("GEMINI_API_KEY", ""),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
}

KEY_ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT = httpx.Timeout(120.0)
# 스트리밍은 청크 사이 대기 시간이 길어질 수 있어 read 타임아웃을 넉넉히 둔다.
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)


async def call_model(spec: ModelSpec, message: str) -> str:
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(
            f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다 (.env 확인)."
        )

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if spec.company == "anthropic":
            resp = await client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": spec.model_id,
                    "max_tokens": MAX_OUTPUT_TOKENS,
                    "messages": [{"role": "user", "content": message}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            # thinking 블록 등이 앞에 올 수 있으므로 text 타입만 골라서 잇는다.
            texts = [b["text"] for b in data["content"] if b.get("type") == "text"]
            return "".join(texts)

        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": spec.model_id,
                "messages": [{"role": "user", "content": message}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def stream_model(
    spec: ModelSpec, messages: list[dict[str, str]]
) -> AsyncIterator[str]:
    """대화 이력(messages)을 포함해 호출하고, 텍스트 조각을 스트리밍으로 yield한다.

    /chat(call_model)과 달리 단일 message가 아니라 [{"role","content"}, ...]
    전체 이력을 받는다 - 채팅방 컨텍스트 유지용으로 새로 추가한 함수이며,
    기존 call_model/ /chat 엔드포인트는 그대로 둔다.
    """
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(
            f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다 (.env 확인)."
        )

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        if spec.company == "anthropic":
            # Anthropic Messages API는 messages 배열 안에 role="system"을 허용하지
            # 않고, 최상위 system 파라미터로 따로 받는다. 다른 두 회사는 system
            # role을 messages에 그대로 둬도 되는 OpenAI 호환 방식이라 그쪽은 안 건드림.
            system_texts = [m["content"] for m in messages if m["role"] == "system"]
            chat_messages = [m for m in messages if m["role"] != "system"]

            payload: dict = {
                "model": spec.model_id,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": chat_messages,
                "stream": True,
            }
            if system_texts:
                payload["system"] = "\n\n".join(system_texts)

            async with client.stream(
                "POST",
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    resp_for_raise = httpx.Response(
                        resp.status_code, content=body, request=resp.request
                    )
                    resp_for_raise.raise_for_status()

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[len("data: ") :])
                    if (
                        event.get("type") == "content_block_delta"
                        and event.get("delta", {}).get("type") == "text_delta"
                    ):
                        yield event["delta"]["text"]
            return

        # openai / google 은 동일한 Chat Completions SSE 형식
        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": spec.model_id, "messages": messages, "stream": True},
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                resp_for_raise = httpx.Response(
                    resp.status_code, content=body, request=resp.request
                )
                resp_for_raise.raise_for_status()

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                delta = chunk["choices"][0].get("delta", {})
                content = delta.get("content")
                if content:
                    yield content
