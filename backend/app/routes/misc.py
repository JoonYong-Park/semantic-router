"""/search, /memory/extract-all, /chat(구버전 단발 호출) - 기존 chat/search.py, memory/router.py, main.py."""

import asyncio

from flask import Blueprint, jsonify, request

from app.auth import current_user_id
from app.db.repo import conversations as conv_repo
from app.errors import ApiError
from app.llm.client import call_model
from app.memory import extract as memory_extract
from app.routing import classifier, complexity
from app.routing.catalog import DEFAULT_MANUAL_SIZE, get_model
from app.routing.selector import select_model
from app.services.files import run_in_background

bp = Blueprint("misc", __name__)


@bp.get("/search")
def search_conversations():
    return jsonify(conv_repo.search(current_user_id(), request.args.get("query", "")))


@bp.post("/memory/extract-all")
def extract_all():
    uid = current_user_id()
    conv_ids = [c["id"] for c in conv_repo.list_conversations(uid)]
    run_in_background(memory_extract.extract_all_sequentially, uid, conv_ids)
    return jsonify({"status": "started", "count": len(conv_ids)})


@bp.post("/chat")
def chat():
    """대화 저장 없이 한 번 묻고 답하는 구버전 엔드포인트 (라우팅 확인용)."""
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        raise ApiError(400, "message가 비어 있습니다.")
    mode = body.get("mode")
    task_category = complexity_score = None
    if mode == "auto":
        if not (classifier.is_ready() and complexity.is_ready()):
            raise ApiError(503, "분류 모델이 아직 준비되지 않았습니다. 잠시 후 다시 시도하거나 모델을 직접 선택하세요.")
        task_category = classifier.classify(message)
        complexity_score = complexity.complexity_score(message)
        spec = select_model(task_category, complexity_score)
    else:
        spec = get_model(mode, body.get("size") or DEFAULT_MANUAL_SIZE)
    try:
        answer = asyncio.run(call_model(spec, message))
    except Exception as exc:  # noqa: BLE001
        raise ApiError(502, f"모델 호출 실패: {exc}")
    return jsonify({"answer": answer, "selected_model": spec.display_name,
                    "task_category": task_category, "complexity_score": complexity_score})
