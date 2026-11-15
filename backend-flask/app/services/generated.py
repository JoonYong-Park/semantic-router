"""모델이 툴로 만든 파일(문서, 이미지, 수정본) 저장 (기존 attachments/service.save_generated_file).

chats/{cid}/generated/ 아래에 저장하고 첨부 아이템(source="generated")으로 등록한다.
메시지 연결(message_id)은 assistant 메시지를 저장할 때 툴 루프가 한다. 그 전에
연결이 끊기면 7일 TTL로 정리된다 (repo.conversations.create_attachment 참고).
"""

from app import storage
from app.db.repo import conversations as conv_repo
from app.services import files


async def save_generated_file(uid, chat_id, display_name: str, ext: str, content: bytes,
                              generation_meta: dict, source_attachment_id=None) -> dict:
    if len(content) > files.MAX_GENERATED_BYTES:
        raise ValueError(
            f"생성된 파일이 너무 큽니다 ({len(content) // (1024 * 1024)}MB, "
            f"최대 {files.MAX_GENERATED_BYTES // (1024 * 1024)}MB)."
        )
    aid, key = files.new_file_key(f"{files.chat_dir(chat_id)}/generated", ext)
    storage.write(key, content)
    extra = {"generation_meta": generation_meta}
    if source_attachment_id:
        extra["source_attachment_id"] = str(source_attachment_id)
    return conv_repo.create_attachment(
        uid, chat_id, display_name, key, files.MIME_BY_EXT.get(ext, "application/octet-stream"),
        len(content), source="generated", extraction_status="done", aid=aid, **extra,
    )
