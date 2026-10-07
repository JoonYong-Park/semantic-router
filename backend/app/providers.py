"""Provider API 호출 (OpenAI / Gemini OpenAI-compat / Anthropic)."""

import base64
import html
import json
import logging
import os
import re
from typing import AsyncIterator

import httpx

from app.models_config import Company, ModelSpec

logger = logging.getLogger("uvicorn.error")

API_KEYS = {
    "openai": os.environ.get("OPENAI_API_KEY", ""),
    "google": os.environ.get("GEMINI_API_KEY", ""),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
    "tavily": os.environ.get("TAVILY_API_KEY", ""),
    "perplexity": os.environ.get("PERPLEXITY_API_KEY", ""),
    "brave": os.environ.get("BRAVE_API_KEY", ""),
}

KEY_ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "brave": "BRAVE_API_KEY",
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TAVILY_URL = "https://api.tavily.com/search"
PERPLEXITY_URL = "https://api.perplexity.ai/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT = httpx.Timeout(120.0)
# 스트리밍은 청크 사이 대기 시간이 길어질 수 있어 read 타임아웃을 넉넉히 둔다.
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
# 웹 검색은 Tavily → Perplexity → Brave 폴백 체인이라, 한 곳이 응답 없이 멈춰
# 있어도 다음으로 빨리 넘어가도록 짧게 둔다(최악의 경우 3곳 × 15초).
SEARCH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# site_hint별로 검색을 좁힐 신뢰 도메인. 날씨는 일반 키워드 검색에서 태풍/정치
# 뉴스나 확률 계산기 스팸 사이트가 섞여 들어오는 걸 실제로 확인해서 추가함.
SITE_HINT_DOMAINS: dict[str, list[str]] = {
    "weather": ["weather.go.kr", "weather.naver.com"],
}


def build_tools_for_company(company: Company, tool_specs: list) -> list[dict]:
    """tools.py의 ToolSpec 목록을 회사별 tools 스키마로 변환만 한다(순환
    import를 피하려고 스펙은 호출부가 넘김). OpenAI/Google(OpenAI 호환)은
    {"type":"function","function":{...}}, Anthropic은 function 래퍼 없이
    {"name","description","input_schema"}. strict는 스펙에 켜져 있을 때 OpenAI와
    Anthropic에만 싣는다 - Google 호환 엔드포인트의 strict 지원은 미검증이라 안 보냄."""
    if company == "anthropic":
        converted = []
        for t in tool_specs:
            if t.strict:
                # strict는 모든 객체에 additionalProperties:false가 필요해서 그대로 보낸다.
                tool = {"name": t.name, "description": t.description,
                        "input_schema": t.parameters, "strict": True}
            else:
                # 예전 Anthropic 요청과 동일하게 additionalProperties는 빼고 보낸다.
                schema = {k: v for k, v in t.parameters.items() if k != "additionalProperties"}
                tool = {"name": t.name, "description": t.description, "input_schema": schema}
            converted.append(tool)
        return converted

    converted = []
    for t in tool_specs:
        function = {"name": t.name, "description": t.description, "parameters": t.parameters}
        if t.strict and company == "openai":
            function["strict"] = True
        converted.append({"type": "function", "function": function})
    return converted


# --- 첨부파일 멀티모달 content 어댑터 ---
#
# conversations.py가 만드는 내부 표현(provider 중립)은 문자열이거나, 아래
# 블록들의 리스트다:
#   {"type": "text", "text": str}
#   {"type": "image", "media_type": str, "data": str(base64)}
#   {"type": "document", "media_type": str, "data": str(base64)}  (PDF)
# 이 함수들이 실제 요청 직전에 회사별 스키마로 변환한다.


