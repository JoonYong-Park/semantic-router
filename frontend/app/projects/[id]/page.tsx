"use client";

// 프로젝트 페이지 - 예전 ProjectModal.tsx(모달)을 대체하는 실제 라우트.
// 왼쪽엔 이 프로젝트 안에서 바로 새 채팅을 시작할 수 있는 간단한 입력창 +
// 그 아래 소속 채팅 목록, 오른쪽엔 지침/색상/참고자료(용량 바 포함)/메모리
// 바로가기를 두는 2단 레이아웃. 이름 변경/삭제는 사이드바의 "⋮" 메뉴로
// 옮겨서(일반 채팅과 동일한 방식) 여기서는 더 이상 다루지 않는다.
import { DragEvent, KeyboardEvent, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  createConversation,
  deleteAttachment,
  deleteProjectFile,
  extractProjectMemoryAll,
  getProject,
  listProjectFiles,
  updateConversationProject,
  updateProject,
  uploadAttachment,
  uploadProjectFile,
} from "@/lib/api";
import { useAppData } from "@/contexts/AppDataContext";
import { stashPendingMessage } from "@/lib/pendingMessage";
import { PendingAttachment, Project, ProjectColor, ProjectFile } from "@/lib/types";
import { AttachmentChip, AttachmentTypeIcon, truncateFilename } from "@/components/AttachmentChip";
import AttachmentViewerModal, { ViewerAttachment } from "@/components/AttachmentViewerModal";
import UsageRing from "@/components/UsageRing";

const PROJECT_COLOR_OPTIONS: { value: ProjectColor; swatchClassName: string; label: string }[] = [
  { value: "default", swatchClassName: "bg-gray-400 dark:bg-gray-300", label: "기본" },
  { value: "red", swatchClassName: "bg-red-500", label: "빨강" },
  { value: "orange", swatchClassName: "bg-orange-500", label: "주황" },
  { value: "yellow", swatchClassName: "bg-yellow-500", label: "노랑" },
  { value: "green", swatchClassName: "bg-green-500", label: "초록" },
  { value: "blue", swatchClassName: "bg-blue-500", label: "파랑" },
  { value: "purple", swatchClassName: "bg-purple-500", label: "보라" },
  { value: "pink", swatchClassName: "bg-pink-500", label: "핑크" },
];

const ACCEPTED_EXTENSIONS = [
  ".txt", ".md", ".csv", ".xlsx", ".docx", ".pptx",
  ".hwp", ".hwpx", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
];

let idCounter = 0;
const nextId = () => `pf-${Date.now()}-${idCounter++}`;

interface PendingUpload {
  id: string;
  file: File;
  status: "uploading" | "error";
  progress?: number;
  error?: string;
}

function BackIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M15 18l-6-6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M4 7h16M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3m2 0v13a1 1 0 01-1 1H8a1 1 0 01-1-1V7h10z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SpinnerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 animate-spin">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M12 19V5M12 5L6 11M12 5L18 11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function PaperclipIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path
        d="M21.44 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3.33 3.33 0 014.71 4.71l-9.2 9.19a1.67 1.67 0 01-2.36-2.36l8.49-8.48"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

