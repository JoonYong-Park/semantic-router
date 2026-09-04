export type Mode = "auto" | "openai" | "google" | "anthropic";
export type Size = "small" | "medium" | "large";

export interface ChatRequestBody {
  message: string;
  mode: Mode;
  size?: Size;
}

export interface ChatResponseBody {
  answer: string;
  selected_model: string;
  task_category?: string | null;
  complexity_score?: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string; // ISO - 날짜 구분선/호버 시간 표시용
  selectedModel?: string | null;
  taskCategory?: string | null;
  complexityScore?: number | null;
  isError?: boolean;
}

// --- 채팅방(대화방) ---

export interface Conversation {
  id: string;
  title: string | null;
  updated_at: string;
}

export interface DBMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  model_used: string | null;
  task_category: string | null;
  complexity_score: number | null;
  created_at: string;
}

export interface SendMessageBody {
  content: string;
  mode: Mode;
  size?: Size;
}

export type StreamEvent =
  | { type: "meta"; model: string; category: string | null; complexity: number | null }
  | { type: "chunk"; content: string }
  | { type: "done" }
  | { type: "error"; message: string };
