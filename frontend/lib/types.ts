export type Mode = "gpt" | "gemini" | "claude" | "auto";

export interface ChatRequestBody {
  message: string;
  mode: Mode;
}

export interface ChatResponseBody {
  answer: string;
  selected_model: string;
  category?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  selectedModel?: string;
  category?: string | null;
  isError?: boolean;
}