export default function ProjectPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const router = useRouter();
  const { conversations, setConversations, setProjects, openMemoryForProject } = useAppData();

  const [project, setProject] = useState<Project | null>(null);
  const [loadingProject, setLoadingProject] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleValue, setTitleValue] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  const [instruction, setInstruction] = useState("");
  const [saving, setSaving] = useState(false);
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [usedBytes, setUsedBytes] = useState(0);
  const [maxBytes, setMaxBytes] = useState(0);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [pending, setPending] = useState<PendingUpload[]>([]);
  // 참고자료(오른쪽) 드래그앤드롭 - 프로젝트 참고 파일로 업로드.
  const [isDraggingRefFile, setIsDraggingRefFile] = useState(false);
  const [extractStatus, setExtractStatus] = useState<"idle" | "loading" | "done">("idle");
  const [viewerFile, setViewerFile] = useState<ViewerAttachment | null>(null);
  const [quickInput, setQuickInput] = useState("");
  const [starting, setStarting] = useState(false);
  const refFileInputRef = useRef<HTMLInputElement>(null);
  const refFileDragDepthRef = useRef(0);

  // 새 채팅(왼쪽) 드래그앤드롭/첨부 버튼 - 지금 만들고 있는 새 채팅의
  // 첨부파일로 업로드(참고자료와는 완전히 별개). 첫 첨부 시점에 대화방을
  // 미리 만들어두고(pendingConvIdRef), 실제 "전송"을 누를 때 재사용한다 -
  // "/"(새 채팅) 화면의 ensureConversationId와 동일한 패턴.
  const [chatAttachments, setChatAttachments] = useState<PendingAttachment[]>([]);
  const [isDraggingChatFile, setIsDraggingChatFile] = useState(false);
  const chatFileInputRef = useRef<HTMLInputElement>(null);
  const chatFileDragDepthRef = useRef(0);
  const pendingConvIdRef = useRef<string | null>(null);
  const isUploadingChatAttachment = chatAttachments.some((a) => a.status === "uploading");

  const projectConversations = conversations.filter((c) => c.project_id === projectId);

  useEffect(() => {
    setLoadingProject(true);
    setNotFound(false);
    getProject(projectId)
      .then((p) => {
        setProject(p);
        setInstruction(p.instruction ?? "");
        setMaxBytes(p.max_capacity_bytes);
        // 방금 "새 프로젝트"로 만들어진 상태 그대로면(사이드바 "+"로 막
        // 생성된 직후) 제목을 바로 고쳐 쓸 수 있게 편집 모드로 열어둔다.
        if (p.name === "새 프로젝트") {
          setTitleValue(p.name);
          setEditingTitle(true);
        }
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoadingProject(false));
  }, [projectId]);

  useEffect(() => {
    if (editingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [editingTitle]);

  function startEditTitle() {
    if (!project) return;
    setTitleValue(project.name);
    setEditingTitle(true);
  }

  async function handleSaveTitle() {
    if (!project) return;
    const trimmed = titleValue.trim();
    setEditingTitle(false);
    if (!trimmed || trimmed === project.name) return;
    try {
      const updated = await updateProject(projectId, { name: trimmed });
      setProject(updated);
      setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch {
      // 실패해도 조용히 무시 (데모 범위) - 제목을 눌러서 다시 시도할 수 있음
    }
  }

  function handleTitleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleSaveTitle();
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setEditingTitle(false);
    }
  }

  useEffect(() => {
    if (notFound) router.replace("/");
  }, [notFound, router]);

  async function refreshFiles() {
    try {
      const data = await listProjectFiles(projectId);
      setFiles(data.files);
      setUsedBytes(data.used_bytes);
      setMaxBytes(data.max_bytes);
    } catch {
      // 조회 실패해도 화면은 그대로 열어둔다 - 다음 조작에서 다시 시도됨
    } finally {
      setLoadingFiles(false);
    }
  }

  useEffect(() => {
    refreshFiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleSaveInstruction() {
    if (!project) return;
    if (instruction === (project.instruction ?? "")) return;
    setSaving(true);
    try {
      const updated = await updateProject(projectId, { instruction });
      setProject(updated);
      setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch {
      // 저장 실패해도 입력값은 그대로 둬서 다시 blur하면 재시도됨
    } finally {
      setSaving(false);
    }
  }

  async function handleColorChange(color: ProjectColor) {
    if (!project || color === project.color) return;
    try {
      const updated = await updateProject(projectId, { color });
      setProject(updated);
      setProjects((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
    } catch {
      // 실패해도 조용히 무시 - 다시 누르면 재시도됨
    }
  }

  async function handleFilesSelected(fileList: FileList | File[]) {
    for (const file of Array.from(fileList)) {
      const localId = nextId();
      setPending((prev) => [...prev, { id: localId, file, status: "uploading", progress: 0 }]);
      try {
        await uploadProjectFile(projectId, file, (percent) => {
          setPending((prev) =>
            prev.map((p) => (p.id === localId ? { ...p, progress: percent } : p))
          );
        });
        setPending((prev) => prev.filter((p) => p.id !== localId));
        await refreshFiles();
      } catch (err) {
        setPending((prev) =>
          prev.map((p) =>
            p.id === localId
              ? { ...p, status: "error", error: err instanceof Error ? err.message : "업로드 실패" }
              : p
          )
        );
      }
    }
  }

  function handleRemovePending(id: string) {
    setPending((prev) => prev.filter((p) => p.id !== id));
  }

  async function handleDeleteFile(fileId: string) {
    setFiles((prev) => prev.filter((f) => f.id !== fileId));
    try {
      await deleteProjectFile(projectId, fileId);
    } finally {
      await refreshFiles();
    }
  }

  async function handleExtract() {
    if (extractStatus === "loading") return;
    setExtractStatus("loading");
    try {
      await extractProjectMemoryAll(projectId);
      setExtractStatus("done");
      setTimeout(() => setExtractStatus("idle"), 2000);
    } catch {
      setExtractStatus("idle");
    }
  }

  // 첫 첨부파일을 붙이거나 실제 "전송"을 누르는 시점에 대화방이 필요한데,
  // 아직 없으면 여기서 만든다("/"의 ensureConversationId와 동일한 패턴) -
  // 이미 만들어둔 게 있으면(pendingConvIdRef) 그대로 재사용한다.
  async function ensurePendingConversation(): Promise<string | null> {
    if (pendingConvIdRef.current) return pendingConvIdRef.current;
    try {
      const conv = await createConversation();
      await updateConversationProject(conv.id, projectId);
      pendingConvIdRef.current = conv.id;
      setConversations((prev) => [{ ...conv, project_id: projectId }, ...prev]);
      return conv.id;
    } catch {
      return null;
    }
  }

  async function handleChatFilesSelected(fileList: FileList | File[]) {
    const convId = await ensurePendingConversation();
    if (!convId) return;

    for (const file of Array.from(fileList)) {
      const localId = nextId();
      setChatAttachments((prev) => [
        ...prev,
        { id: localId, file, status: "uploading", progress: 0 },
      ]);
      try {
        const uploaded = await uploadAttachment(convId, file, (percent) => {
          setChatAttachments((prev) =>
            prev.map((a) => (a.id === localId ? { ...a, progress: percent } : a))
          );
        });
        setChatAttachments((prev) =>
          prev.map((a) => (a.id === localId ? { ...a, id: uploaded.id, status: "ready" } : a))
        );
      } catch (err) {
        setChatAttachments((prev) =>
          prev.map((a) =>
            a.id === localId
              ? { ...a, status: "error", error: err instanceof Error ? err.message : "업로드 실패" }
              : a
          )
        );
      }
    }
  }

  function handleRemoveChatAttachment(attachment: PendingAttachment) {
    setChatAttachments((prev) => prev.filter((a) => a.id !== attachment.id));
    if (attachment.status === "ready" && pendingConvIdRef.current) {
      deleteAttachment(pendingConvIdRef.current, attachment.id).catch(() => {
        // 취소 실패해도 UI에서는 이미 지웠으니 조용히 무시 (데모 범위)
      });
    }
  }

  // 왼쪽 "채팅창" - 텍스트/첨부가 있으면 그 내용으로 바로 첫 메시지까지
  // 보내고, 둘 다 없으면 그냥 빈 채팅방만 만들어서 이동한다("/"의
  // pendingMessage 인계 방식을 그대로 재사용 - /c/[id]가 마운트 즉시
  // 이어받아 스트리밍).
  async function handleStartChat() {
    if (starting || isUploadingChatAttachment) return;
    const text = quickInput.trim();
    const readyAttachments = chatAttachments.filter((a) => a.status === "ready");
    setStarting(true);
    try {
      const convId = await ensurePendingConversation();
      if (!convId) return;
      if (text || readyAttachments.length > 0) {
        stashPendingMessage(convId, {
          content: text,
          mode: "auto",
          attachmentIds: readyAttachments.map((a) => a.id),
          attachmentsSnapshot: readyAttachments.map((a) => ({
            id: a.id,
            original_name: a.file.name,
            mime_type: a.file.type || "application/octet-stream",
            size_bytes: a.file.size,
            extraction_status: "done",
            source: "upload",
            created_at: new Date().toISOString(),
          })),
        });
      }
      setQuickInput("");
      setChatAttachments([]);
      router.push(`/c/${convId}`);
    } catch {
      // 실패해도 조용히 무시 - 버튼을 다시 누르면 재시도됨
    } finally {
      setStarting(false);
    }
  }

  function handleQuickInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleStartChat();
    }
  }

  function handleChatDragEnter(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    chatFileDragDepthRef.current += 1;
    setIsDraggingChatFile(true);
  }
  function handleChatDragOver(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
  }
  function handleChatDragLeave(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    chatFileDragDepthRef.current = Math.max(0, chatFileDragDepthRef.current - 1);
    if (chatFileDragDepthRef.current === 0) setIsDraggingChatFile(false);
  }
  function handleChatDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    chatFileDragDepthRef.current = 0;
    setIsDraggingChatFile(false);
    if (e.dataTransfer.files.length > 0) handleChatFilesSelected(e.dataTransfer.files);
  }

  function handleRefFileDragEnter(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    refFileDragDepthRef.current += 1;
    setIsDraggingRefFile(true);
  }
  function handleRefFileDragOver(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
  }
  function handleRefFileDragLeave(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    refFileDragDepthRef.current = Math.max(0, refFileDragDepthRef.current - 1);
    if (refFileDragDepthRef.current === 0) setIsDraggingRefFile(false);
  }
  function handleRefFileDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    refFileDragDepthRef.current = 0;
    setIsDraggingRefFile(false);
    if (e.dataTransfer.files.length > 0) handleFilesSelected(e.dataTransfer.files);
  }

  if (notFound) return null;
  if (loadingProject || !project) {
    return (
      <div className="flex min-w-0 flex-1 items-center justify-center bg-white dark:bg-gray-950">
        <p className="text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
      </div>
    );
  }

  const percent = maxBytes > 0 ? Math.min((usedBytes / maxBytes) * 100, 100) : 0;
  const barColor = percent >= 90 ? "bg-red-600" : percent >= 70 ? "bg-amber-600" : "bg-[#5B8DB8]";

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-gray-950">
      <div className="flex shrink-0 items-center gap-3 border-b border-gray-100 px-5 py-3 dark:border-gray-800">
        <button
          type="button"
          onClick={() => router.back()}
          className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-sm text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
        >
          <BackIcon />
          뒤로
        </button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-2">
        {/* 왼쪽 - 이 프로젝트 안에서 바로 새 채팅 시작 + 소속 채팅 목록 */}
        <div
          className="relative flex min-h-0 flex-col overflow-hidden border-b border-gray-100 md:border-b-0 md:border-r dark:border-gray-800"
          onDragEnter={handleChatDragEnter}
          onDragOver={handleChatDragOver}
          onDragLeave={handleChatDragLeave}
          onDrop={handleChatDrop}
        >
          <div className="shrink-0 border-b border-gray-100 px-5 py-5 dark:border-gray-800">
            <label className="mb-2 block text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              새 채팅
            </label>
            {chatAttachments.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {chatAttachments.map((a) => (
                  <AttachmentChip
                    key={a.id}
                    name={a.file.name}
                    progress={a.status === "uploading" ? a.progress ?? 0 : undefined}
                    error={a.status === "error" ? a.error ?? "업로드 실패" : undefined}
                    onRemove={() => handleRemoveChatAttachment(a)}
                  />
                ))}
              </div>
            )}
            <div className="flex items-center gap-2 rounded-3xl border border-gray-300 bg-white px-2 py-1.5 shadow-sm focus-within:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:focus-within:border-gray-500">
              <UsageRing />
              <input
                ref={chatFileInputRef}
                type="file"
                multiple
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="sr-only"
                onChange={(e) => {
                  const selected = e.target.files ? Array.from(e.target.files) : [];
                  e.target.value = "";
                  if (selected.length > 0) handleChatFilesSelected(selected);
                }}
              />
              <button
                type="button"
                onClick={() => chatFileInputRef.current?.click()}
                aria-label="파일 첨부"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
              >
                <PaperclipIcon />
              </button>
              <input
                value={quickInput}
                onChange={(e) => setQuickInput(e.target.value)}
                onKeyDown={handleQuickInputKeyDown}
                placeholder="이 프로젝트에서 새로 시작할 메시지를 입력하세요"
                className="min-w-0 flex-1 bg-transparent text-sm text-gray-900 outline-none placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
              />
              <button
                type="button"
                onClick={handleStartChat}
                disabled={starting || isUploadingChatAttachment}
                aria-label="새 채팅 시작"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-900 text-white disabled:opacity-30 dark:bg-white dark:text-gray-900"
              >
                <SendIcon />
              </button>
            </div>
          </div>

          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-5">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              채팅 목록
            </h3>
            {projectConversations.length === 0 ? (
              <p className="py-3 text-center text-sm text-gray-400 dark:text-gray-500">
                아직 이 프로젝트에 속한 채팅이 없습니다
              </p>
            ) : (
              <ul className="flex flex-col gap-0.5">
                {projectConversations.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => router.push(`/c/${c.id}`)}
                      className="w-full truncate rounded-lg px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
                    >
                      {c.title || "새 채팅"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {isDraggingChatFile && (
            <div className="pointer-events-none absolute inset-0 z-10 m-3 flex items-center justify-center rounded-2xl border-2 border-dashed border-gray-400 bg-white/90 text-sm font-medium text-gray-600 dark:border-gray-500 dark:bg-gray-950/90 dark:text-gray-300">
              여기에 놓으면 이 채팅의 첨부파일로 추가됩니다
            </div>
          )}
        </div>

        {/* 오른쪽 - 지침/색상/참고자료(용량 포함)/메모리 바로가기 */}
        <div
          className="scroll-thin relative min-h-0 overflow-y-auto px-5 py-5"
          onDragEnter={handleRefFileDragEnter}
          onDragOver={handleRefFileDragOver}
          onDragLeave={handleRefFileDragLeave}
          onDrop={handleRefFileDrop}
        >
          {isDraggingRefFile && (
            <div className="pointer-events-none absolute inset-0 z-10 m-3 flex items-center justify-center rounded-2xl border-2 border-dashed border-gray-400 bg-white/90 text-sm font-medium text-gray-600 dark:border-gray-500 dark:bg-gray-950/90 dark:text-gray-300">
              여기에 놓으면 참고자료로 추가됩니다
            </div>
          )}
          <div className="mb-7 flex flex-col gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              프로젝트 명
            </label>
            {editingTitle ? (
              <input
                ref={titleInputRef}
                value={titleValue}
                onChange={(e) => setTitleValue(e.target.value)}
                onBlur={handleSaveTitle}
                onKeyDown={handleTitleKeyDown}
                className="w-full rounded-lg bg-white px-2 py-1 text-lg font-semibold text-gray-900 outline-none ring-1 ring-gray-400 dark:bg-gray-950 dark:text-gray-100"
              />
            ) : (
              <button
                type="button"
                onClick={startEditTitle}
                className="block w-full truncate rounded-lg px-2 py-1 text-left text-lg font-semibold text-gray-900 hover:bg-gray-100 dark:text-gray-100 dark:hover:bg-gray-800"
              >
                {project.name}
              </button>
            )}
          </div>

          <div className="mb-7 flex flex-col gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              지침
            </label>
            <textarea
              rows={4}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onBlur={handleSaveInstruction}
              placeholder="이 프로젝트에 속한 채팅 전체에 적용할 지침을 입력하세요"
              className="resize-none rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:border-gray-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:placeholder:text-gray-500"
            />
            {saving && <span className="text-xs text-gray-400 dark:text-gray-500">저장 중...</span>}
          </div>

          <div className="mb-7 flex flex-col gap-2">
            <label className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              폴더 색상
            </label>
            <div className="flex items-center gap-2">
              {PROJECT_COLOR_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => handleColorChange(opt.value)}
                  aria-label={opt.label}
                  title={opt.label}
                  className={`flex h-6 w-6 items-center justify-center rounded-full ring-offset-2 ring-offset-white transition dark:ring-offset-gray-900 ${
                    project.color === opt.value ? "ring-2 ring-gray-700 dark:ring-gray-300" : ""
                  }`}
                >
                  <span className={`h-5 w-5 rounded-full ${opt.swatchClassName}`} />
                </button>
              ))}
            </div>
          </div>

          <div className="mb-7">
            <div className="mb-1.5 flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                메모리
              </h3>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => openMemoryForProject(projectId)}
                  className="rounded-full border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
                >
                  메모리 바로가기
                </button>
                <button
                  type="button"
                  onClick={handleExtract}
                  disabled={extractStatus === "loading"}
                  className="flex items-center gap-1.5 rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-70 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                >
                  {extractStatus === "loading" && <SpinnerIcon />}
                  {extractStatus === "loading"
                    ? "업데이트 중..."
                    : extractStatus === "done"
                      ? "완료"
                      : "프로젝트 메모리 업데이트(TEST)"}
                </button>
              </div>
            </div>
            <p className="text-xs leading-relaxed text-gray-400 dark:text-gray-500">
              이 프로젝트의 채팅 내용에서 자동으로 뽑아낸 정보가 메모리로 저장되어 다음
              답변에 활용됩니다.
            </p>
          </div>

          <div className="mb-4">
            <div className="mb-1.5 flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                참고자료
              </h3>
              <button
                type="button"
                onClick={() => refFileInputRef.current?.click()}
                className="rounded-full bg-gray-900 px-3 py-1.5 text-xs font-medium text-white dark:bg-white dark:text-gray-900"
              >
                파일 추가
              </button>
              <input
                ref={refFileInputRef}
                type="file"
                multiple
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="sr-only"
                onChange={(e) => {
                  const selected = e.target.files ? Array.from(e.target.files) : [];
                  e.target.value = "";
                  if (selected.length > 0) handleFilesSelected(selected);
                }}
              />
            </div>
            <p className="mb-3 text-xs leading-relaxed text-gray-400 dark:text-gray-500">
              여기 올린 파일은 이 프로젝트의 모든 채팅에서 매 메시지마다 참고 자료로 함께
              전달됩니다.
            </p>

            <div className="rounded-xl border border-gray-200 bg-gray-50 px-3 py-3 dark:border-gray-700 dark:bg-gray-800/40">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[11px] text-gray-400 dark:text-gray-500">
                  {formatBytes(usedBytes)} / {formatBytes(maxBytes)}
                </span>
                <span className="text-xs font-semibold text-gray-600 dark:text-gray-300">
                  {percent.toFixed(1)}%
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                <div
                  className={`h-full rounded-full transition-all ${barColor}`}
                  style={{ width: `${percent}%` }}
                />
              </div>
            </div>
          </div>

          {pending.length > 0 && (
            <ul className="mb-2 flex flex-col gap-1.5">
              {pending.map((p) => (
                <li
                  key={p.id}
                  className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700"
                >
                  <AttachmentTypeIcon filename={p.file.name} />
                  <span className="flex-1 truncate text-gray-700 dark:text-gray-300" title={p.file.name}>
                    {truncateFilename(p.file.name)}
                  </span>
                  {p.status === "uploading" ? (
                    <span className="text-xs text-gray-400">{p.progress ?? 0}%</span>
                  ) : (
                    <span className="text-xs text-red-500" title={p.error}>
                      실패
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => handleRemovePending(p.id)}
                    aria-label="제거"
                    className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    <CloseIcon />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {loadingFiles ? (
            <p className="py-6 text-center text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
          ) : files.length === 0 && pending.length === 0 ? (
            <p className="py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              아직 참고자료가 없습니다. 왼쪽에 파일을 드래그하거나 &quot;파일 추가&quot;를 눌러
              올려보세요.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {files.map((f) => (
                <li
                  key={f.id}
                  className="flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700"
                >
                  <button
                    type="button"
                    onClick={() =>
                      setViewerFile({
                        id: f.id,
                        original_name: f.original_name,
                        mime_type: f.mime_type,
                        size_bytes: f.size_bytes,
                      })
                    }
                    className="flex min-w-0 flex-1 items-center gap-2 text-left hover:underline"
                  >
                    <AttachmentTypeIcon filename={f.original_name} />
                    <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300" title={f.original_name}>
                      {truncateFilename(f.original_name)}
                    </span>
                  </button>
                  <span className="shrink-0 text-xs text-gray-400 dark:text-gray-500">
                    {formatBytes(f.size_bytes)}
                  </span>
                  {f.extraction_status === "failed" && (
                    <span className="shrink-0 rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700 dark:bg-red-900/40 dark:text-red-400">
                      읽기 실패
                    </span>
                  )}
                  {f.extraction_status === "pending" && (
                    <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-500 dark:bg-gray-800 dark:text-gray-400">
                      처리 중
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => handleDeleteFile(f.id)}
                    aria-label="삭제"
                    className="shrink-0 text-gray-400 hover:text-red-600 dark:hover:text-red-400"
                  >
                    <TrashIcon />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {viewerFile && (
        <AttachmentViewerModal
          kind="project"
          parentId={projectId}
          attachment={viewerFile}
          onClose={() => setViewerFile(null)}
        />
      )}
    </div>
  );
}
