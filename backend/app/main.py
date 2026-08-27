import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.providers import call_claude, call_gemini, call_openai
from app.routing import RouterCallError, call_router_auto
from app.schemas import ChatRequest, ChatResponse

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

app = FastAPI(title="NPC AI Assistant - Phase 1 Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

MANUAL_DISPLAY_NAMES = {"gpt": "GPT", "gemini": "Gemini", "claude": "Claude"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message가 비어 있습니다.")

    try:
        if req.mode == "auto":
            answer, selected_model, category = await call_router_auto(message)
            return ChatResponse(
                answer=answer, selected_model=selected_model, category=category
            )

        if req.mode == "gpt":
            answer = await call_openai(message)
        elif req.mode == "gemini":
            answer = await call_gemini(message)
        elif req.mode == "claude":
            answer = await call_claude(message)
        else:
            raise HTTPException(status_code=400, detail=f"알 수 없는 mode: {req.mode}")

        return ChatResponse(
            answer=answer,
            selected_model=MANUAL_DISPLAY_NAMES[req.mode],
            category=None,
        )

    except HTTPException:
        raise
    except RouterCallError as exc:
        # Auto 모드 실패: 라우터가 모델을 선택하는 데까지 성공했다면
        # 그 모델 정보를 실어서, 실패해도 "어떤 모델에게 요청했는지" 보여준다.
        raise HTTPException(
            status_code=502,
            detail={
                "message": str(exc),
                "selected_model": exc.selected_model,
                "category": exc.category,
            },
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 데모용 단일 에러 경로
        # 수동 모드 실패는 mode로 대상 모델이 항상 확정돼 있으므로 그대로 실어 보낸다.
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"모델 호출 실패: {exc}",
                "selected_model": MANUAL_DISPLAY_NAMES.get(req.mode),
                "category": None,
            },
        ) from exc
