"use client";

// 특정 채팅방 화면. 메시지 목록 로드, SSE 스트리밍 전송, 첨부파일 업로드/뷰어
// 등 "채팅방 전용" 상태와 로직이 전부 여기 로컬 state로 있다(예전 app/page.tsx
// 61~79행에 있던 것 중 messages/mode/size/input/phase/streamingId/attachments/
// isDraggingFile/viewerAttachment에 해당).
//
// "/"(새 채팅) 화면에서 넘어온 경우, 그 화면이 sessionStorage에 잠깐 맡겨둔
// "보낼 내용"(pendingMessage)이 있으면 DB 조회 대신 그걸로 바로 스트리밍을
// 시작한다 - 이래야 "생성 직후 라우팅 이동" 시점에 스트림이 끊기지 않는다
// (자세한 설계 이유는 frontend/lib/pendingMessage.ts 주석 참고).
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import ChatBubble from "@/components/ChatBubble";
import ChatScreen from "@/components/ChatScreen";
import DateDivider from "@/components/DateDivider";
import EmptyState from "@/components/EmptyState";
import AttachmentViewerModal, { ViewerAttachment } from "@/components/AttachmentViewerModal";
import { useAppData } from "@/contexts/AppDataContext";
import {
  deleteAttachment,
  getConversationMessages,
  streamMessage,
  uploadAttachment,
} from "@/lib/api";
import { isDifferentDay } from "@/lib/dateFormat";
import { popPendingMessage } from "@/lib/pendingMessage";
import { ChatMessage, Mode, PendingAttachment, SendMessageBody, Size } from "@/lib/types";

