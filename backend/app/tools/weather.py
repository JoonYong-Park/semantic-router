"""get_weather 툴 - Open-Meteo(무료, API 키 없음)로 현재 날씨와 예보를 가져온다.

웹 검색으로 날씨를 찾으면 검색 엔진이 저장해 둔 오래된 페이지(며칠~몇 주 전)나 인코딩이 깨진
기상청 페이지가 걸려 "지금 몇 도"에 틀리거나 답하지 못했다. 날씨는 이 툴이 실시간 예보 API를 직접 부른다.

위치: Open-Meteo 지명 검색은 한글 지명을 잘 못 찾는다(부산 → 충주의 마을, 판교 → 북한).
그래서 모델이 지명과 함께 대략의 위도/경도를 넘긴다. 날씨는 동네 수준이면 충분하다.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.tools.base import ToolContext, ToolResult, ToolSpec

logger = logging.getLogger("uvicorn.error")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
KST = ZoneInfo("Asia/Seoul")
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HOURS_AHEAD = 12

# WMO 날씨 코드 → 한국어 (https://open-meteo.com/en/docs 의 weather_code)
WEATHER_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "구름 조금", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비", 56: "약한 어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "약한 어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기", 85: "약한 소낙눈", 86: "소낙눈",
    95: "뇌우", 96: "약한 우박 동반 뇌우", 99: "우박 동반 뇌우",
}


def _desc(code) -> str:
    return WEATHER_CODES.get(int(code), f"날씨 코드 {code}") if code is not None else "-"


async def _execute_get_weather(arguments: dict, ctx: ToolContext) -> ToolResult:
    name = str(arguments.get("location") or "").strip() or "요청한 위치"
    try:
        lat = float(arguments["latitude"])
        lon = float(arguments["longitude"])
    except (KeyError, TypeError, ValueError):
        return ToolResult(content="latitude/longitude가 숫자가 아니라 날씨를 조회하지 못했습니다.", is_error=True)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ToolResult(content="위도/경도 범위가 잘못됐습니다.", is_error=True)
    days = min(max(int(arguments.get("days") or 1), 1), 7)

    ctx.emit({"type": "processing", "message": f"날씨 확인 중: {name}"})
    params = {
        "latitude": lat, "longitude": lon, "timezone": "Asia/Seoul", "forecast_days": max(days, 2),
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "wind_speed_unit": "ms",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(FORECAST_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("날씨 조회 실패 (%s, %s): %s", lat, lon, exc)
        return ToolResult(
            content=f"날씨 API 호출에 실패했습니다: {type(exc).__name__}. 날씨를 확인하지 못했다고 사용자에게 알려줘.",
            is_error=True,
        )

    cur = data.get("current", {})
    lines = [
        f"[날씨: {name} (위도 {lat:.3f}, 경도 {lon:.3f}) / Open-Meteo 예보 모델]",
        f"기준 시각: {cur.get('time', '-')} (KST)",
        f"현재: {_desc(cur.get('weather_code'))}, 기온 {cur.get('temperature_2m')}°C "
        f"(체감 {cur.get('apparent_temperature')}°C), 습도 {cur.get('relative_humidity_2m')}%, "
        f"바람 {cur.get('wind_speed_10m')} m/s, 강수량 {cur.get('precipitation')} mm",
    ]

    hourly = data.get("hourly", {})
    now = datetime.now(KST).strftime("%Y-%m-%dT%H:00")
    times = hourly.get("time", [])
    start = next((i for i, t in enumerate(times) if t >= now), 0)
    hour_rows = []
    for i in range(start, min(start + HOURS_AHEAD, len(times))):
        hour_rows.append(
            f"{times[i][11:16]} {_desc(hourly['weather_code'][i])} {hourly['temperature_2m'][i]}°C "
            f"강수확률 {hourly['precipitation_probability'][i]}%"
        )
    if hour_rows:
        lines.append(f"앞으로 {len(hour_rows)}시간: " + " / ".join(hour_rows))

    daily = data.get("daily", {})
    for i, day in enumerate(daily.get("time", [])[:days]):
        lines.append(
            f"{day}: {_desc(daily['weather_code'][i])}, 최고 {daily['temperature_2m_max'][i]}°C / "
            f"최저 {daily['temperature_2m_min'][i]}°C, 강수확률 최대 {daily['precipitation_probability_max'][i]}%"
        )
    lines.append(
        "위 값으로 답해. 출처는 URL 목록 대신 '날씨 정보: Open-Meteo 예보' 한 줄만 적어. "
        "기상청 공식 특보나 예보가 필요한 질문이면 기상청 확인을 함께 권해."
    )
    return ToolResult(content="\n".join(lines))


GET_WEATHER = ToolSpec(
    name="get_weather",
    description=(
        "현재 날씨, 기온, 강수확률, 오늘~며칠 뒤 예보가 필요할 때 쓴다. 날씨 질문은 web_search 대신 이 툴을 쓴다. "
        "위치는 지명과 함께 그 장소의 대략적인 위도/경도를 직접 넣는다 "
        "(예: 서울 강남구 역삼동 → 37.50, 127.04 / 부산 해운대 → 35.16, 129.16). "
        "위치를 말하지 않으면 서울(37.57, 126.98)로 본다."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "답변에 쓸 지명 (예: 서울 강남구 역삼동)"},
            "latitude": {"type": "number", "description": "위도 (소수)"},
            "longitude": {"type": "number", "description": "경도 (소수)"},
            "days": {"type": "integer", "description": "일별 예보 일수 1~7. 오늘만 물으면 1, 주말·이번 주면 그에 맞게"},
        },
        "required": ["location", "latitude", "longitude"],
        "additionalProperties": False,
    },
    executor=_execute_get_weather,
)
