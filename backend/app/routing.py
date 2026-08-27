"""Auto 모드 — Envoy를 거쳐 vLLM Semantic Router로 요청을 전달하고,
라우터가 응답 헤더에 실어 보내는 분류/선택 결과(x-vsr-selected-*)를 읽어온다.

참고: https://github.com/vllm-project/semantic-router 의
website/docs/troubleshooting/vsr-headers.md 에 문서화된 응답 헤더 계약을 따른다.
  - x-vsr-selected-model    : 라우터가 선택한 논리 모델 이름 (기본 노출)
  - x-vsr-selected-category : 도메인 분류 결과 (x-vsr-debug: true 요청 시에만 노출)
"""

import os

import httpx

ROUTER_URL = os.environ.get("ROUTER_URL", "http://envoy:8801/v1/chat/completions")

REQUEST_TIMEOUT = httpx.Timeout(120.0)

# config/config.yaml의 providers.models[].name 과 일치해야 함
MODEL_DISPLAY_NAMES = {
    "gpt-4": "GPT",
    "gemini-2.5-pro": "Gemini",
    "claude-opus": "Claude",
}


class RouterCallError(Exception):
    """Auto 모드 호출 실패. 라우터가 모델을 선택하는 데까지는 성공했다면
    selected_model/category를 채워서, 실패했더라도 '어떤 모델에게 요청을
    보냈는지'를 프론트에 보여줄 수 있게 한다."""

    def __init__(
        self,
        message: str,
        selected_model: str | None = None,
        category: str | None = None,
    ):
        super().__init__(message)
        self.selected_model = selected_model
        self.category = category


async def call_router_auto(message: str) -> tuple[str, str, str | None]:
    """라우터(Auto 모드) 호출. 반환값: (answer, selected_model_display, category)"""

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            resp = await client.post(
                ROUTER_URL,
                headers={
                    "Content-Type": "application/json",
                    # 디버그 서페이스를 켜야 x-vsr-selected-category가 응답에 실린다.
                    "x-vsr-debug": "true",
                },
                json={
                    "model": "auto",
                    "messages": [{"role": "user", "content": message}],
                },
            )
        except httpx.ConnectError as exc:
            raise RouterCallError(
                "vLLM Semantic Router(Envoy)에 연결할 수 없습니다. "
                "docker compose로 envoy/router 서비스가 떠 있는지 확인하세요."
            ) from exc

        selected_model_raw = resp.headers.get("x-vsr-selected-model")
        category = resp.headers.get("x-vsr-selected-category")
        selected_model_display = (
            MODEL_DISPLAY_NAMES.get(selected_model_raw, selected_model_raw)
            if selected_model_raw
            else None
        )

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RouterCallError(
                f"라우터가 {selected_model_display or '모델'}을(를) 선택했지만 "
                f"호출에 실패했습니다 (HTTP {resp.status_code}): {resp.text[:300]}",
                selected_model=selected_model_display,
                category=category,
            ) from exc

        data = resp.json()
        answer = data["choices"][0]["message"]["content"]

        return answer, selected_model_display or "unknown", category
