"""Provider API 호출 (OpenAI / Gemini OpenAI-compat / Anthropic)."""

import json
import logging
import os
from dataclasses import dataclass, field
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


@dataclass
class ToolCallResult:
    """call_model_with_tools()의 회사별 응답을 통일한 내부 표현.

    wants_search=True면 search_query(+time_range)가 채워지고 direct_text는 None,
    False면 반대 (도구 호출 없이 바로 텍스트로 답한 경우).
    usage는 이 1차 호출 자체가 실제로 쓴 토큰({"input_tokens","output_tokens"}) -
    검색 후 2차 스트리밍 호출의 토큰과 합산해야 이번 턴의 총 사용량이 된다.
    """

    wants_search: bool
    search_query: str | None
    direct_text: str | None
    time_range: str | None = None
    site_hint: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


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


async def call_model_with_tools(
    spec: ModelSpec, messages: list[dict[str, str]]
) -> ToolCallResult:
    """웹 검색 tool을 실어서 1차(비스트리밍) 호출 - 라우팅된 spec 모델 자신이
    검색이 필요한지 판단한다(ChatGPT/Claude가 실제로 쓰는 방식과 동일, 판단
    전용 별도 모델은 안 씀). stream_model()과 달리 tools를 받고 비스트리밍이라
    별도 함수로 뒀다.
    """
    api_key = API_KEYS.get(spec.company, "")
    if not api_key:
        raise RuntimeError(f"{KEY_ENV_NAMES[spec.company]}가 설정되지 않았습니다.")

    tools = build_tools_for_company(spec.company)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        if spec.company == "anthropic":
            # stream_model()과 동일하게 system role은 최상위 파라미터로 분리.
            system_texts = [m["content"] for m in messages if m["role"] == "system"]
            chat_messages = [m for m in messages if m["role"] != "system"]

            payload: dict = {
                "model": spec.model_id,
                "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": chat_messages,
                "tools": tools,
            }
            if system_texts:
                payload["system"] = "\n\n".join(system_texts)

            resp = await client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

            raw_usage = data.get("usage", {})
            usage = {
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
            }

            tool_use = next(
                (b for b in data["content"] if b.get("type") == "tool_use"), None
            )
            if tool_use is not None:
                tool_input = tool_use.get("input", {})
                return ToolCallResult(
                    wants_search=True,
                    search_query=tool_input.get("query"),
                    direct_text=None,
                    time_range=_normalize_time_range(tool_input.get("time_range")),
                    site_hint=_normalize_site_hint(tool_input.get("site_hint")),
                    usage=usage,
                )

            texts = [b["text"] for b in data["content"] if b.get("type") == "text"]
            return ToolCallResult(
                wants_search=False,
                search_query=None,
                direct_text="".join(texts),
                usage=usage,
            )

        # openai / google - 완전히 동일한 tools/tool_calls 포맷
        url = OPENAI_URL if spec.company == "openai" else GOOGLE_URL
        payload: dict = {
            "model": spec.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        }
        if spec.company == "openai":
            # reasoning 모델은 Chat Completions에서 tools를 쓰려면 reasoning_effort를
            # "none"으로 꺼야 한다 (실제로 400 에러를 받아 확인함 - 안 끄면
            # "Function tools with reasoning_effort are not supported ..." 에러).
            payload["reasoning_effort"] = "none"
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

        raw_usage = data.get("usage", {})
        usage = {
            "input_tokens": raw_usage.get("prompt_tokens", 0),
            "output_tokens": raw_usage.get("completion_tokens", 0),
        }

        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            try:
                args = json.loads(tool_calls[0]["function"]["arguments"])
                query = args.get("query")
            except (json.JSONDecodeError, AttributeError):
                args, query = {}, None
            if query:
                return ToolCallResult(
                    wants_search=True,
                    search_query=query,
                    direct_text=None,
                    time_range=_normalize_time_range(args.get("time_range")),
                    site_hint=_normalize_site_hint(args.get("site_hint")),
                    usage=usage,
                )

        return ToolCallResult(
            wants_search=False,
            search_query=None,
            direct_text=message.get("content") or "",
            usage=usage,
        )


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
) -> AsyncIterator[str]:
    """대화 이력(messages)을 포함해 호출하고, 텍스트 조각을 스트리밍으로 yield한다.

    /chat(call_model)과 달리 단일 message가 아니라 [{"role","content"}, ...]
    전체 이력을 받는다 - 채팅방 컨텍스트 유지용으로 새로 추가한 함수이며,
    기존 call_model/ /chat 엔드포인트는 그대로 둔다.

    usage: 넘기면 각 공급사가 응답에 실어 보내는 실제 토큰 사용량
    ({"input_tokens": N, "output_tokens": N})을 이 dict에 채워 넣는다
    (스트리밍 도중 채워지므로 제너레이터가 끝난 뒤 호출자가 읽으면 됨).
    응답에 값이 없으면 채우지 않으니, 호출자가 폴백을 준비해야 한다.
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
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
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

                if usage is not None and chunk.get("usage"):
                    usage["input_tokens"] = chunk["usage"].get("prompt_tokens")
                    usage["output_tokens"] = chunk["usage"].get("completion_tokens")

                choices = chunk.get("choices") or []
                if not choices:
                    continue
                content = choices[0].get("delta", {}).get("content")
                if content:
                    yield content
