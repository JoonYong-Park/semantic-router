"""ToolSpec 목록을 회사별 tools 파라미터 스키마로 변환."""

from app.routing.catalog import Company


def build_tools_for_company(company: Company, tool_specs: list) -> list[dict]:
    """tools/base.py의 ToolSpec 목록을 회사별 tools 스키마로 변환만 한다(순환
    import를 피하려고 스펙은 호출부가 넘김). OpenAI는 Responses API의 평평한
    {"type":"function","name",...}, Google(OpenAI 호환)은 {"type":"function","function":{...}}, Anthropic은 function 래퍼 없이
    {"name","description","input_schema"}. strict는 스펙에 켜져 있을 때 OpenAI와
    Anthropic에만 싣는다 - Google 호환 엔드포인트의 strict 지원은 미검증이라 안 보냄."""
    if company == "anthropic":
        converted = []
        for t in tool_specs:
            if t.strict:
                # strict는 모든 객체에 additionalProperties:false가 필요해서 그대로 보낸다.
                tool = {"name": t.name, "description": t.description,
                        "input_schema": t.parameters, "strict": True}
            else:
                # 예전 Anthropic 요청과 동일하게 additionalProperties는 빼고 보낸다.
                schema = {k: v for k, v in t.parameters.items() if k != "additionalProperties"}
                tool = {"name": t.name, "description": t.description, "input_schema": schema}
            converted.append(tool)
        return converted

    if company == "openai":
        # Responses API 포맷 - function 래퍼 없이 평평하게 둔다.
        converted = []
        for t in tool_specs:
            tool = {"type": "function", "name": t.name, "description": t.description,
                    "parameters": t.parameters}
            if t.strict:
                tool["strict"] = True
            else:
                # strict가 아니면 명시적으로 꺼야 한다(Responses API는 기본값이 strict).
                tool["strict"] = False
            converted.append(tool)
        return converted

    # google - OpenAI 호환 Chat Completions 포맷
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tool_specs
    ]
