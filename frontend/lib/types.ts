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
  searchingQuery?: string | null; // 웹 검색 중일 때만 값이 있고, 첫 chunk가 오면 비움
  processingMessage?: string | null; // 첨부파일 백그라운드 처리 대기 중일 때만 값이 있고, 첫 chunk가 오면 비움
  attachments?: AttachmentOut[]; // 이 메시지에 첨부된 파일 - 전송 후에도 계속 표시
}

// --- 채팅방(대화방) ---

export interface Conversation {
  id: string;
  title: string | null;
  updated_at: string;
  project_id: string | null;
  pinned: boolean;
  sort_order: number;
}

export interface DBMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  model_used: string | null;
  task_category: string | null;
  complexity_score: number | null;
  created_at: string;
  attachments: AttachmentOut[];
}

export interface SendMessageBody {
  content: string;
  mode: Mode;
  size?: Size;
  attachment_ids?: string[];
}

// --- 첨부파일 ---

export interface AttachmentOut {
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  extraction_status: "pending" | "done" | "failed";
  created_at: string;
}

export type AttachmentStatus = "uploading" | "ready" | "error";

export interface PendingAttachment {
  id: string; // 업로드 완료 전엔 로컬 임시 id, 완료 후 서버가 준 attachment_id로 교체
  file: File;
  status: AttachmentStatus;
  progress?: number;
  error?: string;
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

// --- 프로젝트 폴더 ---

export interface Project {
  id: string;
  name: string;
  instruction: string | null;
  max_capacity_bytes: number;
  pinned: boolean;
  sort_order: number;
  color: ProjectColor;
  created_at: string;
  updated_at: string;
}

export type ProjectColor =
  | "default"
  | "red"
  | "orange"
  | "yellow"
  | "green"
  | "blue"
  | "purple"
  | "pink";

export interface ProjectFile {
  id: string;
  original_name: string;
  mime_type: string;
  size_bytes: number;
  extraction_status: "pending" | "done" | "failed";
  created_at: string;
}

export interface ProjectFilesOut {
  files: ProjectFile[];
  used_bytes: number;
  max_bytes: number;
  percent: number;
}

// --- 메모리 조회(전역: 카테고리 → 토픽, 프로젝트: 토픽) ---

export type MemoryCategory = "나" | "주제" | "영역" | "사람";

export interface MemoryTopic {
  id: string;
  name: string;
  summary: string;
  details: string[];
  updated_at: string;
}

export interface MemoryCategoryGroup {
  category: MemoryCategory;
  topics: MemoryTopic[];
}

export interface ProjectMemoryGroup {
  project_id: string;
  project_name: string;
  topics: MemoryTopic[];
}

export interface MemoryOverview {
  global: MemoryCategoryGroup[];
  projects: ProjectMemoryGroup[];
}

export interface MemoryTopicUpdate {
  summary: string;
  details: string[];
}

export type StreamEvent =
  | { type: "meta"; model: string; category: string | null; complexity: number | null }
  | { type: "searching"; query: string }
  | { type: "processing"; message: string }
  | { type: "chunk"; content: string }
  | { type: "done" }
  | { type: "error"; message: string };
