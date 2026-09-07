import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import classifier, complexity
from app.conversations import router as conversations_router
from app.db import init_db
from app.models_config import DEFAULT_MANUAL_SIZE, get_model
from app.providers import call_model
from app.router import select_model
from app.schemas import ChatRequest, ChatResponse
from app.settings import router as settings_router

logger = logging.getLogger("uvicorn.error")

FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")


async def _load_models_background() -> None:
    """HF 모델을 백그라운드 로드 — startup await 시 수동 모드까지 막히지 않게."""
    logger.info("카테고리 분류기 로드 시작...")
    try:
        await asyncio.to_thread(classifier.load_classifier)
        logger.info("카테고리 분류기 로드 완료")
    except Exception as exc:  # noqa: BLE001
        logger.error("카테고리 분류기 로드 실패: %s", exc)

    logger.info("복잡도 임베딩 모델 로드 시작...")
    try:
        await asyncio.to_thread(complexity.load_complexity_model)
        logger.info("복잡도 임베딩 모델 로드 완료 - auto 모드 사용 가능")
    except Exception as exc:  # noqa: BLE001
        logger.error("복잡도 임베딩 모델 로드 실패 - auto 모드를 쓸 수 없습니다: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 채팅방/메시지 테이블 준비 (데모 단계라 Alembic 대신 create_all)
    await init_db()
    task = asyncio.create_task(_load_models_background())
    yield
    task.cancel()


app = FastAPI(title="NPIA - Category/Complexity Routing", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(conversations_router)
app.include_router(settings_router)


def _auto_mode_ready() -> bool:
    return classifier.is_ready() and complexity.is_ready()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "category_classifier_ready": classifier.is_ready(),
        "complexity_model_ready": complexity.is_ready(),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message가 비어 있습니다.")

    task_category: str | None = None
    complexity_score: float | None = None

    if req.mode == "auto":
        if not _auto_mode_ready():
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "분류 모델이 아직 준비되지 않았습니다. 잠시 후 다시 시도하거나 모델을 직접 선택하세요.",
                    "selected_model": None,
                },
            )
        # 두 모델 다 CPU 바운드라 이벤트 루프를 막지 않도록 스레드에서 돌린다.
        task_category = await asyncio.to_thread(classifier.classify, message)
        complexity_score = await asyncio.to_thread(
            complexity.complexity_score, message
        )
        spec = select_model(task_category, complexity_score)
    else:
        spec = get_model(req.mode, req.size or DEFAULT_MANUAL_SIZE)

    try:
        answer = await call_model(spec, message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"모델 호출 실패: {exc}",
                "selected_model": spec.display_name,
                "task_category": task_category,
                "complexity_score": complexity_score,
            },
        ) from exc

    return ChatResponse(
        answer=answer,
        selected_model=spec.display_name,
        task_category=task_category,
        complexity_score=complexity_score,
    )
