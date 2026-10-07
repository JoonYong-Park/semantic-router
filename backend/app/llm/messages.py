"""중립 메시지 포맷(텍스트/이미지/문서 블록, tool_calls, tool 결과)을 회사별
요청 스키마로 변환한다. Anthropic 프롬프트 캐시 브레이크포인트도 여기서 붙인다."""

import json

from app.routing.catalog import Company


# --- 첨부파일 멀티모달 content 어댑터 ---
#
# chat/history.py가 만드는 내부 표현(provider 중립)은 문자열이거나, 아래
# 블록들의 리스트다:
#   {"type": "text", "text": str}
#   {"type": "image", "media_type": str, "data": str(base64)}
#   {"type": "document", "media_type": str, "data": str(base64)}  (PDF)
# 이 함수들이 실제 요청 직전에 회사별 스키마로 변환한다.


def _to_anthropic_content(content: str | list[dict]) -> str | list[dict]:
    if isinstance(content, str):
        return content
    blocks = []
    for block in content:
        if block["type"] == "text":
            blocks.append({"type": "text", "text": block["text"]})
        elif block["type"] not in ("image", "document"):
            # 이미 Anthropic 포맷인 블록(tool_use/tool_result 등)은 그대로 통과 -
            # source 래퍼를 씌우면 깨진다.
            blocks.append(block)
        else:  # image / document - Anthropic 둘 다 source 래퍼가 동일한 모양
            blocks.append(
                {
                    "type": block["type"],
                    "source": {
                        "type": "base64",
                        "media_type": block["media_type"],
                        "data": block["data"],
                    },
                }
            )
    return blocks


def _to_openai_content(content: str | list[dict]) -> str | list[dict]:
    """OpenAI Chat Completions 포맷 (Google의 OpenAI 호환 엔드포인트도 동일하게 지원).
    PDF는 이미지가 아니라 file 블록(file_data에 data URI)으로 보낸다 - OpenAI
    Chat Completions가 실제로 지원하는 파일 입력 방식."""
    if isinstance(content, str):
        return content
    blocks = []
    for block in content:
        if block["type"] == "text":
            blocks.append({"type": "text", "text": block["text"]})
        elif block["type"] == "image":
            blocks.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{block['media_type']};base64,{block['data']}"
                    },
                }
            )
        else:  # document (PDF)
            blocks.append(
                {
                    "type": "file",
                    "file": {
                        "filename": "attachment.pdf",
                        "file_data": f"data:{block['media_type']};base64,{block['data']}",
                    },
                }
            )
    return blocks


