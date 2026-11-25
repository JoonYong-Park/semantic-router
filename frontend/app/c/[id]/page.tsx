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
  resumeStream,
  StreamHandlers,
  streamMessage,
  uploadAttachment,
} from "@/lib/api";
import { isDifferentDay } from "@/lib/dateFormat";
import { popPendingMessage } from "@/lib/pendingMessage";
import { ChatMessage, Mode, PendingAttachment, SendMessageBody, Size } from "@/lib/types";

import { DEFAULT_MODE } from "@/lib/config";
type Phase = "idle" | "waiting-meta" | "streaming";

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

export default function ConversationPage() {
  const params = useParams<{ id: string }>();
  const conversationId = params.id;
  const router = useRouter();
  const { refreshConversations, setIsStreaming } = useAppData();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [mode, setMode] = useState<Mode>(DEFAULT_MODE);
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
  // 우리가 scrollToBottom()으로 직접 스크롤을 움직이는 동안엔 그 결과로
  // 발생하는 scroll 이벤트를 "사용자가 위로 스크롤했다"로 착각하면 안 된다 -
  // 이게 없으면 스트리밍 중 청크 하나 올 때마다 바닥으로 당겨지면서 생기는
  // scroll 이벤트가 매번 stickToBottomRef를 다시 true로 되돌려버려서, 사용자가
  // 위로 스크롤해도 바로 다음 청크에 다시 끌려 내려오는 것처럼 보인다.
  const isAutoScrollingRef = useRef(false);

  function scrollToBottom(smooth: boolean) {
    if (!bottomRef.current) return;
    isAutoScrollingRef.current = true;
    bottomRef.current.scrollIntoView({ behavior: smooth ? "smooth" : "auto" });
    // 스크롤이 정확히 언제 끝나는지는 알 수 없지만, 다음 두 프레임 안에는
    // 거의 항상 끝나므로 그 사이에 들어오는 scroll 이벤트만 "우리가 스크롤
    // 시켜서 난 이벤트"로 보고 무시한다.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        isAutoScrollingRef.current = false;
      });
    });
  }

  // 스트리밍 도중에도 사이드바에서 다른 채팅으로 이동할 수 있게 됐으므로
  // (더 이상 disabled로 안 막음), 이 페이지가 보여주는 대화방이 바뀔 때
  // SSE 콜백이 이미 떠난 화면을 덮어쓰지 않도록 "지금 실제로 보여주고 있는
  // id"를 추적해서 어긋나면 업데이트를 무시한다.
  const displayedIdRef = useRef(conversationId);
  // 지금 보고 있던 대화방에서 다른 대화방으로 넘어가면 이 화면의 구독(fetch)만
  // 끊는다. 서버는 질문을 먼저 저장하고 응답을 백그라운드에서 끝까지 만들어
  // 저장하므로, 돌아오면 loadMessages → resumeStream으로 다시 이어서 본다.
  const streamAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    // 직전 대화방에서 스트리밍 중이었다면 끊고 새 대화방 기준으로 완전히
    // 리셋한다 - 이걸 안 하면 "스트리밍 중" 상태(phase/streamingId)가 다음
    // 대화방 화면까지 그대로 넘어와서, 아무 일도 없는 채팅인데 입력창이
    // 막혀있거나 "질문 분석 중..."이 떠 있는 것처럼 보이는 버그가 난다.
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    setPhase("idle");
    setStreamingId(null);
    setAttachments([]);

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
    loadMessages(conversationId, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // DB에 저장된 메시지를 불러온다. 마지막 메시지가 답변 없는 질문이면, 서버가
  // 아직 그 응답을 만들고 있을 수 있으니(채팅을 옮기거나 새로고침해도 서버는
  // 계속 생성함) 진행 중인 응답에 다시 붙는다(tryResume).
  function loadMessages(convId: string, tryResume: boolean) {
    getConversationMessages(convId)
      .then((dbMessages) => {
        if (displayedIdRef.current !== convId) return;
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
        const last = dbMessages[dbMessages.length - 1];
        if (tryResume && last?.role === "user") {
          streamAssistantReply(convId, null);
        }
      })
      .catch(() => {
        // 존재하지 않는 채팅방이거나(삭제됨/오타 URL) 일시적 오류 - 이 데모
        // 앱엔 별도 404 화면이 없으므로 기본 화면으로 되돌린다.
        if (displayedIdRef.current === convId) setNotFound(true);
      });
  }

  useEffect(() => {
    if (notFound) router.replace("/");
  }, [notFound, router]);

  useEffect(() => {
    if (!stickToBottomRef.current) return;
    // 스트리밍 중(청크 하나당 한 번씩, 초당 수십 번도 가능)엔 "smooth"로
    // 매번 다시 애니메이션을 시작시키면 그 전 애니메이션이 채 끝나기도 전에
    // 또 새로 시작되면서 버벅거리는 것처럼 보인다 - 이럴 땐 애니메이션 없이
    // 즉시 붙여서(auto) 계속 바닥에 고정되게 하고, 스트리밍 중이 아닐 때
    // (새 메시지 전송 직후 등 한 번만 스크롤할 때)만 smooth를 쓴다.
    scrollToBottom(!isStreaming);
  }, [messages, phase, isStreaming]);

  useEffect(() => {
    setIsStreaming(isStreaming);
  }, [isStreaming, setIsStreaming]);

  // 이 페이지 자체가(= /c/[id] 라우트를 완전히 떠나서) 스트리밍 도중
  // 사라지면 전역 isStreaming 플래그가 true로 눌어붙는 것도, 아무도 안
  // 보는 스트림이 계속 요청을 받는 것도 막는 안전장치.
  useEffect(() => {
    return () => {
      setIsStreaming(false);
      streamAbortRef.current?.abort();
    };
  }, [setIsStreaming]);

  function handleMainScroll() {
    // 우리가 scrollToBottom()으로 만든 scroll 이벤트는 사용자 의사가
    // 아니므로 무시 - 안 그러면 스트리밍 중 매 청크마다 "바닥으로 붙임" →
    // 그 결과로 난 scroll 이벤트가 stickToBottomRef를 다시 true로 돌려놔서,
    // 사용자가 위로 스크롤해도 바로 다음 청크에 또 끌려 내려오게 된다.
    if (isAutoScrollingRef.current) return;
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

  // body가 있으면 새 질문 전송, null이면 이미 저장된 질문의 진행 중인 응답에 재접속.
  async function streamAssistantReply(convId: string, body: SendMessageBody | null) {
    const assistantId = nextId();
    setPhase("waiting-meta");
    setStreamingId(assistantId);

    const isCurrent = () => displayedIdRef.current === convId;

    const controller = new AbortController();
    streamAbortRef.current = controller;

    const handlers: StreamHandlers = {
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
        onFile: (attachment) => {
          if (!isCurrent()) return;
          // 생성된 파일 카드를 바로 붙이고, "문서 생성 중..." 표시는 여기서 끈다
          // (파일 뒤에 텍스트 chunk가 안 올 수도 있어서 chunk에서만 지우면 남음).
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? {
                    ...m,
                    attachments: [...(m.attachments ?? []), attachment],
                    processingMessage: null,
                  }
                : m
            )
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
    };

    if (body) {
      await streamMessage(convId, body, handlers, controller.signal);
      // 서버는 done을 보낸 뒤 제목 생성까지 마치고 연결을 닫는다 → 닫힌 뒤 한 번 더 갱신하면 제목이 확실히 붙는다.
      refreshConversations();
      return;
    }
    const resumed = await resumeStream(convId, handlers, controller.signal);
    if (!resumed && isCurrent()) {
      // 진행 중인 응답이 없음 - 방금 끝났을 수 있으니 저장된 메시지를 한 번만
      // 다시 불러온다(실패한 턴이면 질문만 남아 있는 게 실제 상태).
      setPhase("idle");
      setStreamingId(null);
      loadMessages(convId, false);
    }
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
        source: "upload",
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
        <main ref={mainRef} onScroll={handleMainScroll} className="scroll-thin flex-1 overflow-y-auto">
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
                    conversationId={conversationId}
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
