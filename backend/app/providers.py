"""Provider API 호출 (OpenAI / Gemini OpenAI-compat / Anthropic)."""

import json
import logging
import os
from typing import AsyncIterator

import httpx

from app.models_config import Company, ModelSpec

logger = logging.getLogger("uvicorn.error")

API_KEYS = {
    "openai": os.environ.get("OPENAI_API_KEY", ""),
    "google": os.environ.get("GEMINI_API_KEY", ""),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
    "tavily": os.environ.get("TAVILY_API_KEY", ""),
}

KEY_ENV_NAMES = {
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "tavily": "TAVILY_API_KEY",
}

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GOOGLE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TAVILY_URL = "https://api.tavily.com/search"

MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT = httpx.Timeout(120.0)
# 스트리밍은 청크 사이 대기 시간이 길어질 수 있어 read 타임아웃을 넉넉히 둔다.
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=10.0)

WEB_SEARCH_TOOL_NAME = "web_search"
WEB_SEARCH_DESCRIPTION = "최신 정보, 실시간 정보, 특정 사실 확인이 필요할 때 웹을 검색한다."

# Tavily는 기본적으로 최신순 필터 없이 검색해서, "오늘 날씨"처럼 시의성 있는
# 질문에도 몇 년 전 글이 섞여 들어온다 (실제로 "2026년 9월 17일" 같은 날짜를
# 검색어에 넣었더니 날짜 숫자만 겹치는 옛날 글/무관한 글이 섞이는 걸 확인함).
# time_range로 무조건 최근으로 제한하면 이번엔 일반 지식 검색(예: "파이썬 문법")
# 결과가 씨가 마르므로, 검색이 필요한지와 마찬가지로 시간 범위도 모델이 질문
# 성격을 보고 직접 판단해서 선택하게 한다.
_WEB_SEARCH_PROPERTIES = {
    "query": {"type": "string", "description": "검색할 질의어 (간결한 핵심 키워드로 작성하고, 날짜를 문자열로 그대로 넣지 말 것)"},
    "time_range": {
        "type": "string",
        "enum": ["day", "week", "month", "year", "none"],
        "description": (
            "결과를 최신으로 좁힐 범위. 오늘/실시간/최근 이슈처럼 시의성이 "
            "중요하면 day나 week, 그렇지 않은 일반 지식/사실 확인이면 none."
        ),
    },
    "site_hint": {
        "type": "string",
        "enum": ["weather", "none"],
        "description": (
            "실시간 날씨/기온/강수확률/미세먼지 질문이면 'weather'로 지정한다 - "
            "일반 뉴스/블로그가 아니라 신뢰할 수 있는 기상 사이트로 검색을 좁힌다. "
            "그 외 질문은 'none'."
        ),
    },
}

# site_hint별로 검색을 좁힐 신뢰 도메인. 날씨는 일반 키워드 검색에서 태풍/정치
# 뉴스나 확률 계산기 스팸 사이트가 섞여 들어오는 걸 실제로 확인해서 추가함.
SITE_HINT_DOMAINS: dict[str, list[str]] = {
    "weather": ["weather.go.kr", "weather.naver.com"],
}