type Phase = "idle" | "waiting-meta" | "streaming";

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const router = useRouter();
  const { refreshConversations, setIsStreaming } = useAppData();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [mode, setMode] = useState<Mode>("auto");
  const [size, setSize] = useState<Size>("medium");
  const [input, setInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [viewerAttachment, setViewerAttachment] = useState<ViewerAttachment | null>(null);
  const [notFound, setNotFound] = useState(false);

  const isStreaming = phase !== "idle";
  const isUploadingAttachment = attachments.some((a) => a.status === "uploading");
  const bottomRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  // 스트리밍 중 새 청크가 올 때마다 무조건 맨 아래로 스크롤하면, 사용자가
  // 위로 스크롤해서 이전 내용을 보려 해도 계속 아래로 끌려 내려간다. 그래서
  // "사용자가 지금 맨 아래 근처에 있을 때만" 자동 스크롤하도록 추적한다.
  const stickToBottomRef = useRef(true);
  // 브라우저 뒤로/앞으로가기로 이 페이지가 스트리밍 도중 다른 id로 넘어가는
  // 경우(사이드바 클릭은 disabled로 막히지만 이건 못 막음) SSE 콜백이 이미
  // 바뀐 화면을 덮어쓰지 않도록, "지금 이 컴포넌트가 실제로 보여주고 있는
  // id"를 별도로 추적해서 어긋나면 업데이트를 무시한다.
  const displayedIdRef = useRef(conversationId);

  useEffect(() => {
    displayedIdRef.current = conversationId;
    stickToBottomRef.current = true;
    setNotFound(false);

    const pending = popPendingMessage(conversationId);
    if (pending) {
      setMode(pending.mode);
      if (pending.size) setSize(pending.size);
      const userMsg: ChatMessage = {
        id: nextId(),
        role: "user",
        content: pending.content,
        createdAt: new Date().toISOString(),
        attachments: pending.attachmentsSnapshot,
      };
      setMessages([userMsg]);
      const body: SendMessageBody = {
        content: pending.content,
        mode: pending.mode,
        ...(pending.size ? { size: pending.size } : {}),
        ...(pending.attachmentIds.length > 0 ? { attachment_ids: pending.attachmentIds } : {}),
      };
      streamAssistantReply(conversationId, body);
      return;
    }

    setMessages([]);
    getConversationMessages(conversationId)
      .then((dbMessages) => {
        if (displayedIdRef.current !== conversationId) return;
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
      })
      .catch(() => {
        // 존재하지 않는 채팅방이거나(삭제됨/오타 URL) 일시적 오류 - 이 데모
        // 앱엔 별도 404 화면이 없으므로 기본 화면으로 되돌린다.
        if (displayedIdRef.current === conversationId) setNotFound(true);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  useEffect(() => {
    if (notFound) router.replace("/");
  }, [notFound, router]);

  useEffect(() => {
    if (stickToBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, phase]);

  useEffect(() => {
    setIsStreaming(isStreaming);
  }, [isStreaming, setIsStreaming]);

  // 이 페이지가 스트리밍 도중 사라지면(사이드바 클릭은 disabled로 막히지만,
  // 브라우저 URL 직접 수정/뒤로가기 등으로는 가능) 전역 isStreaming 플래그가
  // true로 눌어붙어 사이드바가 영영 비활성 상태가 되는 걸 막는 안전장치.
  useEffect(() => {
    return () => setIsStreaming(false);
  }, [setIsStreaming]);

  function handleMainScroll() {
    const el = mainRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 80;
  }

  async function handleFilesSelected(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const localId = nextId();
      setAttachments((prev) => [...prev, { id: localId, file, status: "uploading", progress: 0 }]);
      try {
        const uploaded = await uploadAttachment(conversationId, file, (percent) => {
          setAttachments((prev) =>
            prev.map((a) => (a.id === localId ? { ...a, progress: percent } : a))
          );
        });
        setAttachments((prev) =>
          prev.map((a) => (a.id === localId ? { ...a, id: uploaded.id, status: "ready" } : a))
        );
      } catch (err) {
        setAttachments((prev) =>
          prev.map((a) =>
            a.id === localId
              ? { ...a, status: "error", error: err instanceof Error ? err.message : "업로드 실패" }
              : a
          )
        );
      }
    }
  }

  function handleRemoveAttachment(attachment: PendingAttachment) {
    setAttachments((prev) => prev.filter((a) => a.id !== attachment.id));
    if (attachment.status === "ready") {
      deleteAttachment(conversationId, attachment.id).catch(() => {
        // 취소 실패해도 UI에서는 이미 지웠으니 조용히 무시 (데모 범위)
      });
    }
  }

  async function streamAssistantReply(convId: string, body: SendMessageBody) {
    const assistantId = nextId();
    setPhase("waiting-meta");
    setStreamingId(assistantId);

    const isCurrent = () => displayedIdRef.current === convId;

    await streamMessage(convId, body, {
      onMeta: (meta) => {
        if (!isCurrent()) return;
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
        if (!isCurrent()) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, searchingQuery: query } : m))
        );
      },
      onProcessing: (message) => {
        if (!isCurrent()) return;
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, processingMessage: message } : m))
        );
      },
      onChunk: (chunk) => {
        if (!isCurrent()) return;
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: m.content + chunk, searchingQuery: null, processingMessage: null }
              : m
          )
        );
      },
      onDone: () => {
        if (!isCurrent()) return;
        setPhase("idle");
        setStreamingId(null);
        refreshConversations();
        // 첫 메시지였다면 제목 생성이 스트림 종료 직후 백그라운드로 도니,
        // 살짝 늦게 한 번 더 갱신해서 제목이 붙는 걸 잡는다.
        setTimeout(refreshConversations, 2000);
      },
      onError: (message) => {
        if (!isCurrent()) return;
        setPhase("idle");
        setStreamingId(null);
        setMessages((prev) => {
          const exists = prev.some((m) => m.id === assistantId);
          if (exists) {
            return prev.map((m) =>
              m.id === assistantId ? { ...m, content: message, isError: true } : m
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
    });
  }

  async function handleSend() {
    const text = input.trim();
    const readyAttachments = attachments.filter((a) => a.status === "ready");
    if ((!text && readyAttachments.length === 0) || isStreaming || isUploadingAttachment) {
      return;
    }

    setInput("");
    stickToBottomRef.current = true;

    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
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

    await streamAssistantReply(conversationId, {
      content: text,
      mode,
      ...(mode !== "auto" ? { size } : {}),
      ...(attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
    });
  }

  if (notFound) return null;

  return (
    <>
      <ChatScreen
        input={input}
        onInputChange={setInput}
        onSend={handleSend}
        disabled={isStreaming || isUploadingAttachment}
        attachments={attachments}
        onFilesSelected={handleFilesSelected}
        onRemoveAttachment={handleRemoveAttachment}
        onAttachmentChipClick={(a) =>
          setViewerAttachment({
            id: a.id,
            original_name: a.file.name,
            mime_type: a.file.type || "application/octet-stream",
            size_bytes: a.file.size,
          })
        }
        mode={mode}
        size={size}
        onModelSelect={(m, s) => {
          setMode(m);
          setSize(s);
        }}
      >
        <main ref={mainRef} onScroll={handleMainScroll} className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-2xl flex-col gap-5 px-4 py-6">
            {messages.length === 0 && phase === "idle" && (
              <EmptyState onPick={(text) => setInput(text)} />
            )}

            {messages.map((m, idx) => {
              const prev = messages[idx - 1];
              const showDivider = !prev || isDifferentDay(prev.createdAt, m.createdAt);
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
      </ChatScreen>

      {viewerAttachment && (
        <AttachmentViewerModal
          kind="chat"
          parentId={conversationId}
          attachment={viewerAttachment}
          onClose={() => setViewerAttachment(null)}
        />
      )}
    </>
  );
}