def _to_anthropic_content(content: str | list[dict]) -> str | list[dict]:
    if isinstance(content, str):
        return content
    blocks = []
    for block in content:
        if block["type"] == "text":
            blocks.append({"type": "text", "text": block["text"]})
        elif block["type"] not in ("image", "document"):
            # 이미 Anthropic 포맷인 블록(tool_use/tool_result 등)은 그대로 통과 -
            # source 래퍼를 씌우면 깨진다.
            blocks.append(block)
        else:  # image / document - Anthropic 둘 다 source 래퍼가 동일한 모양
            blocks.append(
                {
                    "type": block["type"],
                    "source": {
                        "type": "base64",
                        "media_type": block["media_type"],
                        "data": block["data"],
                    },
                }
            )
    return blocks


def _to_openai_content(content: str | list[dict]) -> str | list[dict]:
    """OpenAI Chat Completions 포맷 (Google의 OpenAI 호환 엔드포인트도 동일하게 지원).
    PDF는 이미지가 아니라 file 블록(file_data에 data URI)으로 보낸다 - OpenAI
    Chat Completions가 실제로 지원하는 파일 입력 방식."""
    if isinstance(content, str):
        return content
    blocks = []
    for block in content:
        if block["type"] == "text":
            blocks.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block['media_type']};base64,{block['data']}"
                    },
                }
            )
        else:  # document (PDF)
            blocks.append(
                {
                    "type": "file",
                    "file": {
                        "filename": "attachment.pdf",
                        "file_data": f"data:{block['media_type']};base64,{block['data']}",
                    },
                }
            )
    return blocks


def _raise_for_status_logged(resp: httpx.Response) -> None:
    """resp.raise_for_status()는 응답 본문을 버리고 상태 코드만 담은
    메시지를 던져서, 4xx의 실제 원인(예: OpenAI의 구체적인 거부 사유)이
    로그에 안 남는 문제가 있었다. 재발 방지를 위해 실패 시 본문을 먼저
    로그로 남긴 뒤 그대로 raise한다."""
    if resp.status_code >= 400:
        logger.error("API 오류 응답 (%s): %s", resp.status_code, resp.text)
    resp.raise_for_status()


