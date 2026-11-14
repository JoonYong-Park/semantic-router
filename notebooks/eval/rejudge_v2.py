"""평가셋 v2 라벨링: 추론 깊이 기준표로 100문항을 LLM judge가 2회 독립 채점한다.

backend 컨테이너 안에서 실행한다 (OPENAI_API_KEY 필요, 약 200회 호출).

    docker cp notebooks/eval backend:/tmp/eval
    docker cp notebooks/complexity/results/complexity_eval_results_final.csv backend:/tmp/eval/eval_v1.csv
    docker exec -w /app -e PYTHONPATH=/app backend python /tmp/eval/rejudge_v2.py /tmp/eval/eval_v1.csv /tmp/eval/rejudge_raw.json
    docker cp backend:/tmp/eval/rejudge_raw.json notebooks/eval/results/rejudge_raw.json
"""

import asyncio
import csv
import json
import re
import sys

from app.llm.client import call_model
from app.routing.catalog import get_model

RUBRIC = """당신은 질문 난이도 평가자다. 아래 기준표로 사용자 질문 하나를 채점한다.

[무엇을 평가하나]
좋은 답변을 만들기 위해 필요한 '추론의 깊이'만 본다.
- 주제가 전문적인지, 전문 용어가 들어있는지는 기준이 아니다. ("쿠버네티스가 뭐야?"는 쉽다)
- 질문 길이도 기준이 아니다. 짧아도 어려울 수 있다. ("P=NP야?"는 어렵다)

[점수]
1: 인사, 단일 사실·정의, 단순 변환(번역·맞춤법·짧은 문장 작성). 한 단계로 답이 나온다.
2: 기본 개념 설명이나 일상적 작업. 일반 지식 2~3개를 연결하면 된다.
3: 여러 개념을 비교·적용하거나 구조화된 설명이 필요하다. 중간 수준의 전문 지식.
4: 여러 제약 조건을 동시에 다루는 다단계 분석·설계·논증. 전문가 수준의 판단.
5: 엄밀한 증명, 미해결 문제의 정확한 논의, 고도의 전문 분석.

[정보가 부족한 질문]
"이 코드 고쳐줘", "예산안 짜줘"처럼 답할 대상(코드·문서·상황 등)이 질문에 없으면 needs_context=true로 표시한다.
이때 점수는 '빠진 정보가 평범한 수준으로 주어졌다고 가정했을 때' 그 작업의 전형적 난이도로 매긴다.
정보가 없다는 사실 자체를 이유로 점수를 올리지 마라.

[출력]
JSON 한 줄만 출력: {"score": 1~5 정수, "needs_context": true/false, "reason": "한 문장"}

질문: """

JUDGE = get_model("openai", "medium")


async def _judge_one(question: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for _ in range(3):
            raw = await call_model(JUDGE, RUBRIC + question)
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    d = json.loads(m.group(0))
                    int(d["score"])
                    return d
                except (ValueError, KeyError, TypeError):
                    pass
        return {"score": None, "needs_context": None, "reason": "parse_fail"}


async def main(src: str, dst: str) -> None:
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    sem = asyncio.Semaphore(6)
    runs = {}
    for run in (1, 2):
        runs[run] = await asyncio.gather(*[_judge_one(r["question"], sem) for r in rows])
    json.dump({"model": JUDGE.model_id, "runs": runs}, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    fails = sum(1 for run in runs.values() for d in run if d["score"] is None)
    print(f"done judge={JUDGE.model_id} parse_fail={fails}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
