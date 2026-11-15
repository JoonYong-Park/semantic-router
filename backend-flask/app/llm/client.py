"""LLM 호출 (OpenAI / Gemini OpenAI-compat / Anthropic) - 비스트리밍 call_model과
툴 루프 한 라운드용 스트리밍 stream_model_with_tools."""

import asyncio
import json
import os
import logging
from typing import AsyncIterator

import httpx

from app.llm.http import API_KEYS, KEY_ENV_NAMES, _raise_for_status_logged
from app.llm.messages import (
    _prepare_anthropic_messages,
    _prepare_openai_messages,
    _prepare_openai_responses_input,
)
from app.llm.tool_format import build_tools_for_company
from app.routing.catalog import ModelSpec

logger = logging.getLogger("uvicorn.error")


# OpenAI는 Responses API를 쓴다 - GPT-6 Astra / 6.1 Sol은 Chat Completions에서 툴을
# 쓰려면 reasoning_effort="none"이 필요한데 그 값을 지원하지 않는다(실측 400).
OPENAI_URL = "https://api.openai.com/v1/responses"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# Anthropic은 max_tokens가 필수라 상한이 없을 때 이 값을 보낸다(OpenAI/Gemini는 안 보냄).
# Fable/Opus 5.5는 추론이 항상 켜져 있고 추론 토큰도 이 상한에 포함되므로, 2048이면
# 긴 답이 잘릴 수 있어 16384로 둔다.
MAX_OUTPUT_TOKENS = 16384
REQUEST_TIMEOUT = httpx.Timeout(120.0)
# 스트리밍은 청크 사이 대기 시간이 길어질 수 있어 read 타임아웃을 넉넉히 둔다.
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

# 일시적인 서버 쪽 오류(과부하/게이트웨이)는 짧게 쉬고 다시 시도한다. 실측: Gemini 3.8
# Flash가 "high demand" 503을 자주 낸다. 429는 쿼터 초과(재시도해도 안 됨)와 속도
# 제한이 같은 코드라 구분이 안 돼서 재시도하지 않는다. 스트리밍은 텍스트를 하나도
# 내보내기 전에 실패한 경우에만 재시도한다(이미 화면에 나간 글이 중복되지 않게).
RETRY_STATUS = {500, 502, 503, 504, 529}
RETRY_DELAYS = (1.0, 3.0)  # 최대 2회 재시도


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUS
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError))


def _anthropic_headers(api_key: str) -> dict[str, str]:
    """Anthropic 요청 헤더. 워크스페이스에 묶이지 않은 키는 anthropic-workspace-id
    헤더가 있어야 해서, ANTHROPIC_WORKSPACE_ID가 설정돼 있으면 같이 보낸다."""
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        headers["anthropic-workspace-id"] = workspace_id
    return headers


