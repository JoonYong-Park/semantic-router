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
  User,
  UserUpdateBody,
} from "./types";

import { API_URL, apiFetch, getAccessToken, refreshAccessToken } from "./auth";
import { GUEST_READONLY_MESSAGE, isGuest } from "./guest";

/**
 * 업로드 진행률(onProgress)이 필요해 XHR을 쓴다(fetch는 업로드 진행률 이벤트가 없다).
 * apiFetch와 같이 Bearer 헤더를 붙이고, 401이면 토큰을 재발급해 한 번 다시 올린다.
 */
function xhrUpload<T>(url: string, file: File, onProgress?: (percent: number) => void): Promise<T> {
  const attempt = (token: string | null) =>
    new Promise<{ status: number; text: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
      xhr.onerror = () => reject(new Error("서버에 연결할 수 없습니다."));
      const formData = new FormData();
      formData.append("file", file);
      xhr.send(formData);
    });

  return (async () => {
    if (isGuest()) throw new Error(GUEST_READONLY_MESSAGE);
    let res = await attempt(await getAccessToken());
    if (res.status === 401) {
      const fresh = await refreshAccessToken();
      if (fresh) res = await attempt(fresh);
    }
    if (res.status >= 200 && res.status < 300) {
      try {
        return JSON.parse(res.text) as T;
      } catch {
        throw new Error("서버 응답을 해석하지 못했습니다.");
      }
    }
    let detail = `업로드 실패 (HTTP ${res.status})`;
    try {
      const payload = JSON.parse(res.text);
      if (typeof payload?.detail === "string") detail = payload.detail;
    } catch {
      // 응답이 JSON이 아니면 기본 메시지 유지
    }
    throw new Error(detail);
  })();
}

/**
 * 인증이 필요한 파일을 받아 blob URL로 돌려준다. <img>/<iframe>/<a href>는 Authorization
 * 헤더를 못 붙여서 파일 URL을 바로 넣지 않고 이걸 거친다. 다 쓰면 URL.revokeObjectURL.
 */
export async function fetchBlobUrl(url: string): Promise<string> {
  const res = await apiFetch(url);
  if (!res.ok) throw new Error(`파일을 불러오지 못했습니다 (HTTP ${res.status})`);
  return URL.createObjectURL(await res.blob());
}

