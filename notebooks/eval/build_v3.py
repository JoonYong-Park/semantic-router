"""평가셋 v3: 실사용형(업무 요청 문체) 질문을 길이·난이도가 고르게 섞이도록 만든다.

v2 오류 분석에서 '30자 이상인데 쉬운 질문'이 0개라 라우터가 길이를 난이도로
배운 것을 발견했다(길이 편향). v3는 그 공백을 메우기 위한 셋이다.

1. LLM(GPT-6.1 Sol)이 6개 칸(짧음/김 × 쉬움/보통/어려움)마다 업무 요청을 만든다.
   만든 모델이 붙인 칸 이름은 라벨로 쓰지 않는다.
2. rejudge_v2.py와 같은 기준표로 judge가 2회 독립 채점한다. 이 점수가 라벨이다.
3. 무작위로 반씩 나눠 'aug'(참조 보강용)와 'test'(검증 전용)로 쓴다.
   test는 서비스 참조 데이터에 절대 넣지 않는다.

backend 컨테이너 안에서 실행 (OPENAI_API_KEY 필요):
    docker exec -w /app -e PYTHONPATH=/app backend python /tmp/eval/build_v3.py /tmp/eval/eval_set_v3.csv
"""

import asyncio
import csv
import json
import random
import re
import sys

from app.llm.client import call_model
from app.routing.catalog import get_model

sys.path.insert(0, "/tmp/eval")
from rejudge_v2 import RUBRIC  # noqa: E402

GEN = get_model("openai", "medium")
CELLS = {
    "short_easy": "15자 이하, 한 단계로 끝나는 쉬운 요청",
    "short_medium": "15자 이하, 개념 비교나 구조화된 설명이 필요한 요청",
    "short_hard": "15자 이하지만 다단계 추론이나 전문가 판단이 필요한 어려운 질문",
    "long_easy": "40~90자로 구체적 조건이 많지만 실제로는 정리·변환·작성처럼 쉬운 업무 요청",
    "long_medium": "40~90자, 여러 개념을 비교·적용하는 중간 난이도 업무 요청",
    "long_hard": "40~90자, 여러 제약을 동시에 다루는 다단계 분석·설계 요청",
}
PER_CELL = 24

GEN_PROMPT = """회사 임직원이 사내 AI 어시스턴트에게 보낼 법한 한국어 요청을 {n}개 만들어라.
조건: {desc}
- 업무 분야를 다양하게: 기획, 영업, 인사, 재무, 개발, 마케팅, CS, 법무, 물류, 일상 업무 등
- 서로 문체와 주제가 겹치지 않게
- 번호나 설명 없이 JSON 배열 한 줄로만 출력: ["요청1", "요청2", ...]"""


async def generate(cell: str, desc: str) -> list[str]:
    raw = await call_model(GEN, GEN_PROMPT.format(n=PER_CELL, desc=desc))
    m = re.search(r"\[.*\]", raw, re.S)
    items = json.loads(m.group(0)) if m else []
    return [str(x).strip() for x in items if str(x).strip()][:PER_CELL]


async def judge(q: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for _ in range(3):
            raw = await call_model(GEN, RUBRIC + q)
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    d = json.loads(m.group(0))
                    int(d["score"])
                    return d
                except (ValueError, KeyError, TypeError):
                    pass
        return {"score": None, "needs_context": None, "reason": "parse_fail"}


def tier(score: int) -> str:
    return "small" if score <= 2 else ("medium" if score == 3 else "large")


async def main(dst: str) -> None:
    gen = await asyncio.gather(*[generate(c, d) for c, d in CELLS.items()])
    rows = [{"question": q, "cell": c} for c, qs in zip(CELLS, gen) for q in qs]
    sem = asyncio.Semaphore(6)
    r1 = await asyncio.gather(*[judge(r["question"], sem) for r in rows])
    r2 = await asyncio.gather(*[judge(r["question"], sem) for r in rows])
    random.seed(42)
    for r, a, b in zip(rows, r1, r2):
        r.update(judge_run1=a["score"], judge_run2=b["score"], needs_context=a["needs_context"], reason=a["reason"])
        r["split"] = random.choice(["aug", "test"])
    rows = [r for r in rows if r["judge_run1"] is not None]
    for r in rows:
        r["tier"] = tier(int(r["judge_run1"]))
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["question", "cell", "split", "judge_run1", "judge_run2", "needs_context", "tier", "reason"])
        w.writeheader()
        w.writerows(rows)
    agree = sum(tier(int(r["judge_run1"])) == tier(int(r["judge_run2"])) for r in rows if r["judge_run2"] is not None)
    print(f"rows={len(rows)} judge tier agree={agree}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
