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


def backfill_missing_titles(event=None, context=None) -> dict:
    """제목이 없는 대화방(답변이 하나라도 있는 것)에 제목을 붙인다. 한 번 쓰는 관리 작업.

    배포 Lambda에서: aws lambda invoke --function-name joomini-dev \\
      --payload '{"command": "app.chat.title.backfill_missing_titles"}' out.json
    (Zappa 핸들러가 command의 함수를 실행한다.)
    """
    from boto3.dynamodb.conditions import Attr

    from app.db import dynamo, keys

    kwargs = {
        "FilterExpression": Attr("SK").begins_with(keys.CONV_PREFIX) & Attr("PK").begins_with("USER#")
        & (Attr("title").not_exists() | Attr("title").eq("")),
        "ProjectionExpression": "PK, id",
    }
    targets = []
    while True:
        resp = dynamo.table().scan(**kwargs)
        targets += resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    done = 0
    for item in targets:
        uid, cid = item["PK"].split("#", 1)[1], item["id"]
        messages = conv_repo.get_conversation_page(cid)
        first_user = next((m for m in messages if m.get("role") == "user" and (m.get("content") or "").strip()), None)
        has_answer = any(m.get("role") == "assistant" for m in messages)
        if first_user and has_answer:
            generate_and_save_title(uid, cid, first_user["content"])
            done += 1
    logger.info("제목 채우기: 대상 %d개 중 %d개", len(targets), done)
    return {"candidates": len(targets), "titled": done}
