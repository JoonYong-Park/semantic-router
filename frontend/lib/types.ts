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

// --- 개인 지침(설정) ---

export interface Settings {
  personal_instruction: string | null;
  imported_memory: string | null;
}

export interface SettingsUpdateBody {
  personal_instruction?: string;
}

// --- 토큰 사용량 ---

export type UsagePeriod = "today" | "week" | "month" | "year";

export interface UsageQuota {
  limit: number;
  used: number;
  remaining: number;
  percent: number;
  cycle_start: string;
  cycle_end: string;
  days_left: number;
}

export interface UsageChartPoint {
  label: string;
  full_label: string;
  input: number;
  output: number;
}

export interface UsageByModel {
  model: string;
  calls: number;
  input: number;
  output: number;
  total: number;
}

export interface UsageStats {
  period: UsagePeriod;
  range_label: string;
  summary: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
  };
  chart: UsageChartPoint[];
  by_model: UsageByModel[];
}

// --- 대화 검색 ---

export interface SearchResult {
  conversation_id: string;
  title: string | null;
  snippet: string;
  matched_role: "user" | "assistant";
}

export type StreamEvent =
  | { type: "meta"; model: string; category: string | null; complexity: number | null }
  | { type: "chunk"; content: string }
  | { type: "done" }
  | { type: "error"; message: string };
