"""이미지 생성 (OpenAI Images API)."""

import base64
import logging

import httpx

from app.llm.http import API_KEYS, _raise_for_status_logged

logger = logging.getLogger("uvicorn.error")


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