def build_tools_for_company(company: Company) -> list[dict]:
    """회사별 tools 스키마로 변환. OpenAI/Google(OpenAI 호환)은 완전히 동일한
    {"type":"function","function":{...}} 형태를 쓰고(Google의 OpenAI 호환
    엔드포인트가 이 포맷을 그대로 지원함을 공식 문서로 확인함), Anthropic만
    {"name","description","input_schema"}로 구조가 다르다(function 래퍼 없음)."""
    if company == "anthropic":
        return [
            {
                "name": WEB_SEARCH_TOOL_NAME,
                "description": WEB_SEARCH_DESCRIPTION,
                "input_schema": {
                    "type": "object",
                    "properties": _WEB_SEARCH_PROPERTIES,
                    "required": ["query"],
                },
            }
        ]

    return [
        {
            "type": "function",
            "function": {
                "name": WEB_SEARCH_TOOL_NAME,
                "description": WEB_SEARCH_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": _WEB_SEARCH_PROPERTIES,
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
        }
    ]


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


def _prepare_anthropic_messages(
    messages: list[dict], reference_block_count: int | None = None
) -> tuple[list[str], list[dict]]:
    """system 텍스트 분리 + content 변환 + 캐싱까지 한 번에 처리."""
    system_texts = [m["content"] for m in messages if m["role"] == "system"]
    chat_messages = [
        {"role": m["role"], "content": _to_anthropic_content(m["content"])}
        for m in messages
        if m["role"] != "system"
    ]
    _apply_anthropic_cache_control(chat_messages, reference_block_count)
    return system_texts, chat_messages


def _prepare_openai_messages(messages: list[dict]) -> list[dict]:
    return [
        {"role": m["role"], "content": _to_openai_content(m["content"])}
        for m in messages
    ]


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
    messages: list[dict[str, str]],
    usage: dict[str, int] | None = None,
    reference_block_count: int | None = None,
) -> AsyncIterator[dict]:
    """웹 검색 tool을 실어서 1차 호출 - 라우팅된 spec 모델 자신이 검색이
    필요한지 판단한다(ChatGPT/Claude가 실제로 쓰는 방식과 동일, 판단 전용
    별도 모델은 안 씀). 예전엔 이 1차 호출이 비스트리밍이라 "검색 없이 바로
    답하는" 가장 흔한 경우에도 완성된 텍스트를 한 덩어리로만 보여줬는데,
    여기서는 tools를 실어도 stream=True로 받아서 그 경우에도 실제로
    토큰 단위 스트리밍이 되게 한다.

    yield하는 이벤트:
      {"type": "text", "content": str}      - 직접 답변 텍스트 조각(실시간)
      {"type": "tool_call", "query": str, "time_range": str|None, "site_hint": str|None}
        - 검색이 필요하다고 판단한 경우(텍스트 조각 없이 한 번만 옴)

    usage: stream_model()과 동일한 관례 - 넘기면 이 1차 호출 자체가 쓴
    토큰을 이 dict에 채워 넣는다. 검색 경로면 호출자가 2차 stream_model()
    호출의 토큰과 합산해야 이번 턴의 총 사용량이 된다.

    reference_block_count: 프로젝트 참고자료가 붙은 대화면, 첫 번째 메시지
    맨 앞 몇 개 블록이 참고자료인지(Anthropic 캐싱 2번째 브레이크포인트용).
    프로젝트 없는 일반 대화는 None - 기존 동작 그대로.
    """
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다.")

    tools = build_tools_for_company(spec.company)

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        if spec.company == "anthropic":
            system_texts, chat_messages = _prepare_anthropic_messages(
                messages, reference_block_count
            )

            payload: dict = {
                "model": spec.model_id,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": chat_messages,
                "tools": tools,
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
                    _raise_for_status_logged(resp_for_raise)

                # tool_use 블록의 input은 partial_json 조각으로 쪼개져 오므로
                # content_block_stop에서 한 번에 파싱할 수 있게 모아둔다.
                current_block_type: str | None = None
                tool_json_buffer = ""

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
                        current_block_type = event.get("content_block", {}).get("type")
                        tool_json_buffer = ""
                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield {"type": "text", "content": delta["text"]}
                        elif delta.get("type") == "input_json_delta":
                            tool_json_buffer += delta.get("partial_json", "")
                    elif event_type == "content_block_stop":
                        if current_block_type == "tool_use":
                            try:
                                args = json.loads(tool_json_buffer or "{}")
                            except json.JSONDecodeError:
                                logger.warning(
                                    "툴 인자 JSON 파싱 실패 (model=%s): %s",
                                    spec.model_id,
                                    tool_json_buffer[:200],
                                )
                                args = {}
                            query = args.get("query")
                            if query:
                                yield {
                                    "type": "tool_call",
                                    "query": query,
                                    "time_range": _normalize_time_range(
                                        args.get("time_range")
                                    ),
                                    "site_hint": _normalize_site_hint(
                                        args.get("site_hint")
                                    ),
                                }
                        current_block_type = None
                    elif event_type == "message_delta" and usage is not None:
                        output_tokens = event.get("usage", {}).get("output_tokens")
                        if output_tokens is not None:
                            usage["output_tokens"] = output_tokens
            return

        # openai / google - 완전히 동일한 tools/tool_calls + SSE 포맷
        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        payload: dict = {
            "model": spec.model_id,
            "messages": _prepare_openai_messages(messages),
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if spec.company == "openai":
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
            tool_call_buffers: dict[int | str, dict[str, str]] = {}

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
                        key = tc.get("id") or f"pos-{len(tool_call_buffers)}"
                    buf = tool_call_buffers.setdefault(key, {"arguments": ""})
                    fn = tc.get("function") or {}
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]

                # OpenAI는 툴을 호출하면 finish_reason이 "tool_calls"로 오지만,
                # Gemini(OpenAI 호환)는 툴을 호출해도 "stop"으로 온다(실측) -
                # 그래서 Google은 "stop"이어도 버퍼에 툴 호출이 있으면 처리한다.
                finish_reason = choice.get("finish_reason")
                if finish_reason == "tool_calls" or (
                    spec.company == "google"
                    and finish_reason == "stop"
                    and tool_call_buffers
                ):
                    # 지금 구조는 툴 호출 하나만 처리한다(첫 번째 호출 - OpenAI는
                    # index 0이 항상 먼저 들어오므로 기존 동작과 동일).
                    buf = next(iter(tool_call_buffers.values()), {"arguments": "{}"})
                    try:
                        args = json.loads(buf["arguments"] or "{}")
                    except json.JSONDecodeError:
                        logger.warning(
                            "툴 인자 JSON 파싱 실패 (model=%s): %s",
                            spec.model_id,
                            buf["arguments"][:200],
                        )
                        args = {}
                    query = args.get("query")
                    if query:
                        yield {
                            "type": "tool_call",
                            "query": query,
                            "time_range": _normalize_time_range(args.get("time_range")),
                            "site_hint": _normalize_site_hint(args.get("site_hint")),
                        }


def _normalize_time_range(value: str | None) -> str | None:
    """모델이 준 time_range 값을 Tavily가 받는 값으로 정리. "none"/빈 값/모델이
    스키마 밖의 값을 줬을 때는 전부 제한 없음(None)으로 취급한다."""
    if value in ("day", "week", "month", "year"):
        return value
    return None


def _normalize_site_hint(value: str | None) -> str | None:
    """site_hint도 time_range와 동일하게 스키마에 정의된 값만 인정한다."""
    if value in SITE_HINT_DOMAINS:
        return value
    return None


async def tavily_search(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> list[dict]:
    """Tavily로 웹 검색. 키가 없거나 요청이 실패하면 로그만 남기고 빈 리스트를
    반환한다 - 호출부가 검색 결과 없이 조용히 계속 진행할 수 있게 한다.

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
        logger.error("TAVILY_API_KEY가 설정되지 않아 웹 검색을 건너뜁니다.")
        return []

    payload: dict = {"query": query, "max_results": max_results}
    if time_range:
        payload["time_range"] = time_range
    domains = SITE_HINT_DOMAINS.get(site_hint or "")
    if domains:
        payload["include_domains"] = domains
        payload["include_domains_mode"] = "filter"

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                TAVILY_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
    except Exception as exc:  # noqa: BLE001
        logger.error("Tavily 검색 실패 (query=%s): %s", query, exc)
        return []


async def stream_model(
    spec: ModelSpec,
    messages: list[dict[str, str]],
    usage: dict[str, int] | None = None,
    reference_block_count: int | None = None,
) -> AsyncIterator[str]:
    """대화 이력(messages)을 포함해 호출하고, 텍스트 조각을 스트리밍으로 yield한다.

    /chat(call_model)과 달리 단일 message가 아니라 [{"role","content"}, ...]
    전체 이력을 받는다 - 채팅방 컨텍스트 유지용으로 새로 추가한 함수이며,
    기존 call_model/ /chat 엔드포인트는 그대로 둔다.

    usage: 넘기면 각 공급사가 응답에 실어 보내는 실제 토큰 사용량
    ({"input_tokens": N, "output_tokens": N})을 이 dict에 채워 넣는다
    (스트리밍 도중 채워지므로 제너레이터가 끝난 뒤 호출자가 읽으면 됨).
    응답에 값이 없으면 채우지 않으니, 호출자가 폴백을 준비해야 한다.

    reference_block_count: call_model_with_tools와 동일 - 프로젝트 참고자료
    블록 개수(Anthropic 캐싱 2번째 브레이크포인트용), 없으면 기존 동작 그대로.
    """
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(
            f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다."
        )

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        if spec.company == "anthropic":
            # Anthropic Messages API는 messages 배열 안에 role="system"을 허용하지
            # 않고, 최상위 system 파라미터로 따로 받는다. 다른 두 회사는 system
            # role을 messages에 그대로 둬도 되는 OpenAI 호환 방식이라 그쪽은 안 건드림.
            system_texts, chat_messages = _prepare_anthropic_messages(
                messages, reference_block_count
            )

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
                    _raise_for_status_logged(resp_for_raise)

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[len("data: ") :])
                    event_type = event.get("type")

                    # message_start에 입력 토큰, message_delta에 (누적) 출력
                    # 토큰이 실려 온다 - Anthropic이 실제로 청구하는 값 그대로.
                    if event_type == "message_start" and usage is not None:
                        input_tokens = event.get("message", {}).get("usage", {}).get(
                            "input_tokens"
                        )
                        if input_tokens is not None:
                            usage["input_tokens"] = input_tokens
                    elif event_type == "message_delta" and usage is not None:
                        output_tokens = event.get("usage", {}).get("output_tokens")
                        if output_tokens is not None:
                            usage["output_tokens"] = output_tokens
                    elif (
                        event_type == "content_block_delta"
                        and event.get("delta", {}).get("type") == "text_delta"
                    ):
                        yield event["delta"]["text"]
            return

        # openai / google 은 동일한 Chat Completions SSE 형식.
        # stream_options.include_usage를 켜면 마지막에 choices가 빈 배열이고
        # usage만 채워진 청크가 하나 더 온다 (실제 청구 토큰 수).
        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        async with client.stream(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": spec.model_id,
                "messages": _prepare_openai_messages(messages),
                "stream": True,
                "stream_options": {"include_usage": True},
            },
        ) as resp:
            if resp.status_code >= 400:
                body = await resp.aread()
                resp_for_raise = httpx.Response(
                    resp.status_code, content=body, request=resp.request
                )
                _raise_for_status_logged(resp_for_raise)

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)

                if usage is not None and chunk.get("usage"):
                    usage["input_tokens"] = chunk["usage"].get("prompt_tokens")
                    usage["output_tokens"] = chunk["usage"].get("completion_tokens")

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                content = choices[0].get("delta", {}).get("content")
                if content:
                    yield content
