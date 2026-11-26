"""메시지 전송 한 턴 (기존 chat/router.py의 send_message + run_turn).

흐름은 기존과 같다.
1. 컨텍스트(개인 지침, 가져온 정보, 전역/프로젝트 메모리, 프로젝트 지침)를 먼저 읽는다.
2. 사용자 질문을 바로 저장하고 이번 턴 첨부를 연결한다 (트랜잭션 하나).
3. 응답 생성은 브라우저 연결과 분리된 스레드에서 끝까지 돌고, SSE는 TurnStream을 구독만 한다.
4. 툴 루프, 빈 답변 방어, 생성 파일 연결, 사용량 기록 후 done.

달라진 점: asyncio 태스크 대신 스레드 하나가 asyncio.run()으로 턴을 돈다
(엔진 코드 llm/tools가 async라서). 토큰 사용량은 메시지 SUM 대신 카운터에 더한다.
"""

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.chat.context import DOC_TOOLS_GUIDE, memory_topic_to_text
from app.chat.history import (
    build_history,
    has_files_in_context,
    project_reference_blocks,
    wait_for_attachment_processing,
)
from app.chat.stream import TurnStream, active_turns, active_turns_lock
from app.chat.title import generate_and_save_title
from app.core.tokens import count_tokens, estimate_content_tokens
from app.db.repo import conversations as conv_repo
from app.db.repo import memory as memory_repo
from app.db.repo import projects as project_repo
from app.db.repo import usage as usage_repo
from app.db.repo import users as user_repo
from app.errors import ApiError
from app.llm.client import stream_model_with_tools
from app.routing import classifier, complexity
from app.routing.catalog import DEFAULT_MANUAL_SIZE, get_model
from app.routing.intent import needs_edit_tools, needs_file_tools, needs_image_tools
from app.routing.selector import bump_size, select_model
from app.services.files import run_alongside_response
from app.tools.base import ToolResult
from app.tools.registry import get_tool_specs, run_tool

KST = ZoneInfo("Asia/Seoul")

logger = logging.getLogger("uvicorn.error")

MAX_TOOL_ROUNDS = 5
DOC_MAX_OUTPUT_TOKENS = 32000
EDIT_MAX_TOOL_ROUNDS = 8
EMPTY_ANSWER = "응답을 완성하지 못했어요. 다시 시도해 주세요."
FILES_ONLY_ANSWER = "파일을 생성했습니다."

MODES = {"auto", "openai", "google", "anthropic"}
SIZES = {"small", "medium", "large"}


def start_turn(uid, cid, body: dict) -> TurnStream:
    """요청 검증, 질문 저장까지 하고 응답 생성 스레드를 띄운다. 구독할 TurnStream을 돌려준다."""
    content = body.get("content")
    mode = body.get("mode")
    size = body.get("size")
    if not isinstance(content, str) or mode not in MODES or (size is not None and size not in SIZES):
        raise ApiError(422, "요청 형식이 올바르지 않습니다.")
    attachment_ids = [str(a) for a in body.get("attachment_ids") or []]

    conv = conv_repo.get_conversation(uid, cid)
    if conv is None:
        raise ApiError(404, "채팅방을 찾을 수 없습니다.")
    if mode == "auto" and not (classifier.is_ready() and complexity.is_ready()):
        raise ApiError(503, "분류 모델이 아직 준비되지 않았습니다.")

    is_first_message = int(conv.get("message_count", 0)) == 0
    # 제목이 아직 없으면 이번 턴이 성공했을 때 만든다. "첫 메시지일 때만"이면 첫 질문이 실패한
    # 대화방은 이후 답변이 성공해도 영영 "새 채팅"으로 남았다.
    needs_title = not (conv.get("title") or "").strip()
    settings = user_repo.get_settings(uid)
    memory_facts = [memory_topic_to_text(m) for m in memory_repo.list_user_memory(uid)]
    project_id = conv.get("project_id")
    project_instruction = None
    project_memory_facts: list[str] = []
    if project_id:
        project = project_repo.get_project(uid, project_id)
        project_instruction = project.get("instruction") if project else None
        project_memory_facts = [memory_topic_to_text(m) for m in memory_repo.list_project_memory(project_id)]

    # 이 대화의 것이면서 아직 메시지에 안 붙은 사용자 업로드만 유효 (나머지는 조용히 무시).
    valid_attachments = []
    for aid in attachment_ids:
        att = conv_repo.get_attachment_in_conv(cid, aid)
        if att and not att.get("message_id") and att.get("source", "upload") == "upload":
            valid_attachments.append(att)
    user_msg = conv_repo.add_message(
        uid, cid, "user", content,
        attachment_ids=[a["id"] for a in valid_attachments],
        token_count=count_tokens(content),
    )

    ctx = {
        "uid": uid, "cid": cid, "content": content, "mode": mode, "size": size,
        "user_msg": user_msg, "attachments": valid_attachments, "project_id": project_id,
        "personal_instruction": settings.get("personal_instruction"),
        "imported_memory": settings.get("imported_memory"),
        "memory_facts": memory_facts,
        "project_instruction": project_instruction,
        "project_memory_facts": project_memory_facts,
        "is_first_message": is_first_message,
        "needs_title": needs_title,
    }
    turn = TurnStream()
    with active_turns_lock:
        active_turns[cid] = turn
    run_alongside_response(_drive, turn, ctx)
    return turn


