"""API 응답 모양. 기존 backend/app/schemas.py의 *Out 스키마와 키를 똑같이 맞춘다
(프론트 frontend/lib/types.ts가 그 모양을 기준으로 짜여 있어서).

DynamoDB는 None을 저장하지 않으므로, 빠진 선택 필드는 여기서 None으로 채운다.
"""


def conversation_out(c: dict) -> dict:
    return {
        "id": c["id"],
        "title": c.get("title"),
        "updated_at": c["updated_at"],
        "project_id": c.get("project_id"),
        "pinned": bool(c.get("pinned", False)),
        "sort_order": int(c.get("sort_order", 0)),
    }


def attachment_out(a: dict) -> dict:
    return {
        "id": a["id"],
        "original_name": a["original_name"],
        "mime_type": a["mime_type"],
        "size_bytes": int(a["size_bytes"]),
        "extraction_status": a["extraction_status"],
        "source": a.get("source", "upload"),
        "created_at": a["created_at"],
    }


def message_out(m: dict) -> dict:
    return {
        "id": m["id"],
        "role": m["role"],
        "content": m["content"],
        "model_used": m.get("model_used"),
        "task_category": m.get("task_category"),
        "complexity_score": m.get("complexity_score"),
        "created_at": m["created_at"],
        "attachments": [attachment_out(a) for a in m.get("attachments", [])],
    }


def project_out(p: dict) -> dict:
    return {
        "id": p["id"],
        "name": p["name"],
        "instruction": p.get("instruction"),
        "max_capacity_bytes": int(p["max_capacity_bytes"]),
        "pinned": bool(p.get("pinned", False)),
        "sort_order": int(p.get("sort_order", 0)),
        "color": p.get("color", "default"),
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
    }


def project_file_out(f: dict) -> dict:
    return {
        "id": f["id"],
        "original_name": f["original_name"],
        "mime_type": f["mime_type"],
        "size_bytes": int(f["size_bytes"]),
        "extraction_status": f["extraction_status"],
        "created_at": f["created_at"],
    }