/** 인증이 필요한 파일 다운로드. 파일명은 호출하는 쪽이 아는 원본 이름을 쓴다. */
export async function downloadAuthed(url: string, filename: string): Promise<void> {
  const blobUrl = await fetchBlobUrl(url);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
}

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
  const res = await apiFetch(`${API_URL}/chat`, {
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
  const res = await apiFetch(`${API_URL}/conversations`);
  if (!res.ok) throw new Error(`채팅방 목록을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function createConversation(): Promise<Conversation> {
  const res = await apiFetch(`${API_URL}/conversations`, { method: "POST" });
  if (!res.ok) throw new Error(`채팅방을 만들지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteConversation(id: string): Promise<void> {
  const res = await apiFetch(`${API_URL}/conversations/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`채팅방을 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function getConversationMessages(id: string): Promise<DBMessage[]> {
  const res = await apiFetch(`${API_URL}/conversations/${id}/messages`);
  if (!res.ok) throw new Error(`메시지를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateConversation(
  id: string,
  body: { title?: string; pinned?: boolean; sort_order?: number }
): Promise<Conversation> {
  const res = await apiFetch(`${API_URL}/conversations/${id}`, {
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
  const res = await apiFetch(`${API_URL}/conversations/${id}/project`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId }),
  });
  if (!res.ok) throw new Error(`채팅을 프로젝트에 연결하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 개인 지침(설정) ---

export async function getSettings(): Promise<Settings> {
  const res = await apiFetch(`${API_URL}/settings`);
  if (!res.ok) throw new Error(`설정을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function updateSettings(body: SettingsUpdateBody): Promise<Settings> {
  const res = await apiFetch(`${API_URL}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`설정을 저장하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteInstruction(): Promise<Settings> {
  const res = await apiFetch(`${API_URL}/settings/instruction`, { method: "DELETE" });
  if (!res.ok) throw new Error(`개인 지침을 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function importMemory(text: string): Promise<Settings> {
  const res = await apiFetch(`${API_URL}/settings/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`가져오기에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteImportedMemory(): Promise<{ imported_memory: string | null }> {
  const res = await apiFetch(`${API_URL}/settings/import`, { method: "DELETE" });
  if (!res.ok) throw new Error(`가져온 정보를 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 사용자 ---

export async function updateCurrentUser(body: UserUpdateBody): Promise<User> {
  const res = await apiFetch(`${API_URL}/users/me`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`이름을 저장하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// 프로필 사진 URL. avatar_updated_at을 쿼리에 붙여 사진이 바뀌면 캐시도 갱신되게 한다.
export function getAvatarUrl(user: User | null): string | null {
  if (!user?.avatar_updated_at) return null;
  return `${API_URL}/users/me/avatar?v=${encodeURIComponent(user.avatar_updated_at)}`;
}

export async function uploadAvatar(file: File): Promise<User> {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch(`${API_URL}/users/me/avatar`, { method: "PUT", body: form });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).detail ?? "";
    } catch {}
    throw new Error(typeof detail === "string" && detail ? detail : `사진을 올리지 못했습니다 (HTTP ${res.status})`);
  }
  return res.json();
}

export async function deleteAvatar(): Promise<User> {
  const res = await apiFetch(`${API_URL}/users/me/avatar`, { method: "DELETE" });
  if (!res.ok) throw new Error(`사진을 삭제하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function getCurrentUser(): Promise<User> {
  const res = await apiFetch(`${API_URL}/users/me`);
  if (!res.ok) throw new Error(`사용자 정보를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 자동 메모리 추출 ---

export async function extractMemoryAll(): Promise<{ status: string; count: number }> {
  const res = await apiFetch(`${API_URL}/memory/extract-all`, { method: "POST" });
  if (!res.ok) throw new Error(`메모리 업데이트에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 메모리 조회(전역 + 프로젝트별) ---

export async function getMemoryOverview(): Promise<MemoryOverview> {
  const res = await apiFetch(`${API_URL}/settings/memory`);
  if (!res.ok) throw new Error(`메모리를 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function deleteMemoryFact(memoryId: string): Promise<void> {
  const res = await apiFetch(`${API_URL}/settings/memory/${memoryId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`메모리를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function updateMemoryTopic(
  memoryId: string,
  payload: MemoryTopicUpdate
): Promise<MemoryTopic> {
  const res = await apiFetch(`${API_URL}/settings/memory/${memoryId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`메모리를 수정하지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 프로젝트 폴더 ---

export async function listProjects(): Promise<Project[]> {
  const res = await apiFetch(`${API_URL}/projects`);
  if (!res.ok) throw new Error(`프로젝트 목록을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function createProject(name: string, instruction?: string): Promise<Project> {
  const res = await apiFetch(`${API_URL}/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, instruction }),
  });
  if (!res.ok) throw new Error(`프로젝트를 만들지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function getProject(id: string): Promise<Project> {
  const res = await apiFetch(`${API_URL}/projects/${id}`);
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
  const res = await apiFetch(`${API_URL}/projects/${id}`, {
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
  const res = await apiFetch(`${API_URL}/projects/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) {
    throw new Error(`프로젝트를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function listProjectFiles(projectId: string): Promise<ProjectFilesOut> {
  const res = await apiFetch(`${API_URL}/projects/${projectId}/files`);
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
  return xhrUpload(`${API_URL}/projects/${projectId}/files`, file, onProgress);
}

export async function deleteProjectFile(projectId: string, fileId: string): Promise<void> {
  const res = await apiFetch(`${API_URL}/projects/${projectId}/files/${fileId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`참고자료를 삭제하지 못했습니다 (HTTP ${res.status})`);
  }
}

export async function extractProjectMemoryAll(
  projectId: string
): Promise<{ status: string; count: number }> {
  const res = await apiFetch(`${API_URL}/projects/${projectId}/memory/extract-all`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`메모리 업데이트에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

// --- 토큰 사용량 ---

export async function getUsageQuota(): Promise<UsageQuota> {
  const res = await apiFetch(`${API_URL}/usage/quota`);
  if (!res.ok) throw new Error(`토큰 사용량을 불러오지 못했습니다 (HTTP ${res.status})`);
  return res.json();
}

export async function getUsageStats(period: UsagePeriod): Promise<UsageStats> {
  const res = await apiFetch(`${API_URL}/usage/stats?period=${period}`);
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
  return xhrUpload(`${API_URL}/conversations/${conversationId}/attachments`, file, onProgress);
}

export async function deleteAttachment(
  conversationId: string,
  attachmentId: string
): Promise<void> {
  const res = await apiFetch(
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
  const res = await apiFetch(attachmentPreviewPdfUrl(kind, parentId, itemId));
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
  const res = await apiFetch(`${attachmentBaseUrl(kind, parentId, itemId)}/table`);
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
  const res = await apiFetch(`${attachmentBaseUrl(kind, parentId, itemId)}/text`);
  if (!res.ok) {
    const payload = await res.json().catch(() => null);
    throw new Error(payload?.detail ?? `텍스트를 불러오지 못했습니다 (HTTP ${res.status})`);
  }
  return res.json();
}

// --- 대화 검색 ---

export async function searchConversations(query: string): Promise<SearchResult[]> {
  const res = await apiFetch(`${API_URL}/search?query=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error(`검색에 실패했습니다 (HTTP ${res.status})`);
  return res.json();
}

export interface StreamHandlers {
  onMeta: (meta: { model: string; category: string | null; complexity: number | null }) => void;
  onSearching: (query: string) => void;
  onProcessing: (message: string) => void;
  onFile: (attachment: AttachmentOut) => void;
  onChunk: (content: string) => void;
  onDone: () => void;
  onError: (message: string) => void;
}

/**
 * SSE 응답 본문을 읽어 이벤트별 핸들러를 부른다. fetch의 ReadableStream을
 * 직접 읽는다(EventSource는 POST 바디를 못 보내서 못 씀). 이벤트는 "\n\n"으로
 * 구분되므로, 청크 경계에 걸쳐 잘린 이벤트는 buffer에 남겼다가 이어붙인다.
 */
async function readSseStream(body: ReadableStream<Uint8Array>, handlers: StreamHandlers): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    let done: boolean;
    let value: Uint8Array | undefined;
    try {
      ({ done, value } = await reader.read());
    } catch (err) {
      // 호출 쪽이 signal.abort()로 끊은 경우(다른 채팅으로 이동 등) - 화면만
      // 구독을 멈출 뿐 서버는 응답을 끝까지 만들어 저장하므로 에러로 취급하지 않는다.
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

/**
 * 질문 전송 + 응답 스트리밍. 서버는 질문을 먼저 저장하고 응답을 백그라운드에서
 * 끝까지 생성·저장하므로, signal로 끊으면(채팅 이동/새로고침) 이 화면의 구독만
 * 멈추고 응답 자체는 계속 만들어진다 - 돌아오면 resumeStream으로 다시 붙는다.
 */
export async function streamMessage(
  conversationId: string,
  body: SendMessageBody,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  let res: Response;
  try {
    res = await apiFetch(`${API_URL}/conversations/${conversationId}/messages`, {
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
  await readSseStream(res.body, handlers);
}

/**
 * 이 대화방에서 아직 생성 중인 응답이 있으면 지금까지의 진행 상황부터 다시 받아
 * 이어서 스트리밍한다. 진행 중인 응답이 없으면(서버 204) false.
 */
export async function resumeStream(
  conversationId: string,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<boolean> {
  let res: Response;
  try {
    res = await apiFetch(`${API_URL}/conversations/${conversationId}/stream`, { signal });
  } catch {
    return false;
  }
  if (res.status === 204 || !res.ok || !res.body) return false;
  await readSseStream(res.body, handlers);
  return true;
}
