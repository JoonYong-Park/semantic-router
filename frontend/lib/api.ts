import {
  ChatRequestBody,
  ChatResponseBody,
  Conversation,
  DBMessage,
  SendMessageBody,
  Settings,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ChatApiError extends Error {
  selectedModel?: string | null;
  taskCategory?: string | null;
  complexityScore?: number | null;

  constructor(
    message: string,
    selectedModel?: string | null,
    taskCategory?: string | null,
    complexityScore?: number | null
  ) {
    super(message);
    this.name = "ChatApiError";
    this.selectedModel = selectedModel;
    this.taskCategory = taskCategory;
    this.complexityScore = complexityScore;
  }
}

export async function sendChat(body: ChatRequestBody): Promise<ChatResponseBody> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    if (detail && typeof detail === "object") {
      throw new ChatApiError(
        detail.message ?? `요청 실패 (HTTP ${res.status})`,
        detail.selected_model,
        detail.task_category,
        detail.complexity_score
      );
    }
    throw new ChatApiError(
      typeof detail === "string" ? detail : `요청 실패 (HTTP ${res.status})`
    );
  }

  return res.json();
}

// --- 채팅방(대화방) ---

export async function listConversations(): Promise<Conversation[]> {
  const res = await fetch(`${API_URL}/conversations`);
  if (!res.ok) throw new Error(`채팅방 목록을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function createConversation(): Promise<Conversation> {
  const res = await fetch(`${API_URL}/conversations`, { method: "POST" });
  if (!res.ok) throw new Error(`채팅방을 만들지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/conversations/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`채팅방을 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function getConversationMessages(id: string): Promise<DBMessage[]> {
  const res = await fetch(`${API_URL}/conversations/${id}/messages`);
  if (!res.ok) throw new Error(`메시지를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 개인 지침(설정) ---

export async function getSettings(): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings`);
  if (!res.ok) throw new Error(`설정을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateSettings(instruction: string): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ personal_instruction: instruction }),
  });
  if (!res.ok) throw new Error(`설정을 저장하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteInstruction(): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings/instruction`, { method: "DELETE" });
  if (!res.ok) throw new Error(`개인 지침을 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

/**
 * SSE 스트리밍 전송. fetch의 ReadableStream을 직접 읽어서 파싱한다
 * (EventSource는 POST 바디를 못 보내서 못 씀).
 * 이벤트는 "\n\n"으로 구분되므로, 청크 경계에 걸쳐 잘린 이벤트는
 * buffer에 남겨뒀다가 다음 read()에서 이어붙인다.
 */
export async function streamMessage(
  conversationId: string,
  body: SendMessageBody,
  handlers: {
    onMeta: (meta: { model: string; category: string | null; complexity: number | null }) => void;
    onChunk: (content: string) => void;
    onDone: () => void;
    onError: (message: string) => void;
  }
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    handlers.onError("서버에 연결할 수 없습니다.");
    return;
  }

  if (!res.ok || !res.body) {
    const payload = await res.json().catch(() => null);
    const detail = payload?.detail;
    handlers.onError(
      typeof detail === "string" ? detail : `요청 실패 (HTTP ${res.status})`
    );
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const rawEvent of events) {
      const dataLine = rawEvent.split("\n").find((l) => l.startsWith("data: "));
      if (!dataLine) continue;

      let data: { type: string; [key: string]: unknown };
      try {
        data = JSON.parse(dataLine.slice("data: ".length));
      } catch {
        continue;
      }

      if (data.type === "meta") {
        handlers.onMeta({
          model: data.model as string,
          category: (data.category as string | null) ?? null,
          complexity: (data.complexity as number | null) ?? null,
        });
      } else if (data.type === "chunk") {
        handlers.onChunk(data.content as string);
      } else if (data.type === "done") {
        handlers.onDone();
      } else if (data.type === "error") {
        handlers.onError(data.message as string);
      }
    }
  }
}
