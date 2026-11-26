// "/"(새 채팅) 화면에서 첫 메시지를 보내면, 실제 전송(POST + SSE 수신)은
// 새로 만들어진 대화방 id로 곧장 이동한 "/c/{id}" 페이지가 담당한다.
// 그 사이(대화방 생성 직후 ~ /c/{id} 마운트) "보낼 내용"을 들고 넘어갈
// 자리가 필요한데, 두 페이지가 서로 다른 라우트 컴포넌트(= 서로 다른 React
// state)라 그냥 넘겨줄 방법이 없다. sessionStorage에 잠깐 맡겨두고(새로고침엔
// 살아남지만 이 탭을 벗어나면 사라짐), /c/{id}가 마운트 직후 한 번 읽고
// 바로 지운다 - 정상적인 "다른 채팅 클릭"/"새로고침" 흐름에서는 이 키가
// 존재하지 않으므로 평소엔 그냥 DB에서 메시지를 불러오는 경로를 탄다.
import { AttachmentOut, Mode, Size } from "./types";

export interface PendingFirstMessage {
  content: string;
  mode: Mode;
  size?: Size;
  attachmentIds: string[];
  attachmentsSnapshot: AttachmentOut[];
}

function storageKey(conversationId: string): string {
  return `pendingMessage:${conversationId}`;
}

export function stashPendingMessage(conversationId: string, payload: PendingFirstMessage): void {
  try {
    sessionStorage.setItem(storageKey(conversationId), JSON.stringify(payload));
  } catch {
    // sessionStorage가 막혀있으면(프라이빗 브라우징 등) 그냥 포기 - /c/{id}는
    // 이 경우 빈 메시지 목록으로 시작하고, 사용자가 메시지를 다시 입력해야 함
  }
}

export function popPendingMessage(conversationId: string): PendingFirstMessage | null {
  try {
    const raw = sessionStorage.getItem(storageKey(conversationId));
    if (!raw) return null;
    sessionStorage.removeItem(storageKey(conversationId));
    return JSON.parse(raw) as PendingFirstMessage;
  } catch {
    return null;
  }
}
