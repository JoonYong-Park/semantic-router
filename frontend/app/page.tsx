"use client";

import { KeyboardEvent, useRef, useState } from "react";
import ModelPicker from "@/components/ModelPicker";
import ThemeToggle from "@/components/ThemeToggle";
import ChatBubble from "@/components/ChatBubble";
import { ChatApiError, sendChat } from "@/lib/api";
import { ChatMessage, Mode, Size } from "@/lib/types";

type LoadingStage = "classifying" | "calling" | null;

const STAGE_LABEL: Record<Exclude<LoadingStage, null>, string> = {
  classifying: "질문 분석 중...",
  calling: "응답 생성 중...",
};

let idCounter = 0;
const nextId = () => `${Date.now()}-${idCounter++}`;

export default function Home() {
  const [mode, setMode] = useState<Mode>("auto");
  const [size, setSize] = useState<Size>("medium");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loadingStage, setLoadingStage] = useState<LoadingStage>(null);
  const stageTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isLoading = loadingStage !== null;

  function handleModelSelect(newMode: Mode, newSize: Size) {
    setMode(newMode);
    setSize(newSize);
  }

  async function handleSend() {
    const text = input.trim();
    if (!text || isLoading) return;

    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: "user", content: text },
    ]);
    setInput("");

    if (mode === "auto") {
      setLoadingStage("classifying");
      // 백엔드 단일 요청이므로 로딩 단계를 UX용으로만 2단계로 나눔
      stageTimer.current = setTimeout(() => setLoadingStage("calling"), 700);
    } else {
      setLoadingStage("calling");
    }

    try {
      const res = await sendChat({
        message: text,
        mode,
        ...(mode !== "auto" ? { size } : {}),
      });
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content: res.answer,
          selectedModel: res.selected_model,
          taskCategory: res.task_category,
          complexityScore: res.complexity_score,
        },
      ]);
    } catch (err) {
      const isApiError = err instanceof ChatApiError;
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: "assistant",
          content:
            err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.",
          selectedModel: isApiError ? err.selectedModel : undefined,
          taskCategory: isApiError ? err.taskCategory : undefined,
          complexityScore: isApiError ? err.complexityScore : undefined,
          isError: true,
        },
      ]);
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
    <div className="flex h-screen flex-col bg-white dark:bg-gray-950">
      <header className="flex items-center justify-between border-b border-gray-100 px-4 py-2.5 dark:border-gray-800">
        <span className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-200">
          <img src="/icons/npia.png" alt="" className="h-9 w-auto" />
          NPIA(Navigating Productivity with Intelligent Assistant)
        </span>
        <ThemeToggle />
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-2xl flex-col gap-5 px-4 py-6">
          {messages.length === 0 && (
            <div className="mt-24 text-center text-gray-400 dark:text-gray-500">
              <p className="text-lg font-medium text-gray-600 dark:text-gray-300">
                무엇을 도와드릴까요?
              </p>
              <p className="mt-1 text-sm">
                Auto를 선택하면 질문을 분석해서 알맞은 모델로 자동 연결합니다.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <ChatBubble key={m.id} message={m} />
          ))}
          {isLoading && (
            <div className="animate-pulse text-sm text-gray-400 dark:text-gray-500">
              {loadingStage && STAGE_LABEL[loadingStage]}
            </div>
          )}
        </div>
      </main>

      <div className="border-t border-gray-100 bg-white px-4 py-4 dark:border-gray-800 dark:bg-gray-950">
        <div className="mx-auto flex max-w-2xl items-center gap-2 rounded-full border border-gray-300 bg-white px-2 py-1.5 shadow-sm focus-within:border-gray-400 dark:border-gray-700 dark:bg-gray-900 dark:focus-within:border-gray-500">
          <input
            className="flex-1 bg-transparent px-3 py-1.5 text-sm text-gray-900 outline-none placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
            placeholder="메시지를 입력하세요..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
          />

          <ModelPicker mode={mode} size={size} onSelect={handleModelSelect} />

          <button
            type="button"
            onClick={handleSend}
            disabled={isLoading || !input.trim()}
            aria-label="전송"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-900 text-white disabled:opacity-30 dark:bg-white dark:text-gray-900"
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
  );
}
