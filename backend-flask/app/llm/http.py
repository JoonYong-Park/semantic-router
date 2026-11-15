"""벤더 API 공용 - API 키 테이블과 실패 응답 본문을 로그로 남기는 raise 헬퍼.
LLM(client/images)과 웹 검색(websearch)이 같이 쓴다."""

import logging
import os

import httpx

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


def _raise_for_status_logged(resp: httpx.Response) -> None:
    """resp.raise_for_status()는 응답 본문을 버리고 상태 코드만 담은
    메시지를 던져서, 4xx의 실제 원인(예: OpenAI의 구체적인 거부 사유)이
    로그에 안 남는 문제가 있었다. 재발 방지를 위해 실패 시 본문을 먼저
    로그로 남긴 뒤 그대로 raise한다."""
    if resp.status_code >= 400:
        logger.error("API 오류 응답 (%s): %s", resp.status_code, resp.text)
    resp.raise_for_status()
