import {
  AttachmentOut,
  ChatRequestBody,
  ChatResponseBody,
  Conversation,
  DBMessage,
  MemoryOverview,
  MemoryTopic,
  MemoryTopicUpdate,
  Project,
  ProjectColor,
  ProjectFilesOut,
  SearchResult,
  SendMessageBody,
  Settings,
  SettingsUpdateBody,
  UsagePeriod,
  UsageQuota,
  UsageStats,
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

export async function updateConversation(
  id: string,
  body: { title?: string; pinned?: boolean; sort_order?: number }
): Promise<Conversation> {
  const res = await fetch(`${API_URL}/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`채팅을 수정하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateConversationProject(
  id: string,
  projectId: string | null
): Promise<Conversation> {
  const res = await fetch(`${API_URL}/conversations/${id}/project`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) throw new Error(`채팅을 프로젝트에 연결하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 개인 지침(설정) ---

export async function getSettings(): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings`);
  if (!res.ok) throw new Error(`설정을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateSettings(body: SettingsUpdateBody): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`설정을 저장하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteInstruction(): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings/instruction`, { method: "DELETE" });
  if (!res.ok) throw new Error(`개인 지침을 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function importMemory(text: string): Promise<Settings> {
  const res = await fetch(`${API_URL}/settings/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`가져오기에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteImportedMemory(): Promise<{ imported_memory: string | null }> {
  const res = await fetch(`${API_URL}/settings/import`, { method: "DELETE" });
  if (!res.ok) throw new Error(`가져온 정보를 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 자동 메모리 추출 ---

export async function extractMemoryAll(): Promise<{ status: string; count: number }> {
  const res = await fetch(`${API_URL}/memory/extract-all`, { method: "POST" });
  if (!res.ok) throw new Error(`메모리 업데이트에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 메모리 조회(전역 + 프로젝트별) ---

export async function getMemoryOverview(): Promise<MemoryOverview> {
  const res = await fetch(`${API_URL}/settings/memory`);
  if (!res.ok) throw new Error(`메모리를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteMemoryFact(memoryId: string): Promise<void> {
  const res = await fetch(`${API_URL}/settings/memory/${memoryId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`메모리를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function updateMemoryTopic(
  memoryId: string,
  payload: MemoryTopicUpdate
): Promise<MemoryTopic> {
  const res = await fetch(`${API_URL}/settings/memory/${memoryId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`메모리를 수정하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 프로젝트 폴더 ---

export async function listProjects(): Promise<Project[]> {
  const res = await fetch(`${API_URL}/projects`);
  if (!res.ok) throw new Error(`프로젝트 목록을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function createProject(name: string, instruction?: string): Promise<Project> {
  const res = await fetch(`${API_URL}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, instruction }),
  });
  if (!res.ok) throw new Error(`프로젝트를 만들지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function getProject(id: string): Promise<Project> {
  const res = await fetch(`${API_URL}/projects/${id}`);
  if (!res.ok) throw new Error(`프로젝트를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateProject(
  id: string,
  body: {
    name?: string;
    instruction?: string;
    pinned?: boolean;
    sort_order?: number;
    color?: ProjectColor;
  }
): Promise<Project> {
  const res = await fetch(`${API_URL}/projects/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`프로젝트를 수정하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// 프로젝트를 지우면 소속 채팅방(+ 그 메시지/첨부)/참고자료/프로젝트 메모리가
// 전부 같이 지워진다(백엔드 FK CASCADE + delete_chat_upload_dir 정리, 되돌릴 수 없음).
export async function deleteProject(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/projects/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`프로젝트를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function listProjectFiles(projectId: string): Promise<ProjectFilesOut> {
  const res = await fetch(`${API_URL}/projects/${projectId}/files`);
  if (!res.ok) throw new Error(`참고자료 목록을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

/**
 * uploadAttachment()와 동일하게 XHR로 업로드 진행률을 받는다.
 */
export function uploadProjectFile(
  projectId: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<ProjectFilesOut["files"][number]> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/projects/${projectId}/files`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("서버 응답을 해석하지 못했습니다."));
        }
      } else {
        let detail = `업로드 실패 (HTTP ${xhr.status})`;
        try {
          const payload = JSON.parse(xhr.responseText);
          if (typeof payload?.detail === "string") detail = payload.detail;
        } catch {
          // 응답이 JSON이 아니면 기본 메시지 유지
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("서버에 연결할 수 없습니다."));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export async function deleteProjectFile(projectId: string, fileId: string): Promise<void> {
  const res = await fetch(`${API_URL}/projects/${projectId}/files/${fileId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`참고자료를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function extractProjectMemoryAll(
  projectId: string
): Promise<{ status: string; count: number }> {
  const res = await fetch(`${API_URL}/projects/${projectId}/memory/extract-all`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`메모리 업데이트에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 토큰 사용량 ---

export async function getUsageQuota(): Promise<UsageQuota> {
  const res = await fetch(`${API_URL}/usage/quota`);
  if (!res.ok) throw new Error(`토큰 사용량을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function getUsageStats(period: UsagePeriod): Promise<UsageStats> {
  const res = await fetch(`${API_URL}/usage/stats?period=${period}`);
  if (!res.ok) throw new Error(`사용량 통계를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 첨부파일 ---

/**
 * XMLHttpRequest로 업로드 진행률(onProgress)을 받는다 - fetch는 업로드
 * 진행률 이벤트를 지원하지 않아서 이 함수만 예외적으로 XHR을 쓴다.
 */
export function uploadAttachment(
  conversationId: string,
  file: File,
  onProgress?: (percent: number) => void
): Promise<AttachmentOut> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/conversations/${conversationId}/attachments`);

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText));
        } catch {
          reject(new Error("서버 응답을 해석하지 못했습니다."));
        }
      } else {
        let detail = `업로드 실패 (HTTP ${xhr.status})`;
        try {
          const payload = JSON.parse(xhr.responseText);
          if (typeof payload?.detail === "string") detail = payload.detail;
        } catch {
          // 응답이 JSON이 아니면 기본 메시지 유지
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("서버에 연결할 수 없습니다."));

    const formData = new FormData();
    formData.append("file", file);
    xhr.send(formData);
  });
}

export async function deleteAttachment(
  conversationId: string,
  attachmentId: string
): Promise<void> {
  const res = await fetch(
    `${API_URL}/conversations/${conversationId}/attachments/${attachmentId}`,
    { method: "DELETE" }
  );
  if (!res.ok && res.status !== 404) {
    throw new Error(`첨부파일을 취소하지 못했습니다 (HTTP ${res.status})`);
  }
}

// --- 첨부파일 미리보기(뷰어) ---
// <img src>/<iframe src>에 아래 URL들을 직접 넣는 구조 (인증 헤더 없음, CORS 제한 없음).
// kind로 채팅 첨부(/conversations/{parentId}/attachments/{itemId}/...)와 프로젝트
// 참고자료(/projects/{parentId}/files/{itemId}/...) 양쪽 경로를 공유해서 쓴다 -
// 백엔드가 두 경로 다 동일한 4종(file/table/text/preview-pdf) 엔드포인트를
// 제공하므로, 프론트도 하나의 뷰어 컴포넌트/API로 재사용한다.
export type AttachmentKind = "chat" | "project";

function attachmentBaseUrl(kind: AttachmentKind, parentId: string, itemId: string): string {
  return kind === "chat"
    ? `${API_URL}/conversations/${parentId}/attachments/${itemId}`
    : `${API_URL}/projects/${parentId}/files/${itemId}`;
}

export function attachmentFileUrl(
  kind: AttachmentKind,
  parentId: string,
  itemId: string,
  download = false
): string {
  const base = `${attachmentBaseUrl(kind, parentId, itemId)}/file`;
  return download ? `${base}?download=1` : base;
}

export function attachmentPreviewPdfUrl(
  kind: AttachmentKind,
  parentId: string,
  itemId: string
): string {
  return `${attachmentBaseUrl(kind, parentId, itemId)}/preview-pdf`;
}

/**
 * DOCX/PPTX 미리보기 PDF를 받아온다. iframe src에 URL을 바로 넣으면 변환
 * 실패(422) 시 에러 JSON이 그대로 프레임에 노출되므로, fetch로 먼저 성공
 * 여부를 확인하고 성공하면 blob URL을 iframe에 넘긴다.
 */
export async function fetchAttachmentPreviewPdf(
  kind: AttachmentKind,
  parentId: string,
  itemId: string
): Promise<string> {
  const res = await fetch(attachmentPreviewPdfUrl(kind, parentId, itemId));
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : `미리보기를 만들지 못했습니다 (HTTP ${res.status})`
    );
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export interface AttachmentTable {
  sheets: {
    name: string;
    rows: string[][];
    total_rows: number;
    truncated: boolean;
  }[];
}

export async function getAttachmentTable(
  kind: AttachmentKind,
  parentId: string,
  itemId: string
): Promise<AttachmentTable> {
  const res = await fetch(`${attachmentBaseUrl(kind, parentId, itemId)}/table`);
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `표를 불러오지 못했습니다 (HTTP ${res.status})`);
  }
  return res.json();
}

export interface AttachmentTextPreview {
  text: string;
  truncated: boolean;
}

export async function getAttachmentText(
  kind: AttachmentKind,
  parentId: string,
  itemId: string
): Promise<AttachmentTextPreview> {
  const res = await fetch(`${attachmentBaseUrl(kind, parentId, itemId)}/text`);
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `텍스트를 불러오지 못했습니다 (HTTP ${res.status})`);
  }
  return res.json();
}

// --- 대화 검색 ---

export async function searchConversations(query: string): Promise<SearchResult[]> {
  const res = await fetch(`${API_URL}/search?query=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error(`검색에 실패했습니다 (HTTP ${res.status})`);
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
    onSearching: (query: string) => void;
    onProcessing: (message: string) => void;
    onFile: (attachment: AttachmentOut) => void;
    onChunk: (content: string) => void;
    onDone: () => void;
    onError: (message: string) => void;
  },
  // 사용자가 스트리밍 도중 다른 채팅방으로 넘어가는 것 자체는 허용하되
  // (더 이상 안 막음), 그 화면을 완전히 떠나거나 다른 채팅으로 바꿀 때
  // 더는 아무도 안 보는 응답을 서버에 계속 만들게 두지 않도록 호출
  // 쪽에서 끊을 수 있게 둔다.
  signal?: AbortSignal
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
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
    let done: boolean;
    let value: Uint8Array | undefined;
    try {
      ({ done, value } = await reader.read());
    } catch (err) {
      // 호출 쪽이 signal.abort()로 끊은 경우(다른 채팅으로 이동 등) - 아무도
      // 더 안 보는 응답이니 에러로 취급하지 않고 조용히 중단한다.
      if (err instanceof DOMException && err.name === "AbortError") return;
      throw err;
    }
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
      } else if (data.type === "searching") {
        handlers.onSearching(data.query as string);
      } else if (data.type === "processing") {
        handlers.onProcessing(data.message as string);
      } else if (data.type === "file") {
        handlers.onFile(data.attachment as AttachmentOut);
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
