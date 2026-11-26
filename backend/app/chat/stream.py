"""한 턴의 SSE 이벤트 버퍼(TurnStream)와 진행 중인 턴 레지스트리 (기존 chat/stream.py).

기존은 asyncio.Condition이었고, Flask(동기 서버)에서는 응답 생성이 별도 스레드에서
돌기 때문에 threading.Condition으로 바꿨다. 동작은 같다: 생성 스레드는 브라우저 연결과
상관없이 끝까지 돌아 답을 저장하고, SSE 응답은 쌓인 이벤트를 처음부터 다시 받거나
(재접속) 이어서 받기만 한다.

Lambda에서는 응답 스트리밍 Lambda(joomini-api)가 이 경로를 받는다. 같은 실행 환경에 다시
붙어야 이어 받기가 되므로, 다른 실행 환경으로 간 재접속은 저장된 메시지를 다시 읽는 것으로 대신한다.

연결 중에 이벤트가 한동안 없으면(웹 검색, 문서 생성 등) 15초마다 SSE 주석(": ping")을 보낸다.
CloudFront와 API Gateway가 조용한 연결을 끊지 않게 하는 용도이고, 프론트는 data: 줄만 읽는다.
"""

import json
import threading

PING_SECONDS = 15


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class TurnStream:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.done = False
        self._cond = threading.Condition()
        # done 이후의 뒷정리(제목 생성)까지 끝났는지. Lambda가 이때까지 호출을 유지한다.
        self._settled = threading.Event()

    def emit(self, event: dict) -> None:
        with self._cond:
            self.events.append(event)
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self.done = True
            self._cond.notify_all()

    def wait_done(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self.done, timeout=timeout)

    def settle(self) -> None:
        self._settled.set()

    def wait_settled(self, timeout: float) -> bool:
        return self._settled.wait(timeout)

    def subscribe(self):
        sent = 0
        while True:
            with self._cond:
                if sent >= len(self.events) and not self.done:
                    self._cond.wait(timeout=PING_SECONDS)
                idle = sent >= len(self.events) and not self.done
                batch = self.events[sent:]
                sent = len(self.events)
                finished = self.done
            if idle:
                yield ": ping\n\n"
                continue
            for event in batch:
                yield sse(event)
            if finished:
                return


# 진행 중인 턴 - 대화방당 하나. 이 프로세스 메모리에만 있다.
active_turns: dict[str, TurnStream] = {}
active_turns_lock = threading.Lock()
