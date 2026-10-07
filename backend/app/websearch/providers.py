"""웹 검색 - Tavily → Perplexity → Brave 폴백 체인(search_web)."""

import html
import logging
import re

import httpx

from app.llm.http import API_KEYS, _raise_for_status_logged

logger = logging.getLogger("uvicorn.error")


TAVILY_URL = "https://api.tavily.com/search"
PERPLEXITY_URL = "https://api.perplexity.ai/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
# 웹 검색은 Tavily → Perplexity → Brave 폴백 체인이라, 한 곳이 응답 없이 멈춰
# 있어도 다음으로 빨리 넘어가도록 짧게 둔다(최악의 경우 3곳 × 15초).
SEARCH_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# site_hint별로 검색을 좁힐 신뢰 도메인. 날씨는 일반 키워드 검색에서 태풍/정치
# 뉴스나 확률 계산기 스팸 사이트가 섞여 들어오는 걸 실제로 확인해서 추가함.
SITE_HINT_DOMAINS: dict[str, list[str]] = {
    "weather": ["weather.go.kr", "weather.naver.com"],
}


async def tavily_search(
    query: str,
    max_results: int = 5,
    time_range: str | None = None,
    site_hint: str | None = None,
) -> list[dict]:
    """Tavily로 웹 검색. 키가 없거나 요청이 실패하면 예외를 그대로 올린다 -
    호출부(tools/web_search.py의 web_search)가 그 사실을 모델에 에러 결과로 돌려준다.
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
