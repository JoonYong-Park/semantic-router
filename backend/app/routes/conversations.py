"""/conversations - 기존 chat/router.py (CRUD, 메시지 조회, 전송, 재접속 스트림). URL과 응답 형태는 같다."""

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app import storage
from app.auth import current_user_id
from app.chat.stream import active_turns
from app.chat.turn import start_turn
from app.db.repo import conversations as conv_repo
from app.db.repo import projects as project_repo
from app.errors import ApiError
from app.serializers import conversation_out, message_out
from app.services import files

bp = Blueprint("conversations", __name__, url_prefix="/conversations")


def owned_conversation(cid) -> dict:
    conv = conv_repo.get_conversation(current_user_id(), cid)
    if conv is None:
        raise ApiError(404, "채팅방을 찾을 수 없습니다.")
    return conv


def _sse_response(turn) -> Response:
    return Response(
        stream_with_context(turn.subscribe()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.post("")
def create_conversation():
    return jsonify(conversation_out(conv_repo.create_conversation(current_user_id())))


@bp.get("")
def list_conversations():
    return jsonify([conversation_out(c) for c in conv_repo.list_conversations(current_user_id())])


@bp.patch("/<cid>")
def update_conversation(cid):
    """제목 변경 / 고정 토글. 바디에 들어온 필드만 바꾼다 (기존 exclude_unset)."""
    owned_conversation(cid)
    body = request.get_json(silent=True) or {}
    fields = {k: body[k] for k in ("title", "pinned", "sort_order") if k in body}
    conv = conv_repo.update_conversation(current_user_id(), cid, **fields) if fields \
        else conv_repo.get_conversation(current_user_id(), cid)
    return jsonify(conversation_out(conv))


@bp.patch("/<cid>/project")
def update_conversation_project(cid):
    """채팅을 프로젝트로 편입하거나(project_id) 해제한다(null)."""
    owned_conversation(cid)
    project_id = (request.get_json(silent=True) or {}).get("project_id")
    if project_id is not None and project_repo.get_project(current_user_id(), project_id) is None:
        raise ApiError(404, "프로젝트를 찾을 수 없습니다.")  # 남의 프로젝트도 없는 것과 똑같이
    conv = conv_repo.set_conversation_project(current_user_id(), cid, project_id)
    return jsonify(conversation_out(conv))


@bp.delete("/<cid>")
def delete_conversation(cid):
    owned_conversation(cid)
    conv_repo.delete_conversation(current_user_id(), cid)
    storage.delete_prefix(files.chat_dir(cid))  # 업로드 원본, 추출 이미지, 생성 파일
    return "", 204


@bp.get("/<cid>/messages")
def get_messages(cid):
    owned_conversation(cid)
    return jsonify([message_out(m) for m in conv_repo.get_conversation_page(cid)])


@bp.get("/<cid>/stream")
def resume_stream(cid):
    """다른 채팅에 갔다 오거나 새로고침한 화면이 진행 중인 응답에 다시 붙는다.
    진행 중인 응답이 없으면 204."""
    owned_conversation(cid)
    turn = active_turns.get(cid)
    if turn is None or turn.done:
        return "", 204
    return _sse_response(turn)


@bp.post("/<cid>/messages")
def send_message(cid):
    turn = start_turn(current_user_id(), cid, request.get_json(silent=True) or {})
    return _sse_response(turn)
