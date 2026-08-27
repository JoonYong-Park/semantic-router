"use client";

import { KeyboardEvent, useRef, useState } from "react";
import ModeSelector from "@/components/ModeSelector";
import ChatBubble from "@/components/ChatBubble";
import { ChatApiError, sendChat } from "@/lib/api";
import { ChatMessage, Mode } from "@/lib/types";

type LoadingStage = "classifying" | "calling" | null;

const MANUAL_LABEL: Record<Exclude<Mode, "auto">, string> = {
  gpt: "GPT",
  gemini: "Gemini",
  claude: "Claude",
};

const STAGE_LABEL: Record<Exclude<LoadingStage, null>, string> = {
  classifying: "🔍 질문 분류 중...",
  calling: "💬 모델 호출 중...",
};

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

export default function Home() {
  const [mode, setMode] = useState<Mode>("auto");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loadingStage, setLoadingStage] = useState<LoadingStage>(null);
  const stageTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isLoading = loadingStage !== null;

  async function handleSend() {
    const text = input.trim();
    if (!text || isLoading) return;

    const userMessage: ChatMessage = { id: nextId(), role: "user", content: text };
    setMessages((prev) => [...prev, userMessage]);
    setInput("");

    if (mode === "auto") {
      setLoadingStage("classifying");
      // 실제 분류/호출 단계는 백엔드 단일 호출 안에서 순차 처리되므로,
      // UX상 "분류 중 -> 모델 호출 중" 2단계를 근사적으로 보여준다.
      stageTimer.current = setTimeout(() => setLoadingStage("calling"), 600);
    } else {
      setLoadingStage("calling");
    }

    try {
      const res = await sendChat({ message: text, mode });
      const aiMessage: ChatMessage = {
        id: nextId(),
        role: "assistant",
        content: res.answer,
        selectedModel: res.selected_model,
        category: res.category,
      };
      setMessages((prev) => [...prev, aiMessage]);
    } catch (err) {
      const isApiError = err instanceof ChatApiError;
      // 수동 모드는 어떤 모델을 호출하려 했는지 클라이언트에서 이미 알고 있으니,
      // 백엔드가 못 채워준 경우에도 폴백으로 채워서 항상 배지가 뜨게 한다.
      const fallbackModel = mode !== "auto" ? MANUAL_LABEL[mode] : undefined;
      const errorMessage: ChatMessage = {
        id: nextId(),
        role: "assistant",
        content: err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.",
        selectedModel: (isApiError ? err.selectedModel : undefined) ?? fallbackModel,
        category: isApiError ? err.category : undefined,
        isError: true,
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      if (stageTimer.current) clearTimeout(stageTimer.current);
      setLoadingStage(null);
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  return (
    <main className="mx-auto flex h-screen max-w-2xl flex-col p-4">
      <header className="mb-4">
        <h1 className="text-lg font-bold">NPC AI Assistant — 1단계 데모</h1>
        <p className="text-xs text-gray-500">
          vLLM Semantic Router 기반 GPT / Gemini / Claude 자동 라우팅
        </p>
      </header>

      <div className="mb-4">
        <ModeSelector value={mode} onChange={setMode} />
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto rounded-xl border border-gray-200 bg-gray-50 p-4">
        {messages.length === 0 && (
          <p className="mt-8 text-center text-sm text-gray-400">
            메시지를 입력해서 대화를 시작하세요.
          </p>
        )}
        {messages.map((m) => (
          <ChatBubble key={m.id} message={m} />
        ))}
        {isLoading && (
          <div className="flex justify-start">
            <div className="animate-pulse rounded-2xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-500">
              {loadingStage && STAGE_LABEL[loadingStage]}
            </div>
          </div>
        )}
      </div>

      <div className="mt-4 flex gap-2">
        <input
          className="flex-1 rounded-lg border border-gray-300 px-4 py-2.5 text-sm outline-none focus:border-gray-500"
          placeholder="질문을 입력하세요..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={isLoading || !input.trim()}
          className="rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-40"
        >
          전송
        </button>
      </div>
    </main>
  );
}