async def call_model(spec: ModelSpec, message: str) -> str:
    """비스트리밍 1회 호출(툴 없음). 일시적 오류는 RETRY_DELAYS만큼 재시도한다."""
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        try:
            return await _call_model_once(spec, message)
        except Exception as exc:  # noqa: BLE001
            if delay is None or not _is_retryable(exc):
                raise
            logger.warning("모델 호출 재시도 %d회차 (model=%s): %s", attempt + 1, spec.model_id, exc)
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def _call_model_once(spec: ModelSpec, message: str) -> str:
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(
            f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다."
        )

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if spec.company == "anthropic":
            resp = await client.post(
                ANTHROPIC_URL,
                headers=_anthropic_headers(api_key),
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

        if spec.company == "openai":
            resp = await client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": spec.model_id, "input": message, "store": False},
            )
            _raise_for_status_logged(resp)
            data = resp.json()
            # output에는 reasoning 아이템 등이 섞이므로 message의 output_text만 잇는다.
            return "".join(
                part.get("text", "")
                for item in data.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            )

        resp = await client.post(
            GOOGLE_URL,
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
      세 회사 모두 tools는 유지하고 tool_choice를 none으로 보낸다 - tools를 빼면
      Gemini가 "툴을 더 부르고 싶은" 상태에서 빈 답변을 낸 것을 실측했다
      (OpenAI Responses API는 tool_choice none으로 정상 답변하는 것을 실측).
    병렬 툴 호출은 요청 파라미터로 끈다. 그래도 여러 개가 오면 전부 yield하고,
    호출부가 모든 호출에 결과를 돌려줘야 한다(결과 없는 호출이 있으면 다음 요청이 400).

    usage: 넘기면 이 회차 호출이 실제로 쓴 토큰을 채워 넣는다(회차별 합산은 호출부).
    reference_block_count / history_len: Anthropic 캐싱 브레이크포인트 위치용
    (_prepare_anthropic_messages 참고). 다른 회사는 무시.
    max_output_tokens: 이 회차의 출력 토큰 상한. None이면 기존 동작 그대로 -
    Anthropic은 max_tokens 필수라 MAX_OUTPUT_TOKENS(16384), OpenAI/Google은
    상한을 안 보냄. OpenAI는 Responses API의 max_output_tokens(추론 토큰 포함). 문서 툴처럼 긴 인자가 필요한
    회차에서 값을 넘겨 올리는 용도.
    """
    for attempt, delay in enumerate((*RETRY_DELAYS, None)):
        yielded = False
        try:
            async for event in _stream_once(
                spec, messages, tool_specs, usage, reference_block_count,
                history_len, max_output_tokens, force_final,
            ):
                yielded = True
                yield event
            return
        except Exception as exc:  # noqa: BLE001
            if yielded or delay is None or not _is_retryable(exc):
                raise
            logger.warning("모델 호출 재시도 %d회차 (model=%s): %s", attempt + 1, spec.model_id, exc)
            await asyncio.sleep(delay)


async def _stream_once(
    spec: ModelSpec,
    messages: list[dict],
    tool_specs: list | None,
    usage: dict[str, int] | None = None,
    reference_block_count: int | None = None,
    history_len: int | None = None,
    max_output_tokens: int | None = None,
    force_final: bool = False,
) -> AsyncIterator[dict]:
    """stream_model_with_tools의 1회 시도(재시도 없음)."""
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다.")

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
                headers=_anthropic_headers(api_key),
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

        if spec.company == "openai":
            async for event in _stream_openai_responses(
                client, spec, api_key, messages, tools_payload, usage, max_output_tokens, force_final
            ):
                yield event
            return

        # google - OpenAI 호환 Chat Completions SSE 포맷
        url = GOOGLE_URL
        payload = {
            "model": spec.model_id,
            "messages": _prepare_openai_messages(messages, spec.company),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        if tools_payload:
            payload["tools"] = tools_payload
            payload["tool_choice"] = "none" if force_final else "auto"

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


async def _stream_openai_responses(
    client: httpx.AsyncClient,
    spec: ModelSpec,
    api_key: str,
    messages: list[dict],
    tools_payload: list[dict] | None,
    usage: dict[str, int] | None,
    max_output_tokens: int | None,
    force_final: bool,
) -> AsyncIterator[dict]:
    """OpenAI Responses API 스트리밍 1회차. 텍스트는 output_text.delta로 바로
    내보내고, 툴 호출은 output_item.done에서 완성된 function_call을 모아 끝에
    내보낸다(Chat Completions처럼 인자 조각을 직접 이어 붙일 필요 없음).

    store=False라 서버에 대화가 남지 않으므로, 추론 모델의 reasoning 아이템
    (encrypted_content 포함)을 받아 첫 툴 호출의 provider_extra에 실어 두고 다음
    회차 입력에 그대로 되돌려 보낸다(_prepare_openai_responses_input)."""
    payload: dict = {
        "model": spec.model_id,
        "input": _prepare_openai_responses_input(messages),
        "stream": True,
        "store": False,
        "include": ["reasoning.encrypted_content"],
    }
    if max_output_tokens is not None:
        payload["max_output_tokens"] = max_output_tokens
    if tools_payload:
        payload["tools"] = tools_payload
        payload["tool_choice"] = "none" if force_final else "auto"
        payload["parallel_tool_calls"] = False

    calls: list[dict] = []
    reasoning_items: list[dict] = []
    incomplete_reason: str | None = None

    async with client.stream(
        "POST", OPENAI_URL, headers={"Authorization": f"Bearer {api_key}"}, json=payload
    ) as resp:
        if resp.status_code >= 400:
            body = await resp.aread()
            _raise_for_status_logged(httpx.Response(resp.status_code, content=body, request=resp.request))

        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            event_type = event.get("type")

            if event_type == "response.output_text.delta":
                if event.get("delta"):
                    yield {"type": "text", "content": event["delta"]}
            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    calls.append(item)
                elif item.get("type") == "reasoning":
                    # status 같은 출력 전용 필드는 빼고 입력으로 받는 필드만 남긴다.
                    reasoning_items.append(
                        {k: v for k, v in item.items() if k in ("type", "id", "summary", "encrypted_content")}
                    )
            elif event_type in ("response.completed", "response.incomplete"):
                response = event.get("response") or {}
                if usage is not None and response.get("usage"):
                    usage["input_tokens"] = response["usage"].get("input_tokens")
                    usage["output_tokens"] = response["usage"].get("output_tokens")
                if event_type == "response.incomplete":
                    incomplete_reason = (response.get("incomplete_details") or {}).get("reason")
            elif event_type in ("response.failed", "error"):
                detail = (event.get("response") or {}).get("error") or event.get("error") or event
                logger.error("OpenAI 스트림 오류 (model=%s): %s", spec.model_id, str(detail)[:500])
                raise RuntimeError(f"OpenAI 응답 실패: {str(detail)[:200]}")

    for i, item in enumerate(calls):
        call = _make_tool_call(
            spec,
            call_id=item.get("call_id") or f"call_{i}",
            name=item.get("name", ""),
            raw=item.get("arguments", ""),
            provider_extra={"reasoning_items": reasoning_items} if i == 0 and reasoning_items else None,
        )
        # 출력 한도로 잘렸으면 마지막 호출 인자가 불완전할 수 있으니 실패로 표시한다.
        if incomplete_reason == "max_output_tokens" and i == len(calls) - 1:
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
