import { ChatRequestBody, ChatResponseBody } from "./types";

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
