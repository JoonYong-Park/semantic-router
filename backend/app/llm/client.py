"""LLM 호출 (OpenAI / Gemini OpenAI-compat / Anthropic) - 비스트리밍 call_model과
툴 루프 한 라운드용 스트리밍 stream_model_with_tools."""

import json
import logging
from typing import AsyncIterator

import httpx

from app.llm.http import API_KEYS, KEY_ENV_NAMES, _raise_for_status_logged
from app.llm.messages import _prepare_anthropic_messages, _prepare_openai_messages
from app.llm.tool_format import build_tools_for_company
from app.routing.catalog import ModelSpec

logger = logging.getLogger("uvicorn.error")


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
            f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다."
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


async def stream_model_with_tools(
    spec: ModelSpec,
    messages: list[dict],
    tool_specs: list | None,
    usage: dict[str, int] | None = None,
    reference_block_count: int | None = None,
    history_len: int | None = None,
    max_output_tokens: int | None = None,
    force_final: bool = False,
) -> AsyncIterator[dict]:
    """중립 포맷 messages로 1회 스트리밍 호출한다(툴 루프의 한 회차).

    yield하는 이벤트:
      {"type": "text", "content": str} - 답변 텍스트 조각(실시간)
      {"type": "tool_call", "id", "name", "arguments": dict|None,
       "raw_arguments": str, "parse_error": bool, "truncated": bool, "provider_extra": dict}
        - 응답이 끝난 뒤, 이번 응답의 툴 호출을 순서대로 전부 yield
          (Anthropic은 stop_reason을 봐야 잘림 여부를 알 수 있어서 끝에 모아 보냄)

    tool_specs: tools/base.py의 ToolSpec 목록. None/빈 목록이면 tools 없이 호출한다.
    force_final: 툴 루프의 마지막 회차 - 툴 호출 없이 최종 답변을 강제한다.
      OpenAI는 tools를 아예 뺀다(실측으로 정상 확인). Anthropic/Google은 tools를
      유지하고 tool_choice를 none으로 보낸다 - tools를 빼면 Gemini가 "툴을 더
      부르고 싶은" 상태에서 빈 답변을 낸 것을 실측했고, Anthropic은 tool 블록이
      있는 요청에서 tools를 빼는 게 안전한지 확인이 안 됨(둘 다 미검증).
    병렬 툴 호출은 요청 파라미터로 끈다. 그래도 여러 개가 오면 전부 yield하고,
    호출부가 모든 호출에 결과를 돌려줘야 한다(결과 없는 호출이 있으면 다음 요청이 400).

    usage: 넘기면 이 회차 호출이 실제로 쓴 토큰을 채워 넣는다(회차별 합산은 호출부).
    reference_block_count / history_len: Anthropic 캐싱 브레이크포인트 위치용
    (_prepare_anthropic_messages 참고). 다른 회사는 무시.
    max_output_tokens: 이 회차의 출력 토큰 상한. None이면 기존 동작 그대로 -
    Anthropic은 max_tokens 필수라 MAX_OUTPUT_TOKENS(2048), OpenAI/Google은
    상한을 안 보냄(지금까지도 안 보냈음). 문서 툴처럼 긴 인자가 필요한
    회차에서 값을 넘겨 올리는 용도.
    """
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다.")

    if force_final and spec.company == "openai":
        tool_specs = None
    tools_payload = build_tools_for_company(spec.company, tool_specs) if tool_specs else None

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        if spec.company == "anthropic":
            # Anthropic Messages API는 messages 배열 안에 role="system"을 허용하지
            # 않고, 최상위 system 파라미터로 따로 받는다.
            system_texts, chat_messages = _prepare_anthropic_messages(
                messages, reference_block_count, history_len
            )

            payload: dict = {
                "model": spec.model_id,
                "max_tokens": max_output_tokens or MAX_OUTPUT_TOKENS,
                "messages": chat_messages,
                "stream": True,
            }
            if tools_payload:
                payload["tools"] = tools_payload
                payload["tool_choice"] = (
                    {"type": "none"}
                    if force_final
                    else {"type": "auto", "disable_parallel_tool_use": True}
                )
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
                    _raise_for_status_logged(resp_for_raise)

                # tool_use 블록의 input은 partial_json 조각으로 쪼개져 오므로
                # content_block_stop에서 블록별로 파싱해 모아두고, stop_reason까지
                # 확인한 뒤(잘림 여부) 응답 끝에서 한꺼번에 yield한다.
                current_block: dict | None = None
                tool_json_buffer = ""
                tool_calls: list[dict] = []
                stop_reason: str | None = None

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[len("data: ") :])
                    event_type = event.get("type")

                    if event_type == "message_start" and usage is not None:
                        input_tokens = event.get("message", {}).get("usage", {}).get(
                            "input_tokens"
                        )
                        if input_tokens is not None:
                            usage["input_tokens"] = input_tokens
                    elif event_type == "content_block_start":
                        current_block = event.get("content_block") or {}
                        tool_json_buffer = ""
                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield {"type": "text", "content": delta["text"]}
                        elif delta.get("type") == "input_json_delta":
                            tool_json_buffer += delta.get("partial_json", "")
                    elif event_type == "content_block_stop":
                        if current_block and current_block.get("type") == "tool_use":
                            tool_calls.append(
                                _make_tool_call(
                                    spec,
                                    call_id=current_block.get("id", ""),
                                    name=current_block.get("name", ""),
                                    raw=tool_json_buffer,
                                )
                            )
                        current_block = None
                    elif event_type == "message_delta":
                        stop_reason = event.get("delta", {}).get("stop_reason") or stop_reason
                        if usage is not None:
                            output_tokens = event.get("usage", {}).get("output_tokens")
                            if output_tokens is not None:
                                usage["output_tokens"] = output_tokens
                    # 그 외 이벤트(ping, message_stop, 새로 생긴 타입 등)는 무시

            if stop_reason == "max_tokens" and tool_calls:
                # 출력 한도에 걸려 마지막 툴 인자가 잘렸을 수 있다 - 우연히 JSON이
                # 닫혀 있어도 내용이 불완전할 수 있으니 실패로 취급한다.
                tool_calls[-1]["parse_error"] = True
                tool_calls[-1]["truncated"] = True
                tool_calls[-1]["arguments"] = None
            for call in tool_calls:
                yield call
            return

        # openai / google - 동일한 Chat Completions SSE 포맷
        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        payload = {
            "model": spec.model_id,
            "messages": _prepare_openai_messages(messages, spec.company),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_output_tokens is not None:
            # OpenAI reasoning 모델은 max_tokens 대신 max_completion_tokens를 받는다.
            key = "max_completion_tokens" if spec.company == "openai" else "max_tokens"
            payload[key] = max_output_tokens
        if tools_payload:
            payload["tools"] = tools_payload
            # force_final일 때 여기까지 오는 건 Google뿐(OpenAI는 위에서 tools를 뺌)
            payload["tool_choice"] = "none" if force_final else "auto"
            if spec.company == "openai":
                payload["parallel_tool_calls"] = False
                # reasoning 모델은 Chat Completions에서 tools를 쓰려면 reasoning_effort를
                # "none"으로 꺼야 한다 (실제로 400 에러를 받아 확인함 - 안 끄면
                # "Function tools with reasoning_effort are not supported ..." 에러).
                payload["reasoning_effort"] = "none"

        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                resp_for_raise = httpx.Response(
                    resp.status_code, content=body, request=resp.request
                )
                _raise_for_status_logged(resp_for_raise)

            # tool_calls도 arguments가 여러 청크로 쪼개져 오므로 호출별로 모은다.
            # OpenAI는 index로 구분해서 보내지만, Gemini(OpenAI 호환)는 index 키
            # 없이 호출마다 id를 붙여 보낸다(실측) - index가 없으면 id로, id도
            # 없으면 등장 순서로 버퍼를 구분해서 병렬 호출 인자가 한 문자열로
            # 이어 붙지 않게 한다.
            buffers: dict[int | str, dict] = {}
            finish_reason: str | None = None

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw_payload = line[len("data: ") :]
                if raw_payload == "[DONE]":
                    break
                chunk = json.loads(raw_payload)

                if usage is not None and chunk.get("usage"):
                    usage["input_tokens"] = chunk["usage"].get("prompt_tokens")
                    usage["output_tokens"] = chunk["usage"].get("completion_tokens")

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})

                content = delta.get("content")
                if content:
                    yield {"type": "text", "content": content}

                for tc in delta.get("tool_calls") or []:
                    key = tc.get("index")
                    if key is None:
                        key = tc.get("id") or f"pos-{len(buffers)}"
                    buf = buffers.setdefault(
                        key, {"id": "", "name": "", "arguments": "", "provider_extra": {}}
                    )
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        buf["name"] = fn["name"]
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]
                    # Gemini 3의 thought_signature - 다음 요청에 그대로 되돌려 보내야 함
                    if tc.get("extra_content"):
                        buf["provider_extra"]["extra_content"] = tc["extra_content"]

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        # OpenAI는 툴을 호출하면 finish_reason이 "tool_calls"로 오지만, Gemini(OpenAI
        # 호환)는 툴을 호출해도 "stop"으로 온다(실측) - 그래서 finish_reason과
        # 상관없이 버퍼에 모인 호출을 전부 내보낸다. "length"(출력 한도)로 끝났으면
        # 마지막 호출 인자가 잘렸을 수 있으니 실패로 표시한다.
        calls = list(buffers.values())
        for i, buf in enumerate(calls):
            call = _make_tool_call(
                spec,
                call_id=buf["id"] or f"call_{i}",
                name=buf["name"],
                raw=buf["arguments"],
                provider_extra=buf["provider_extra"],
            )
            if finish_reason == "length" and i == len(calls) - 1:
                call["parse_error"] = True
                call["truncated"] = True
                call["arguments"] = None
            yield call


def _make_tool_call(
    spec: ModelSpec,
    call_id: str,
    name: str,
    raw: str,
    provider_extra: dict | None = None,
) -> dict:
    """툴 호출 이벤트 하나를 만든다. 인자 JSON 파싱에 실패하면 arguments=None,
    parse_error=True로 표시만 하고(툴 루프가 is_error 결과로 모델에 돌려줌)
    경고 로그를 남긴다."""
    arguments: dict | None
    parse_error = False
    try:
        parsed = json.loads(raw or "{}")
        arguments = parsed if isinstance(parsed, dict) else None
        parse_error = arguments is None
    except json.JSONDecodeError:
        arguments = None
        parse_error = True
    if parse_error:
        logger.warning("툴 인자 JSON 파싱 실패 (model=%s): %s", spec.model_id, (raw or "")[:200])
    return {
        "type": "tool_call",
        "id": call_id,
        "name": name,
        "arguments": arguments,
        "raw_arguments": raw or "",
        "parse_error": parse_error,
        "truncated": False,
        "provider_extra": provider_extra or {},
    }
