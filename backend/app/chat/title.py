"""첫 메시지로 채팅방 제목을 백그라운드에서 생성 (기존 chat/title.py)."""

import asyncio
import logging

from app.db.repo import conversations as conv_repo
from app.llm.client import call_model
from app.routing.catalog import get_model

logger = logging.getLogger("uvicorn.error")

# 제목 생성은 품질보다 속도/비용이 중요해서 가장 작은 모델을 쓴다.
TITLE_MODEL = get_model("openai", "small")


def generate_and_save_title(uid, cid, first_message: str) -> None:
    """응답 저장이 끝난 뒤 별도 스레드에서 실행한다 (services.files.run_in_background)."""
    try:
        prompt = (
            "아래 질문을 10자 이내의 짧은 제목으로 만들어줘. "
            f"설명 없이 제목만 답해.\n\n{first_message}"
        )
        title = asyncio.run(call_model(TITLE_MODEL, prompt))
        title = title.strip().strip('"').strip("'")[:200]
    except Exception as exc:  # noqa: BLE001
        logger.error("제목 생성 실패 (conversation_id=%s): %s", cid, exc)
        return
    if conv_repo.get_conversation(uid, cid) is not None:
        conv_repo.update_conversation(uid, cid, title=title)
