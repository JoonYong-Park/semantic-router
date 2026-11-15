"""한 턴의 SSE 이벤트 버퍼(TurnStream)와 진행 중인 턴 레지스트리 (기존 chat/stream.py).

기존은 asyncio.Condition이었고, Flask(동기 서버)에서는 응답 생성이 별도 스레드에서
돌기 때문에 threading.Condition으로 바꿨다. 동작은 같다: 생성 스레드는 브라우저 연결과
상관없이 끝까지 돌아 답을 저장하고, SSE 응답은 쌓인 이벤트를 처음부터 다시 받거나
(재접속) 이어서 받기만 한다.

Lambda로 갈 때는 프로세스 메모리에 둘 수 없으므로 이 부분을 "비동기 작업 + 폴링"으로
바꾼다 (docs/ko/AWS_MIGRATION_LOG.md 남은 일 6번).
"""

import json
import threading


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class TurnStream:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self._cond = threading.Condition()

    def emit(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def subscribe(self):
        sent = 0
        while True:
            with self._cond:
                while sent >= len(self.events) and not self.done:
                    self._cond.wait()
                batch = self.events[sent:]
                sent = len(self.events)
                finished = self.done
            for event in batch:
                yield sse(event)
            if finished:
                return


# 진행 중인 턴 - 대화방당 하나. 이 프로세스 메모리에만 있다.
active_turns: dict[str, TurnStream] = {}
active_turns_lock = threading.Lock()