def _drive(turn: TurnStream, ctx: dict) -> None:
    cid = ctx["cid"]
    completed = False
    try:
        completed = asyncio.run(_run_turn(turn, ctx))
    except Exception as exc:  # noqa: BLE001
        logger.error("응답 생성 실패 (conversation_id=%s): %s", cid, exc, exc_info=True)
        turn.emit({"type": "error", "message": f"응답 생성 실패: {exc}"})
    finally:
        turn.finish()
        with active_turns_lock:
            if active_turns.get(cid) is turn:
                del active_turns[cid]
    try:
        # 답변이 저장된 턴에서, 아직 제목이 없으면 만든다 (모델 호출 실패 시에는 만들지 않음).
        if completed and ctx["needs_title"]:
            title_source = ctx["content"]
            if not ctx["is_first_message"]:
                # 첫 질문이 실패해 제목이 비어 있던 대화방 - 지금 질문("내일은?")보다 첫 질문이 대화를 잘 나타낸다
                first = next((m for m in conv_repo.get_conversation_page(cid)
                              if m.get("role") == "user" and (m.get("content") or "").strip()), None)
                title_source = first["content"] if first else title_source
            generate_and_save_title(ctx["uid"], cid, title_source)
    finally:
        # Lambda는 이 신호까지 응답(=호출)을 붙잡아 둔다. done 이벤트 직후 호출이
        # 끝나면 실행 환경이 멈춰 위의 제목 생성이 중간에 끊겼다.
        turn.settle()