def _apply_anthropic_cache_control(
    anthropic_messages: list[dict], reference_block_count: int | None = None
) -> None:
    """대화 이력 중 "이번 턴"을 제외한 마지막 안정 지점(이전 턴)의 마지막
    content block에 프롬프트 캐싱을 건다. 그 지점까지의 모든 이전 턴(특히
    이미지가 포함된 과거 턴)이 캐시 대상이 되어, 다음 턴부터는 재계산 없이
    캐시를 재사용한다. 새 질문(마지막 메시지)은 매번 바뀌므로 캐시 대상에서 뺀다.

    reference_block_count가 있으면(프로젝트 참고자료가 첫 번째 메시지 맨
    앞에 끼워진 경우) 그 블록들이 끝나는 지점에도 별도 브레이크포인트를
    하나 더 찍는다 - 참고자료 자체가 바뀌지 않는 한 대화 턴 수와 무관하게
    항상 캐시 재사용 대상이 되도록. 두 브레이크포인트는 서로 다른 메시지/
    블록을 가리키므로 충돌 없이 공존한다(Anthropic은 최대 4개까지 지원,
    지금은 2개만 사용)."""
    if reference_block_count and anthropic_messages:
        first_content = anthropic_messages[0]["content"]
        if isinstance(first_content, list) and len(first_content) >= reference_block_count:
            idx = reference_block_count - 1
            first_content[idx] = {**first_content[idx], "cache_control": {"type": "ephemeral"}}

    if len(anthropic_messages) < 2:
        return
    target = anthropic_messages[-2]
    content = target["content"]
    if isinstance(content, str):
        target["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


# --- 중립 메시지 포맷 → 회사별 변환 ---
#
# conversations.py의 툴 루프는 회사와 무관한 아래 포맷으로 메시지를 쌓는다:
#   {"role": "system"|"user", "content": str | list[블록]}
#   {"role": "assistant", "content": str, "tool_calls": [  # tool_calls는 없을 수 있음
#       {"id", "name", "arguments": dict, "provider_extra": dict}]}
#   {"role": "tool", "tool_call_id", "name", "content": str, "is_error": bool}
# 아래 함수들이 요청 직전에 회사별 스키마로 바꾼다.


def _to_anthropic_chat_messages(messages: list[dict]) -> list[dict]:
    """system을 뺀 중립 메시지를 Anthropic messages로 변환. 연속된 tool 결과는
    user 메시지 하나에 tool_result 블록으로 모두 묶는다(호출마다 결과가 하나씩
    있어야 하고, 같은 user 메시지에서 tool_result가 텍스트보다 앞에 와야 함)."""
    converted: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }
            if m.get("is_error"):
                block["is_error"] = True
            prev = converted[-1] if converted else None
            if prev and prev["role"] == "user" and prev.get("_tool_results"):
                prev["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block], "_tool_results": True})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for call in m["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": role, "content": _to_anthropic_content(m["content"])})
    for msg in converted:
        msg.pop("_tool_results", None)
    return converted


def _prepare_anthropic_messages(
    messages: list[dict],
    reference_block_count: int | None = None,
    history_len: int | None = None,
) -> tuple[list[str], list[dict]]:
    """system 텍스트 분리 + content 변환 + 캐싱까지 한 번에 처리.

    history_len: messages 중 앞에서부터 몇 개가 "이번 턴 툴 루프 전의 원래
    히스토리"(이번 사용자 질문까지)인지. 캐시 브레이크포인트(직전 턴 끝)는
    이 구간 안에서만 찍어야 한다 - 루프 메시지까지 포함해 [-2]를 잡으면
    회차마다 위치가 바뀌어 캐시가 깨진다. None이면 전체가 히스토리."""
    if history_len is None:
        history_len = len(messages)
    system_texts = [m["content"] for m in messages if m["role"] == "system"]
    base = _to_anthropic_chat_messages(messages[:history_len])
    _apply_anthropic_cache_control(base, reference_block_count)
    loop_part = _to_anthropic_chat_messages(messages[history_len:])
    return system_texts, base + loop_part


def _prepare_openai_messages(messages: list[dict], company: Company) -> list[dict]:
    """중립 메시지를 OpenAI Chat Completions 포맷으로 변환(Google 호환 엔드포인트
    공용). Google은 툴 호출 응답에 실려 온 extra_content(thought_signature)를
    assistant tool_calls의 해당 호출에 그대로 되돌려 보내야 한다 - 빼면
    "Function call is missing a thought_signature" 400이 남(실측)."""
    converted: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            content = m["content"]
            if m.get("is_error"):
                content = f"[툴 실행 오류] {content}"
            converted.append(
                {"role": "tool", "tool_call_id": m["tool_call_id"], "content": content}
            )
            continue
        if role == "assistant" and m.get("tool_calls"):
            tool_calls = []
            for call in m["tool_calls"]:
                entry = {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                extra = (call.get("provider_extra") or {}).get("extra_content")
                if company == "google" and extra:
                    entry["extra_content"] = extra
                tool_calls.append(entry)
            converted.append(
                {"role": "assistant", "content": m.get("content") or None, "tool_calls": tool_calls}
            )
            continue
        converted.append({"role": role, "content": _to_openai_content(m["content"])})
    return converted


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

    tool_specs: tools.py의 ToolSpec 목록. None/빈 목록이면 tools 없이 호출한다.
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


async def tavily_search(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> list[dict]:
    """Tavily로 웹 검색. 키가 없거나 요청이 실패하면 예외를 그대로 올린다 -
    호출부(tools.py의 web_search)가 그 사실을 모델에 에러 결과로 돌려준다.
    결과가 0건이면 빈 리스트(실패와 구분).

    time_range: "day"/"week"/"month"/"year"로 최신순 필터를 걸 수 있다. 기본
    검색은 최신순 필터가 없어서 "오늘 날씨"처럼 시의성 있는 질문에도 몇 년 전
    글이 섞여 들어오는 걸 확인했다 (날짜 숫자만 우연히 겹치는 옛날 글 등).
    반대로 일반 지식 검색엔 이 필터가 오히려 결과를 씨마르게 하므로, 필요할
    때만(모델이 판단해서) 걸도록 옵션으로 뺐다.

    site_hint: "weather" 등으로 지정하면 SITE_HINT_DOMAINS의 신뢰 도메인으로만
    결과를 좁힌다(include_domains_mode="filter"). 일반 키워드 검색은 날씨
    질문에도 태풍 뉴스·정치 기사·확률 계산기 스팸 사이트가 섞여 들어오는 걸
    확인해서 추가했다.
    """
    api_key = API_KEYS.get("tavily", "")
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY가 설정되지 않았습니다.")

    payload: dict = {"query": query, "max_results": max_results}
    if time_range:
        payload["time_range"] = time_range
    domains = SITE_HINT_DOMAINS.get(site_hint or "")
    if domains:
        payload["include_domains"] = domains
        payload["include_domains_mode"] = "filter"

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        resp = await client.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        _raise_for_status_logged(resp)
        data = resp.json()
        return data.get("results", [])


async def perplexity_search(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> list[dict]:
    """Perplexity Search API로 웹 검색(Tavily 실패 시 2순위). 실패하면 예외를
    그대로 올린다. time_range는 search_recency_filter, site_hint는
    search_domain_filter(허용 도메인 목록)로 넘긴다. 결과는 Tavily와 같은
    {title, url, content} 모양으로 맞춘다(content = snippet)."""
    api_key = API_KEYS.get("perplexity", "")
    if not api_key:
        raise RuntimeError("PERPLEXITY_API_KEY가 설정되지 않았습니다.")

    payload: dict = {"query": query, "max_results": max_results}
    if time_range:
        payload["search_recency_filter"] = time_range
    domains = SITE_HINT_DOMAINS.get(site_hint or "")
    if domains:
        payload["search_domain_filter"] = domains

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        resp = await client.post(
            PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        _raise_for_status_logged(resp)
        return [
            {"title": r.get("title") or "", "url": r.get("url") or "", "content": r.get("snippet") or ""}
            for r in resp.json().get("results", [])
        ]


_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


def _plain_text(value: str) -> str:
    """Brave 결과의 description에는 <strong> 같은 HTML 태그/엔티티가 섞여 있다."""
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


async def brave_search(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> list[dict]:
    """Brave Web Search API로 웹 검색(Perplexity까지 실패했을 때 3순위). 실패하면
    예외를 그대로 올린다. time_range는 freshness(pd/pw/pm/py), site_hint는 도메인
    제한 파라미터가 따로 없어서 질의어에 site: 연산자를 붙여서 건다. 결과는
    {title, url, content} 모양으로 맞추고, content는 description과
    extra_snippets를 이어 붙인 평문이다."""
    api_key = API_KEYS.get("brave", "")
    if not api_key:
        raise RuntimeError("BRAVE_API_KEY가 설정되지 않았습니다.")

    q = query
    domains = SITE_HINT_DOMAINS.get(site_hint or "")
    if domains:
        q = f"{query} ({' OR '.join(f'site:{d}' for d in domains)})"
    params: dict = {"q": q, "count": max_results, "extra_snippets": "true"}
    if time_range in _BRAVE_FRESHNESS:
        params["freshness"] = _BRAVE_FRESHNESS[time_range]

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT) as client:
        resp = await client.get(
            BRAVE_URL,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            params=params,
        )
        _raise_for_status_logged(resp)
        results = (resp.json().get("web") or {}).get("results", [])
    return [
        {
            "title": _plain_text(r.get("title", "")),
            "url": r.get("url") or "",
            "content": " ".join(
                _plain_text(t) for t in [r.get("description", ""), *(r.get("extra_snippets") or [])] if t
            ),
        }
        for r in results
    ]


class SearchUnavailableError(Exception):
    """모든 검색 제공자가 실패했거나 키가 없을 때. 메시지에 제공자별 사유가 있다."""


def _short_error(exc: Exception) -> str:
    """로그/에러 메시지용 한 줄 사유(키나 응답 본문은 넣지 않는다)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "타임아웃"
    return f"{type(exc).__name__}: {str(exc)[:80]}"


async def search_web(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> tuple[list[dict], str]:
    """웹 검색 - Tavily가 에러(HTTP 오류, 타임아웃, 키 없음 등)면 Perplexity,
    그것도 실패하면 Brave 순서로 시도한다. (결과, 실제로 쓴 제공자 이름)을 돌려준다.
    결과 0건은 에러가 아니라 정상 응답으로 보고 폴백하지 않는다. 키가 없는
    제공자는 건너뛰고, 전부 실패하면 SearchUnavailableError를 올린다."""
    failures: list[str] = []
    for name, search in _SEARCH_CHAIN:
        if not API_KEYS.get(name):
            failures.append(f"{name}: 키 없음")
            continue
        try:
            results = await search(query, max_results, time_range, site_hint)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {_short_error(exc)}")
            logger.warning("웹 검색 실패, 다음 제공자로 폴백 (provider=%s): %s", name, _short_error(exc))
            continue
        if failures:
            logger.warning("웹 검색 폴백 성공 (provider=%s, 앞선 실패: %s)", name, "; ".join(failures))
        else:
            logger.info("웹 검색 (provider=%s, results=%d)", name, len(results))
        return results, name
    raise SearchUnavailableError("모든 검색 제공자가 실패했습니다 - " + "; ".join(failures))


# 폴백 순서 - 앞에서부터 시도한다.
_SEARCH_CHAIN = (
    ("tavily", tavily_search),
    ("perplexity", perplexity_search),
    ("brave", brave_search),
)


# --- 이미지 생성 (OpenAI Images API) ---

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
# 고품질 이미지는 최대 2분까지 걸릴 수 있어(공식 가이드) 스트리밍 타임아웃과
# 별개로 넉넉하게 둔다.
IMAGE_TIMEOUT = httpx.Timeout(180.0, connect=10.0)


class ImageRateLimitError(Exception):
    """429 - 속도 제한 또는 요금/쿼터 한도."""


class ImageContentPolicyError(Exception):
    """콘텐츠 정책으로 거부됨. 메시지는 모델이 사용자에게 전달할 사유."""


async def generate_image(model: str, prompt: str, size: str, quality: str) -> bytes:
    """OpenAI Images API로 이미지 1장을 만들고 PNG 바이트를 돌려준다(b64_json).
    포맷 변환(jpg)은 호출부가 Pillow로 한다. 429는 ImageRateLimitError,
    정책 거부는 ImageContentPolicyError, 그 외 실패는 httpx 예외를 그대로 올린다."""
    api_key = API_KEYS.get("openai", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다.")
    async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as client:
        resp = await client.post(
            OPENAI_IMAGES_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "prompt": prompt,
                "size": size,
                "quality": quality,
                "output_format": "png",
                "n": 1,
            },
        )
    if resp.status_code == 429:
        logger.error("이미지 생성 429: %s", resp.text[:500])
        raise ImageRateLimitError(resp.text[:300])
    if resp.status_code == 400:
        try:
            error = resp.json().get("error", {})
        except ValueError:
            error = {}
        code = f"{error.get('code', '')} {error.get('type', '')}".lower()
        message = error.get("message", "")
        if any(k in code or k in message.lower() for k in ("moderation", "safety", "policy", "content_filter")):
            logger.warning("이미지 생성 정책 거부: %s", message[:300])
            raise ImageContentPolicyError(message or "콘텐츠 정책에 따라 거부되었습니다.")
    _raise_for_status_logged(resp)
    return base64.b64decode(resp.json()["data"][0]["b64_json"])
