"use client";

import {
  KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import ModelPicker from "@/components/ModelPicker";
import ThemeToggle from "@/components/ThemeToggle";
import ChatBubble from "@/components/ChatBubble";
import DateDivider from "@/components/DateDivider";
import EmptyState from "@/components/EmptyState";
import Sidebar from "@/components/Sidebar";
import {
  createConversation,
  deleteConversation,
  getConversationMessages,
  listConversations,
  streamMessage,
} from "@/lib/api";
import { isDifferentDay } from "@/lib/dateFormat";
import { ChatMessage, Conversation, Mode, Size } from "@/lib/types";

type Phase = "idle" | "waiting-meta" | "streaming";

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

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

  const isStreaming = phase !== "idle";
  const bottomRef = useRef<HTMLDivElement>(null);
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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, phase]);

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
    setActiveId(null);
    setMessages([]);
  }

  async function handleSelectConversation(id: string) {
    if (isStreaming || id === activeId) return;
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

  async function handleSend(overrideText?: string) {
    const text = (overrideText ?? input).trim();
    if (!text || isStreaming) return;

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

    const userMsg: ChatMessage = {
      id: nextId(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);

    const assistantId = nextId();
    setPhase("waiting-meta");
    setStreamingId(assistantId);

    await streamMessage(
      convId,
      { content: text, mode, ...(mode !== "auto" ? { size } : {}) },
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
        onChunk: (chunk) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, content: m.content + chunk } : m
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
      />

      <div className="flex min-w-0 flex-1 flex-col bg-white dark:bg-gray-950">
        <header className="flex items-center justify-end border-b border-gray-100 px-4 py-2.5 dark:border-gray-800">
          <ThemeToggle />
        </header>

        <main className="flex-1 overflow-y-auto">
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
                  <ChatBubble message={m} isStreaming={m.id === streamingId} />
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
          <div className="mx-auto flex max-w-2xl items-end gap-2 rounded-3xl border border-gray-300 bg-white px-2 py-1.5 shadow-sm focus-within:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:focus-within:border-gray-500">
            <textarea
              ref={textareaRef}
              rows={1}
              className="max-h-40 flex-1 resize-none overflow-y-auto bg-transparent px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
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
              disabled={isStreaming}
            />

            <ModelPicker mode={mode} size={size} onSelect={(m, s) => { setMode(m); setSize(s); }} />

            <button
              type="button"
              onClick={() => handleSend()}
              disabled={isStreaming || !input.trim()}
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
  );
}