async def _run_turn(turn: TurnStream, ctx: dict) -> bool:
    """답변을 저장하고 done까지 보냈으면 True."""
    uid, cid, content, mode = ctx["uid"], ctx["cid"], ctx["content"], ctx["mode"]
    project_id = ctx["project_id"]
    new_attachments = ctx["attachments"]

    # 1) 라우팅
    task_category = None
    complexity_score = None
    if mode == "auto":
        task_category = await asyncio.to_thread(classifier.classify, content)
        complexity_score = await asyncio.to_thread(complexity.complexity_score, content)
        spec = select_model(task_category, complexity_score)
    else:
        spec = get_model(mode, ctx["size"] or DEFAULT_MANUAL_SIZE)

    # routing/intent.py는 첨부를 속성(a.mime_type)으로 읽어서, dict를 속성 객체로 감싸 넘긴다.
    att_views = [SimpleNamespace(**a) for a in new_attachments]
    doc_tools_enabled, intent_reason = needs_file_tools(content, att_views)
    if doc_tools_enabled:
        bumped = bump_size(spec) if mode == "auto" else spec
        logger.info("파일 의도 감지 (conversation_id=%s, reason=%s, model=%s→%s)",
                    cid, intent_reason, spec.display_name, bumped.display_name)
        spec = bumped

    image_tools_enabled, image_reason = needs_image_tools(content)
    if image_tools_enabled:
        logger.info("이미지 의도 감지 (conversation_id=%s, reason=%s)", cid, image_reason)

    from app.documents.editor import supported_formats

    editable_exts = tuple(f".{f}" for f in supported_formats())
    files_in_context = any(a["original_name"].lower().endswith(editable_exts) for a in new_attachments) \
        or has_files_in_context(cid, project_id)
    edit_tools_enabled, edit_reason = needs_edit_tools(content, att_views, files_in_context)
    if edit_tools_enabled:
        bumped = bump_size(spec) if mode == "auto" else spec
        logger.info("수정 의도 감지 (conversation_id=%s, reason=%s, model=%s→%s)",
                    cid, edit_reason, spec.display_name, bumped.display_name)
        spec = bumped

    # 2) meta - 스트리밍 전에 보내서 프론트가 배지를 바로 그린다
    turn.emit({"type": "meta", "model": spec.display_name, "category": task_category,
               "complexity": complexity_score})

    if any(a["extraction_status"] == "pending" for a in new_attachments):
        turn.emit({"type": "processing", "message": "첨부파일 처리 중..."})
        new_attachments = wait_for_attachment_processing(cid, new_attachments)

    # 3) 이력 (이미 저장된 이번 질문은 빼고 new_query로 직접 붙인다)
    history = build_history(cid, content, new_attachments, exclude_message_id=ctx["user_msg"]["id"])

    reference_block_count = None
    if project_id:
        reference_blocks = project_reference_blocks(project_repo.list_project_files(project_id, done_only=True))
        if reference_blocks:
            first = history[0]["content"]
            if isinstance(first, str):
                first = [{"type": "text", "text": first}] if first else []
            history[0]["content"] = reference_blocks + first
            reference_block_count = len(reference_blocks)

    # 4) 질문 token_count를 실제로 모델에 보낸 content 기준으로 갱신
    conv_repo.update_message(cid, ctx["user_msg"]["sk"],
                             token_count=estimate_content_tokens(history[-1]["content"]))

    system_parts = []
    if (ctx["personal_instruction"] or "").strip():
        system_parts.append(f"[개인 지침]\n{ctx['personal_instruction'].strip()}")
    if (ctx["imported_memory"] or "").strip():
        system_parts.append(f"[가져온 정보]\n{ctx['imported_memory'].strip()}")
    if ctx["memory_facts"]:
        system_parts.append("[기억]\n" + "\n".join(f"- {f}" for f in ctx["memory_facts"]))
    if (ctx["project_instruction"] or "").strip():
        system_parts.append(f"[프로젝트 지침]\n{ctx['project_instruction'].strip()}")
    if ctx["project_memory_facts"]:
        system_parts.append("[프로젝트 기억]\n" + "\n".join(f"- {f}" for f in ctx["project_memory_facts"]))
    if len(system_parts) > 1:
        system_parts = [
            "아래는 사용자에 대한 참고 정보다. 서로 내용이 충돌하면 "
            "[개인 지침] → [가져온 정보] → [기억] → [프로젝트 지침] → "
            "[프로젝트 기억] 순으로 우선 적용해."
        ] + system_parts
    if doc_tools_enabled or edit_tools_enabled:
        system_parts.append(DOC_TOOLS_GUIDE)
    # 모델은 오늘 날짜를 모른다. "오늘/내일/이번 주" 질문과 검색 결과의 최신 여부 판단에 쓴다.
    now = datetime.now(KST)
    system_parts.append(f"[현재 시각] {now:%Y-%m-%d %H:%M} KST ({'월화수목금토일'[now.weekday()]}요일)")
    if system_parts:
        history = [{"role": "system", "content": "\n\n".join(system_parts)}] + history

    # 5) 툴 루프
    full_response = ""
    usage: dict[str, int] = {}
    web_search_used = False
    web_search_query = None
    generated_ids: list[str] = []
    working = list(history)
    history_len = len(working)
    tool_specs = get_tool_specs(
        include_doc_tools=doc_tools_enabled,
        include_image_tools=image_tools_enabled,
        include_edit_tools=edit_tools_enabled,
    )
    max_rounds = EDIT_MAX_TOOL_ROUNDS if edit_tools_enabled else MAX_TOOL_ROUNDS
    max_output_tokens = DOC_MAX_OUTPUT_TOKENS if doc_tools_enabled else None
    tool_ctx_base = {"conversation_id": cid, "project_id": project_id, "user_id": uid, "turn_state": {}}
    try:
        for round_index in range(max_rounds):
            is_last_round = round_index == max_rounds - 1
            round_text = ""
            round_calls: list[dict] = []
            round_usage: dict[str, int] = {}
            async for event in stream_model_with_tools(
                spec, working, tool_specs, round_usage, reference_block_count, history_len,
                max_output_tokens=max_output_tokens, force_final=is_last_round,
            ):
                if event["type"] == "text":
                    piece = event["content"]
                    if not round_text and full_response:
                        piece = "\n\n" + piece
                    round_text += event["content"]
                    full_response += piece
                    turn.emit({"type": "chunk", "content": piece})
                elif event["type"] == "tool_call":
                    round_calls.append(event)

            for key in ("input_tokens", "output_tokens"):
                if round_usage.get(key) is not None:
                    usage[key] = (usage.get(key) or 0) + round_usage[key]

            if round_calls:
                logger.info("툴 호출 (conversation_id=%s, round=%d, tools=%s)",
                            cid, round_index + 1, [c["name"] for c in round_calls])
            if not round_calls or is_last_round:
                break

            working.append({
                "role": "assistant",
                "content": round_text,
                "tool_calls": [
                    {"id": c["id"], "name": c["name"], "arguments": c["arguments"] or {},
                     "provider_extra": c["provider_extra"]}
                    for c in round_calls
                ],
            })
            for call in round_calls:
                result: ToolResult | None = None
                async for kind, payload in run_tool(call, tool_ctx_base):
                    if kind == "event":
                        turn.emit(payload)
                    else:
                        result = payload
                if result.metadata.get("web_search_query") and not web_search_used:
                    web_search_used = True
                    web_search_query = result.metadata["web_search_query"]
                if result.metadata.get("generated_attachment_id"):
                    generated_ids.append(str(result.metadata["generated_attachment_id"]))
                working.append({"role": "tool", "tool_call_id": call["id"], "name": call["name"],
                                "content": result.content, "is_error": result.is_error})
    except Exception as exc:  # noqa: BLE001
        logger.error("모델 호출 실패 (conversation_id=%s, model=%s): %s",
                     cid, spec.display_name, exc, exc_info=True)
        if not generated_ids:
            turn.emit({"type": "error", "message": f"모델 호출 실패: {exc}"})
            return False
        # 이미 만든 파일이 있으면 카드가 사라지지 않게 정상 마무리한다.

    if not full_response.strip():
        full_response = FILES_ONLY_ANSWER if generated_ids else EMPTY_ANSWER
        turn.emit({"type": "chunk", "content": full_response})

    input_tokens = usage.get("input_tokens")
    if input_tokens is None:
        input_tokens = sum(estimate_content_tokens(m["content"]) for m in working)
    output_tokens = usage.get("output_tokens")
    if output_tokens is None:
        output_tokens = count_tokens(full_response)

    # 6) 답변 저장 + 생성 파일 연결 + 대화 갱신 (트랜잭션 하나), 사용량 카운터
    conv_repo.add_message(
        uid, cid, "assistant", full_response, attachment_ids=generated_ids,
        model_used=spec.display_name, task_category=task_category, complexity_score=complexity_score,
        input_tokens=input_tokens, output_tokens=output_tokens, token_count=count_tokens(full_response),
        web_search_used=web_search_used, web_search_query=web_search_query,
    )
    usage_repo.record_usage(uid, spec.display_name, input_tokens, output_tokens)

    # 7) done은 답변이 저장된 뒤에 (화면이 목록/메시지를 다시 불러와도 방금 답이 보이게)
    turn.emit({"type": "done"})
    return True