def _apply_anthropic_cache_control(
    anthropic_messages: list[dict], reference_block_count: int | None = None
) -> None:
    """대화 이력 중 "이번 턴"을 제외한 마지막 안정 지점(이전 턴)의 마지막
    content block에 프롬프트 캐싱을 건다. 그 지점까지의 모든 이전 턴(특히
    이미지가 포함된 과거 턴)이 캐시 대상이 되어, 다음 턴부터는 재계산 없이
    캐시를 재사용한다. 새 질문(마지막 메시지)은 매번 바뀌므로 캐시 대상에서 뺀다.

    reference_block_count가 있으면(프로젝트 참고자료가 첫 번째 메시지 맨
    앞에 끼워진 경우) 그 블록들이 끝나는 지점에도 별도 브레이크포인트를
    하나 더 찍는다 - 참고자료 자체가 바뀌지 않는 한 대화 턴 수와 무관하게
    항상 캐시 재사용 대상이 되도록. 두 브레이크포인트는 서로 다른 메시지/
    블록을 가리키므로 충돌 없이 공존한다(Anthropic은 최대 4개까지 지원,
    지금은 2개만 사용)."""
    if reference_block_count and anthropic_messages:
        first_content = anthropic_messages[0]["content"]
        if isinstance(first_content, list) and len(first_content) >= reference_block_count:
            idx = reference_block_count - 1
            first_content[idx] = {**first_content[idx], "cache_control": {"type": "ephemeral"}}

    if len(anthropic_messages) < 2:
        return
    target = anthropic_messages[-2]
    content = target["content"]
    if isinstance(content, str):
        target["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


# --- 중립 메시지 포맷 → 회사별 변환 ---
#
# chat/history.py의 툴 루프는 회사와 무관한 아래 포맷으로 메시지를 쌓는다:
#   {"role": "system"|"user", "content": str | list[블록]}
#   {"role": "assistant", "content": str, "tool_calls": [  # tool_calls는 없을 수 있음
#       {"id", "name", "arguments": dict, "provider_extra": dict}]}
#   {"role": "tool", "tool_call_id", "name", "content": str, "is_error": bool}
# 아래 함수들이 요청 직전에 회사별 스키마로 바꾼다.


def _to_anthropic_chat_messages(messages: list[dict]) -> list[dict]:
    """system을 뺀 중립 메시지를 Anthropic messages로 변환. 연속된 tool 결과는
    user 메시지 하나에 tool_result 블록으로 모두 묶는다(호출마다 결과가 하나씩
    있어야 하고, 같은 user 메시지에서 tool_result가 텍스트보다 앞에 와야 함)."""
    converted: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }
            if m.get("is_error"):
                block["is_error"] = True
            prev = converted[-1] if converted else None
            if prev and prev["role"] == "user" and prev.get("_tool_results"):
                prev["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block], "_tool_results": True})
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for call in m["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": call.get("arguments") or {},
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue
        converted.append({"role": role, "content": _to_anthropic_content(m["content"])})
    for msg in converted:
        msg.pop("_tool_results", None)
    return converted


def _prepare_anthropic_messages(
    messages: list[dict],
    reference_block_count: int | None = None,
    history_len: int | None = None,
) -> tuple[list[str], list[dict]]:
    """system 텍스트 분리 + content 변환 + 캐싱까지 한 번에 처리.

    history_len: messages 중 앞에서부터 몇 개가 "이번 턴 툴 루프 전의 원래
    히스토리"(이번 사용자 질문까지)인지. 캐시 브레이크포인트(직전 턴 끝)는
    이 구간 안에서만 찍어야 한다 - 루프 메시지까지 포함해 [-2]를 잡으면
    회차마다 위치가 바뀌어 캐시가 깨진다. None이면 전체가 히스토리."""
    if history_len is None:
        history_len = len(messages)
    system_texts = [m["content"] for m in messages if m["role"] == "system"]
    base = _to_anthropic_chat_messages(messages[:history_len])
    _apply_anthropic_cache_control(base, reference_block_count)
    loop_part = _to_anthropic_chat_messages(messages[history_len:])
    return system_texts, base + loop_part


def _prepare_openai_messages(messages: list[dict], company: Company) -> list[dict]:
    """중립 메시지를 OpenAI Chat Completions 포맷으로 변환(Google 호환 엔드포인트
    공용). Google은 툴 호출 응답에 실려 온 extra_content(thought_signature)를
    assistant tool_calls의 해당 호출에 그대로 되돌려 보내야 한다 - 빼면
    "Function call is missing a thought_signature" 400이 남(실측)."""
    converted: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            content = m["content"]
            if m.get("is_error"):
                content = f"[툴 실행 오류] {content}"
            converted.append(
                {"role": "tool", "tool_call_id": m["tool_call_id"], "content": content}
            )
            continue
        if role == "assistant" and m.get("tool_calls"):
            tool_calls = []
            for call in m["tool_calls"]:
                entry = {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                extra = (call.get("provider_extra") or {}).get("extra_content")
                if company == "google" and extra:
                    entry["extra_content"] = extra
                tool_calls.append(entry)
            converted.append(
                {"role": "assistant", "content": m.get("content") or None, "tool_calls": tool_calls}
            )
            continue
        converted.append({"role": role, "content": _to_openai_content(m["content"])})
    return converted
