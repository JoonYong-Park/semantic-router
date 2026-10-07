"""한 턴의 SSE 이벤트 버퍼(TurnStream)와 진행 중인 턴 레지스트리."""

import asyncio
import json
import logging
from uuid import UUID



logger = logging.getLogger("uvicorn.error")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class TurnStream:
    """한 턴의 응답 생성 진행 상황. 응답 생성은 브라우저 연결과 분리된 백그라운드
    태스크가 끝까지 수행하고(채팅 이동/새로고침으로 연결이 끊겨도 계속 생성·저장),
    SSE는 여기 쌓인 이벤트를 처음부터 다시 받거나(재접속) 이어서 받기만 한다.
    구독자가 끊겨도 DB 작업이 중간에 취소되지 않으므로, 취소 시점에 따라
    트랜잭션이 열린 채 남아 잠금을 쥐던 문제도 생기지 않는다."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self.task: asyncio.Task | None = None
        self._cond = asyncio.Condition()

    async def emit(self, event: dict) -> None:
        async with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    async def finish(self) -> None:
        async with self._cond:
            self.done = True
            self._cond.notify_all()

    async def subscribe(self):
        sent = 0
        while True:
            async with self._cond:
                while sent >= len(self.events) and not self.done:
                    await self._cond.wait()
                batch = self.events[sent:]
                sent = len(self.events)
                finished = self.done
            for event in batch:
                yield _sse(event)
            if finished:
                return


# 진행 중인 턴 - 대화방당 하나. 단일 uvicorn 프로세스 메모리에만 있다(워커를
# 여러 개로 늘리면 재접속이 다른 프로세스로 갈 수 있어 공유 저장소가 필요함).
_active_turns: dict[UUID, TurnStream] = {}
