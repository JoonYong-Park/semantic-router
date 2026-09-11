"use client";

import { useState } from "react";
import { extractMemoryAll } from "@/lib/api";

type Status = "idle" | "loading" | "done";

function SpinnerIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 animate-spin">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

// 데모 범위라 스케줄러 없이 수동 버튼으로만 트리거한다 (백엔드 memory.py 주석 참고).
// 라벨의 "24시간"은 나중에 자동화되면 이 주기로 돈다는 안내 문구.
export default function MemoryUpdateButton() {
  const [status, setStatus] = useState<Status>("idle");

  async function handleClick() {
    if (status === "loading") return;
    setStatus("loading");
    try {
      await extractMemoryAll();
      setStatus("done");
      setTimeout(() => setStatus("idle"), 2000);
    } catch {
      // 실패해도 다음에 다시 눌러 재시도할 수 있게 기본 상태로 되돌린다
      setStatus("idle");
    }
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={status === "loading"}
      className="fixed bottom-4 right-4 z-40 flex items-center gap-1.5 rounded-full border border-gray-300 bg-white px-3 py-1.5 text-xs text-gray-600 shadow-sm hover:bg-gray-50 disabled:opacity-70 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800"
    >
      {status === "loading" && <SpinnerIcon />}
      {status === "loading"
        ? "업데이트 중..."
        : status === "done"
          ? "완료"
          : "자동저장 업데이트(TEST)"}
    </button>
  );
}
