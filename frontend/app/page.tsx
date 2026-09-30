"use client";

import {
  DragEvent,
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import ModelPicker from "@/components/ModelPicker";
import ThemeToggle from "@/components/ThemeToggle";
import ChatBubble from "@/components/ChatBubble";
import { AttachmentChip } from "@/components/AttachmentChip";
import DateDivider from "@/components/DateDivider";
import EmptyState from "@/components/EmptyState";
import Sidebar from "@/components/Sidebar";
import SettingsModal from "@/components/SettingsModal";
import ImportMemoryModal from "@/components/ImportMemoryModal";
import UsageStatsModal from "@/components/UsageStatsModal";
import UsageRing from "@/components/UsageRing";
import SearchModal from "@/components/SearchModal";
import MemoryUpdateButton from "@/components/MemoryUpdateButton";
import AttachmentViewerModal, { ViewerAttachment } from "@/components/AttachmentViewerModal";
import {
  createConversation,
  deleteAttachment,
  deleteConversation,
  getConversationMessages,
  listConversations,
  streamMessage,
  uploadAttachment,
} from "@/lib/api";
import { isDifferentDay } from "@/lib/dateFormat";
import {
  ChatMessage,
  Conversation,
  Mode,
  PendingAttachment,
  Size,
} from "@/lib/types";

type Phase = "idle" | "waiting-meta" | "streaming";

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

// 서버(attachments.py ALL_ALLOWED_EXTS)와 맞춰둔 허용 확장자 - <input accept>와
// 드롭 시 1차 필터링용. 최종 검증은 항상 백엔드가 한다.
const ACCEPTED_EXTENSIONS = [
  ".txt", ".md", ".csv", ".xlsx", ".docx", ".pptx",
  ".hwp", ".hwpx", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
];

export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);

  const [mode, setMode] = useState<Mode>("auto");
  const [size, setSize] = useState<Size>("medium");
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [activeSettingsModal, setActiveSettingsModal] = useState<
    "personal" | "import" | "usage" | "search" | null
  >(null);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [isDraggingFile, setIsDraggingFile] = useState(false);
  const [viewerAttachment, setViewerAttachment] = useState<ViewerAttachment | null>(null);

  const isStreaming = phase !== "idle";
  const isUploadingAttachment = attachments.some((a) => a.status === "uploading");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  // 스트리밍 중 새 청크가 올 때마다 무조건 맨 아래로 스크롤하면, 사용자가
  // 위로 스크롤해서 이전 내용을 보려 해도 계속 아래로 끌려 내려간다. 그래서
  // "사용자가 지금 맨 아래 근처에 있을 때만" 자동 스크롤하도록 추적한다.
  const stickToBottomRef = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // e.nativeEvent.isComposing 하나만으로는 브라우저별로 놓치는 경우가 있어서,
  // onCompositionStart/End로 직접 추적하는 걸 같이 둔다 (표준적인 이중 방어).
  const isComposingRef = useRef(false);

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch(() => setConversations([]));

    try {
      setSidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
    } catch {
      // localStorage 접근 불가(프라이빗 브라우징 등)해도 기본값(펼침)으로 그냥 진행
    }
  }, []);

  function toggleSidebar() {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("sidebarCollapsed", next ? "1" : "0");
      } catch {
        // 저장 실패해도 화면 전환 자체는 정상 동작하게 무시
      }
      return next;
    });
  }

  useEffect(() => {
    if (stickToBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, phase]);

  function handleMainScroll() {
    const el = mainRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 80;
  }

  // 입력창 자동 높이 조절 (Shift+Enter 줄바꿈 시 늘어나도록)
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  async function refreshConversations() {
    try {
      setConversations(await listConversations());
    } catch {
      // 목록 갱신 실패는 조용히 무시 (다음 액션에서 다시 시도됨)
    }
  }

  function handleNewChat() {
    if (isStreaming) return;
    stickToBottomRef.current = true;
    setActiveId(null);
    setMessages([]);
  }

  async function handleSelectConversation(id: string) {
    if (isStreaming || id === activeId) return;
    stickToBottomRef.current = true;
    setActiveId(id);
    try {
      const dbMessages = await getConversationMessages(id);
      setMessages(
        dbMessages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          createdAt: m.created_at,
          selectedModel: m.model_used,
          taskCategory: m.task_category,
          complexityScore: m.complexity_score,
          attachments: m.attachments,
        }))
      );
    } catch {
      setMessages([]);
    }
  }

  async function handleDeleteConversation(id: string) {
    if (isStreaming) return;
    setConversations((prev) => prev.filter((c) => c.id !== id));
    if (id === activeId) {
      setActiveId(null);
      setMessages([]);
    }
    try {
      await deleteConversation(id);
    } catch {
      // 실패해도 목록에선 이미 지웠으니, 다음 refreshConversations 때 다시 나타나면
      // 그때 사용자가 인지할 수 있음 (데모 범위에선 재시도 UI까진 안 만듦)
    }
  }

  // 첨부파일은 메시지 전송 전(드래그하는 순간)에 이미 업로드되므로, 채팅방이
  // 아직 없으면(첫 메시지 전) 여기서도 handleSend와 동일하게 미리 만들어야 한다.
  async function ensureConversationId(): Promise<string | null> {
    if (activeId) return activeId;
    try {
      const conv = await createConversation();
      setConversations((prev) => [conv, ...prev]);
      setActiveId(conv.id);
      return conv.id;
    } catch {
      return null;
    }
  }

  async function handleFilesSelected(files: FileList | File[]) {
    const convId = await ensureConversationId();
    if (!convId) return;

    for (const file of Array.from(files)) {
      const localId = nextId();
      setAttachments((prev) => [
        ...prev,
        { id: localId, file, status: "uploading", progress: 0 },
      ]);

      try {
        const uploaded = await uploadAttachment(convId, file, (percent) => {
          setAttachments((prev) =>
            prev.map((a) => (a.id === localId ? { ...a, progress: percent } : a))
          );
        });
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === localId ? { ...a, id: uploaded.id, status: "ready" } : a
          )
        );
      } catch (err) {
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === localId
              ? {
                  ...a,
                  status: "error",
                  error: err instanceof Error ? err.message : "업로드 실패",
                }
              : a
          )
        );
      }
    }
  }

  function handleRemoveAttachment(attachment: PendingAttachment) {
    setAttachments((prev) => prev.filter((a) => a.id !== attachment.id));
    // "ready"는 서버에도 올라가 있는 실제 attachment_id라 취소 API를 호출해야
    // 한다. "uploading" 도중 취소는 로컬에서만 제거 - 업로드가 뒤늦게
    // 끝나더라도 24시간 뒤 고아 첨부 정리 배치가 알아서 지운다.
    if (attachment.status === "ready" && activeId) {
      deleteAttachment(activeId, attachment.id).catch(() => {
        // 취소 실패해도 UI에서는 이미 지웠으니 조용히 무시 (데모 범위)
      });
    }
  }

  function handleDragEnter(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setIsDraggingFile(true);
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
  }

  function handleDragLeave(e: DragEvent<HTMLDivElement>) {
    if (!e.dataTransfer.types.includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFile(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setIsDraggingFile(false);
    if (e.dataTransfer.files.length > 0) {
      handleFilesSelected(e.dataTransfer.files);
    }
  }

  async function handleSend(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    const readyAttachments = attachments.filter((a) => a.status === "ready");
    if ((!text && readyAttachments.length === 0) || isStreaming || isUploadingAttachment) {
      return;
    }

    setInput("");

    let convId = activeId;
    if (!convId) {
      try {
        const conv = await createConversation();
        convId = conv.id;
        setConversations((prev) => [conv, ...prev]);
        setActiveId(conv.id);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: nextId(),
            role: "assistant",
            content: "채팅방을 만들지 못했습니다. 잠시 후 다시 시도해주세요.",
            createdAt: new Date().toISOString(),
            isError: true,
          },
        ]);
        return;
      }
    }

    // 새 메시지를 보낼 땐 이전에 위로 스크롤해뒀었더라도 다시 아래로 따라가게 한다.
    stickToBottomRef.current = true;

    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
      // 서버 왕복 없이 방금 업로드된 정보로 바로 보여준다 (channel 새로고침
      // 전에도 "뭘 첨부해서 보냈는지" 즉시 표시되도록 - 실제 DB 값은 다음
      // handleSelectConversation 조회 때 그대로 대체된다).
      attachments: readyAttachments.map((a) => ({
        id: a.id,
        original_name: a.file.name,
        mime_type: a.file.type || "application/octet-stream",
        size_bytes: a.file.size,
        extraction_status: "done",
        created_at: new Date().toISOString(),
      })),
    };
    setMessages((prev) => [...prev, userMsg]);

    const attachmentIds = readyAttachments.map((a) => a.id);
    setAttachments([]);

    const assistantId = nextId();
    setPhase("waiting-meta");
    setStreamingId(assistantId);

    await streamMessage(
      convId,
      {
        content: text,
        mode,
        ...(mode !== "auto" ? { size } : {}),
        ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
      },
      {
        onMeta: (meta) => {
          setPhase("streaming");
          setMessages((prev) => [
            ...prev,
            {
              id: assistantId,
              role: "assistant",
              content: "",
              createdAt: new Date().toISOString(),
              selectedModel: meta.model,
              taskCategory: meta.category,
              complexityScore: meta.complexity,
            },
          ]);
        },
        onSearching: (query) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, searchingQuery: query } : m
            )
          );
        },
        onProcessing: (message) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, processingMessage: message } : m
            )
          );
        },
        onChunk: (chunk) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    content: m.content + chunk,
                    searchingQuery: null,
                    processingMessage: null,
                  }
                : m
            )
          );
        },
        onDone: () => {
          setPhase("idle");
          setStreamingId(null);
          refreshConversations();
          // 첫 메시지였다면 제목 생성이 스트림 종료 직후 백그라운드로 도니,
          // 살짝 늦게 한 번 더 갱신해서 제목이 붙는 걸 잡는다.
          setTimeout(refreshConversations, 2000);
        },
        onError: (message) => {
          setPhase("idle");
          setStreamingId(null);
          setMessages((prev) => {
            const exists = prev.some((m) => m.id === assistantId);
            if (exists) {
              return prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: message, isError: true }
                  : m
              );
            }
            return [
              ...prev,
              {
                id: assistantId,
                role: "assistant",
                content: message,
                createdAt: new Date().toISOString(),
                isError: true,
              },
            ];
          });
        },
      }
    );
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      // 한글/일본어 등 IME로 글자를 조합하는 도중에 Enter를 누르면(조합 확정용),
      // 브라우저가 이 keydown을 isComposing=true로 먼저 한 번 보내고 조금 뒤에
      // 진짜 Enter를 한 번 더 보낸다. 이걸 구분 안 하면 "조합 중이던 일부 글자"로
      // 한 번, "완성된 전체 문장"으로 또 한 번 - 총 두 번 전송돼버린다.
      if (e.nativeEvent.isComposing || isComposingRef.current) return;
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <div className="flex h-screen">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        disabled={isStreaming}
        collapsed={sidebarCollapsed}
        onToggleCollapsed={toggleSidebar}
        onSelect={handleSelectConversation}
        onNew={handleNewChat}
        onDelete={handleDeleteConversation}
        onOpenPersonalInstruction={() => setActiveSettingsModal("personal")}
        onOpenImportMemory={() => setActiveSettingsModal("import")}
        onOpenUsage={() => setActiveSettingsModal("usage")}
        onOpenSearch={() => setActiveSettingsModal("search")}
      />

      {activeSettingsModal === "personal" && (
        <SettingsModal onClose={() => setActiveSettingsModal(null)} />
      )}
      {activeSettingsModal === "import" && (
        <ImportMemoryModal onClose={() => setActiveSettingsModal(null)} />
      )}
      {activeSettingsModal === "usage" && (
        <UsageStatsModal onClose={() => setActiveSettingsModal(null)} />
      )}
      {activeSettingsModal === "search" && (
        <SearchModal
          onClose={() => setActiveSettingsModal(null)}
          onSelectConversation={(id) => {
            setActiveSettingsModal(null);
            handleSelectConversation(id);
          }}
        />
      )}

      {viewerAttachment && activeId && (
        <AttachmentViewerModal
          conversationId={activeId}
          attachment={viewerAttachment}
          onClose={() => setViewerAttachment(null)}
        />
      )}

      <MemoryUpdateButton />

      <div
        className="relative flex min-w-0 flex-1 flex-col bg-white dark:bg-gray-950"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDraggingFile && (
          <div className="pointer-events-none absolute inset-0 z-10 m-3 flex items-center justify-center rounded-2xl border-2 border-dashed border-gray-400 bg-white/80 text-sm font-medium text-gray-600 dark:border-gray-500 dark:bg-gray-950/80 dark:text-gray-300">
            여기에 파일을 놓으세요
          </div>
        )}

        <header className="flex items-center justify-end border-b border-gray-100 px-4 py-2.5 dark:border-gray-800">
          <ThemeToggle />
        </header>

        <main ref={mainRef} onScroll={handleMainScroll} className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-2xl flex-col gap-5 px-4 py-6">
            {messages.length === 0 && phase === "idle" && (
              <EmptyState onPick={(text) => setInput(text)} />
            )}

            {messages.map((m, idx) => {
              const prev = messages[idx - 1];
              const showDivider =
                !prev || isDifferentDay(prev.createdAt, m.createdAt);
              return (
                <div key={m.id}>
                  {showDivider && <DateDivider iso={m.createdAt} />}
                  <ChatBubble
                    message={m}
                    isStreaming={m.id === streamingId}
                    onAttachmentClick={(attachmentId) => {
                      const found = m.attachments?.find((a) => a.id === attachmentId);
                      if (found) setViewerAttachment(found);
                    }}
                  />
                </div>
              );
            })}

            {phase === "waiting-meta" && (
              <div className="animate-pulse text-sm text-gray-400 dark:text-gray-500">
                질문 분석 중...
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </main>

        <div className="border-t border-gray-100 bg-white px-4 py-4 dark:border-gray-800 dark:bg-gray-950">
          <div className="mx-auto max-w-2xl">
            {attachments.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-2">
                {attachments.map((a) => (
                  <AttachmentChip
                    key={a.id}
                    name={a.file.name}
                    progress={a.status === "uploading" ? a.progress ?? 0 : undefined}
                    error={a.status === "error" ? a.error ?? "업로드 실패" : undefined}
                    onRemove={() => handleRemoveAttachment(a)}
                    onClick={
                      a.status === "ready"
                        ? () =>
                            setViewerAttachment({
                              id: a.id,
                              original_name: a.file.name,
                              mime_type: a.file.type || "application/octet-stream",
                              size_bytes: a.file.size,
                            })
                        : undefined
                    }
                  />
                ))}
              </div>
            )}

          <div className="flex items-end gap-2 rounded-3xl border border-gray-300 bg-white px-2 py-1.5 shadow-sm focus-within:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:focus-within:border-gray-500">
            <UsageRing />

            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPTED_EXTENSIONS.join(",")}
              className="sr-only"
              onChange={(e) => {
                // e.target.files는 input에 실시간으로 묶인 라이브 FileList라,
                // handleFilesSelected(비동기 - 내부에 await 지점이 있음)에
                // 그대로 넘기고 나서 바로 아래에서 value를 리셋하면, 그 await가
                // 끝나기도 전에 라이브 FileList가 먼저 비어버려서 실제로는
                // 파일이 0개로 처리된다(선택창은 뜨는데 골라도 반응 없는 버그의
                // 원인이었음). 그래서 File 객체들을 여기서 동기적으로 배열로
                // 미리 꺼내놓은 뒤에 value를 리셋한다.
                const selected = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (selected.length > 0) {
                  handleFilesSelected(selected);
                }
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              aria-label="파일 첨부"
              className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 disabled:opacity-30 dark:text-gray-400 dark:hover:bg-gray-800"
            >
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M21.44 11.05l-9.19 9.19a5 5 0 01-7.07-7.07l9.19-9.19a3.33 3.33 0 014.71 4.71l-9.2 9.19a1.67 1.67 0 01-2.36-2.36l8.49-8.48"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>

            <textarea
              ref={textareaRef}
              rows={1}
              className="max-h-40 min-w-0 flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:overflow-hidden placeholder:whitespace-nowrap placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
              placeholder="메시지를 입력하세요... (Shift+Enter로 줄바꿈)"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onCompositionStart={() => {
                isComposingRef.current = true;
              }}
              onCompositionEnd={() => {
                isComposingRef.current = false;
              }}
            />

            <ModelPicker mode={mode} size={size} onSelect={(m, s) => { setMode(m); setSize(s); }} />

            <button
              type="button"
              onClick={() => handleSend()}
              disabled={
                isStreaming ||
                isUploadingAttachment ||
                (!input.trim() && attachments.filter((a) => a.status === "ready").length === 0)
              }
              aria-label="전송"
              className="mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-900 text-white disabled:opacity-30 dark:bg-white dark:text-gray-900"
            >
              <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
                <path
                  d="M12 19V5M12 5L6 11M12 5L18 11"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          </div>
          </div>
        </div>
      </div>
    </div>
  );
}
