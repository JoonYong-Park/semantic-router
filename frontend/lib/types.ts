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
  selectedModel?: string | null;
  taskCategory?: string | null;
  complexityScore?: number | null;
  isError?: boolean;
}
