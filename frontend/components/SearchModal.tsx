"use client";

import { useEffect, useRef, useState } from "react";
import { searchConversations } from "@/lib/api";
import { SearchResult } from "@/lib/types";

// 타이핑마다 바로 요청을 쏘면 너무 잦아서, 짧게 debounce한다.
const SEARCH_DEBOUNCE_MS = 250;

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <line
        x1="21"
        y1="21"
        x2="16.65"
        y2="16.65"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

// 스니펫 안에서 검색어와 일치하는 부분만 굵게 표시한다 (대소문자 무시,
// 정규식 특수문자는 이스케이프해서 리터럴로 취급).
function highlightMatch(text: string, query: string) {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase() ? (
      <mark key={i} className="rounded bg-yellow-200 text-gray-900 dark:bg-yellow-500/40 dark:text-white">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    )
  );
}

export default function SearchModal({
  onClose,
  onSelectConversation,
}: {
  onClose: () => void;
  onSelectConversation: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setResults([]);
      setSearched(false);
      return;
    }

    setLoading(true);
    const timer = setTimeout(() => {
      searchConversations(trimmed)
        .then((r) => setResults(r))
        .catch(() => setResults([]))
        .finally(() => {
          setLoading(false);
          setSearched(true);
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [query]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 px-4 pt-24"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
          <span className="text-gray-400 dark:text-gray-500">
            <SearchIcon />
          </span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="채팅 검색"
            className="flex-1 bg-transparent text-sm text-gray-900 outline-none placeholder:text-gray-400 dark:text-gray-100 dark:placeholder:text-gray-500"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <div className="max-h-96 overflow-y-auto">
          {loading && (
            <p className="px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              검색 중...
            </p>
          )}

          {!loading && searched && results.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-gray-400 dark:text-gray-500">
              검색 결과가 없습니다
            </p>
          )}

          {!loading && results.length > 0 && (
            <ul className="py-1">
              {results.map((r) => (
                <li key={r.conversation_id}>
                  <button
                    type="button"
                    onClick={() => onSelectConversation(r.conversation_id)}
                    className="flex w-full flex-col items-start gap-0.5 px-4 py-2.5 text-left hover:bg-gray-50 dark:hover:bg-gray-800"
                  >
                    <span className="w-full truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                      {r.title || "새 채팅"}
                    </span>
                    <span className="line-clamp-1 w-full text-xs text-gray-500 dark:text-gray-400">
                      {highlightMatch(r.snippet, query.trim())}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
