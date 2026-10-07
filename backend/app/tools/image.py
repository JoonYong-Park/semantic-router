"""이미지 생성 툴 (generate_image)."""

import asyncio
import io
import json
import logging
import time
from datetime import datetime

from app.llm.images import ImageContentPolicyError, ImageRateLimitError, generate_image
from app.tools.base import ToolContext, ToolResult, ToolSpec, _obj

logger = logging.getLogger("uvicorn.error")


# --- 이미지 생성 툴 ---

MAX_IMAGES_PER_TURN = 2
IMAGE_MODEL = "gpt-image-2.5-flare"
IMAGE_SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
IMAGE_QUALITIES = {"standard": "medium", "high": "high"}

GENERATE_IMAGE_PARAMS = _obj({
    "prompt": {
        "type": "string",
        "description": (
            "만들 이미지 설명. 영어 또는 한국어 모두 가능. 이미지 안에 넣을 글자가 있으면 "
            "따옴표로 정확히 명시. 실존 인물이나 상표/로고를 그리는 요청은 쓰지 말 것"
        ),
    },
    "aspect_ratio": {"type": "string", "enum": ["square", "landscape", "portrait"],
                     "description": "square=정사각형, landscape=가로형(슬라이드/배너), portrait=세로형(포스터)"},
    "quality": {"type": "string", "enum": ["standard", "high"],
                "description": "보통은 standard, 사용자가 고화질을 원할 때만 high"},
    "format": {"type": "string", "enum": ["png", "jpg"], "description": "파일 형식, 특별한 요청이 없으면 png"},
})

GENERATE_IMAGE_DESCRIPTION = (
    "사용자가 이미지, 그림, 일러스트, 썸네일, 배너, 포스터, 배경 이미지 등을 새로 만들어 달라고 할 때 이미지 1장을 생성한다. "
    "차트·표·도표는 이 툴이 아니라 문서 툴이나 채팅 답변으로 처리하고, 코드나 SVG를 요청한 경우에도 호출하지 않는다. "
    "한 번의 답변에서는 1장이 기본이며 사용자가 여러 장을 명시적으로 원할 때만 더 호출한다(최대 2장). "
    "quality는 사용자가 고화질/고해상도를 명시하지 않으면 항상 standard로 한다(high는 비용이 큼). "
    "만든 이미지는 사용자에게 카드로 바로 표시되므로 답변에 이미지 URL이나 마크다운 이미지 링크를 만들지 말고 "
    "어떤 이미지를 만들었는지 한두 문장으로만 안내한다."
)


def _encode_image(png_bytes: bytes, fmt: str) -> bytes:
    """API가 준 PNG를 Pillow로 열어 검증하고, jpg면 RGB로 변환해 저장한다."""
    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes))
    img.load()
    if fmt == "png":
        return png_bytes
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _execute_generate_image(arguments: dict, ctx: ToolContext) -> ToolResult:
    from app.attachments import service as attachment_service
    from app.schemas import AttachmentOut

    prompt = arguments.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return ToolResult(content="prompt가 비어 있습니다. 만들 이미지를 구체적으로 설명해 다시 호출하세요.", is_error=True)
    size = IMAGE_SIZES.get(arguments.get("aspect_ratio"))
    quality = IMAGE_QUALITIES.get(arguments.get("quality"))
    fmt = arguments.get("format")
    if size is None or quality is None or fmt not in ("png", "jpg"):
        return ToolResult(
            content=(
                "인자 검증 실패: aspect_ratio는 square/landscape/portrait, quality는 standard/high, "
                f"format은 png/jpg 중 하나여야 합니다 (받은 값: {arguments.get('aspect_ratio')!r}, "
                f"{arguments.get('quality')!r}, {fmt!r})."
            ),
            is_error=True,
        )

    count = ctx.turn_state.get("image_count", 0)
    if count >= MAX_IMAGES_PER_TURN:
        return ToolResult(
            content=f"한 번의 답변에서 이미지는 최대 {MAX_IMAGES_PER_TURN}장까지 만들 수 있습니다. 더 만들지 말고 만든 이미지를 안내하세요.",
            is_error=True,
        )
    model = IMAGE_MODEL
    ctx.emit({"type": "processing", "message": "이미지 생성 중..."})
    started = time.monotonic()
    try:
        png = await generate_image(model, prompt, size, quality)
    except ImageRateLimitError:
        return ToolResult(
            content="이미지 생성 요청이 많아 잠시 제한되었습니다(429). 이미지를 만들지 못했다고 알리고 잠시 후 다시 시도해 달라고 안내하세요.",
            is_error=True,
        )
    except ImageContentPolicyError as exc:
        return ToolResult(
            content=f"이미지 생성이 콘텐츠 정책에 따라 거부되었습니다. 사유: {exc}. 이 사유를 사용자에게 그대로 알리고, 다른 표현으로 요청해 달라고 안내하세요.",
            is_error=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("이미지 생성 실패 (model=%s, size=%s): %s", model, size, exc, exc_info=True)
        return ToolResult(
            content="이미지 생성 중 일시적인 오류가 발생했습니다. 이미지를 만들지 못했다고 알리고 잠시 후 다시 시도해 달라고 안내하세요.",
            is_error=True,
        )
    elapsed = time.monotonic() - started
    logger.info("이미지 생성 완료 (model=%s, size=%s, quality=%s, %.1fs)", model, size, quality, elapsed)

    data = await asyncio.to_thread(_encode_image, png, fmt)
    display_name = f"이미지_{datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')}.{fmt}"
    meta = {"kind": "image", "tool": "generate_image", "model": model, "size": size, "quality": quality, "format": fmt}
    try:
        attachment = await attachment_service.save_generated_file(ctx.conversation_id, display_name, fmt, data, meta)
    except ValueError as exc:  # 25MB 상한
        return ToolResult(content=str(exc), is_error=True)

    ctx.turn_state["image_count"] = count + 1
    ctx.emit({"type": "file", "attachment": AttachmentOut.model_validate(attachment).model_dump(mode="json")})
    payload = {
        "ok": True,
        "file_id": str(attachment.id),
        "filename": display_name,
        "size": size,
        "note": "이미지는 이미 사용자에게 카드로 표시됨. URL이나 이미지 링크를 만들지 말고 한두 문장으로만 안내할 것.",
    }
    return ToolResult(
        content=json.dumps(payload, ensure_ascii=False),
        metadata={"generated_attachment_id": attachment.id},
    )


GENERATE_IMAGE = ToolSpec(
    name="generate_image",
    description=GENERATE_IMAGE_DESCRIPTION,
    parameters=GENERATE_IMAGE_PARAMS,
    executor=_execute_generate_image,
    strict=True,
)
