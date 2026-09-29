"use client";

import { useState } from "react";
import { ChatMessage } from "@/lib/types";
import { formatHoverTime } from "@/lib/dateFormat";
import { AttachmentChip } from "./AttachmentChip";
import MarkdownContent from "./MarkdownContent";
import ModelBadge from "./ModelBadge";

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <rect x="9" y="9" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
      <path
        d="M15 9V5a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2h4"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M5 13l4 4L19 7"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 권한이 없는 환경 등 - 조용히 무시 (버튼은 그대로 다시 누를 수 있음)
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      aria-label="응답 복사"
      title={copied ? "복사됨" : "복사"}
      className="flex h-4 w-4 items-center justify-center text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </button>
  );
}

export default function ChatBubble({
  message,
  isStreaming = false,
  onAttachmentClick,
}: {
  message: ChatMessage;
  isStreaming?: boolean;
  onAttachmentClick?: (attachmentId: string) => void;
}) {
  const isUser = message.role === "user";
  const time = formatHoverTime(message.createdAt);

  if (isUser) {
    return (
      <div className="group flex items-end justify-end gap-2">
        <span className="w-9 shrink-0 pb-1 text-right text-[11px] text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-500">
          {time}
        </span>
        <div className="flex max-w-[75%] flex-col items-end gap-1.5">
          {message.attachments && message.attachments.length > 0 && (
            <div className="flex flex-wrap justify-end gap-1.5">
              {message.attachments.map((a) => (
                <AttachmentChip
                  key={a.id}
                  name={a.original_name}
                  onClick={onAttachmentClick ? () => onAttachmentClick(a.id) : undefined}
                />
              ))}
            </div>
          )}
          {message.content && (
            <div className="whitespace-pre-wrap rounded-2xl bg-gray-100 px-4 py-2.5 text-sm leading-relaxed text-gray-900 dark:bg-gray-800 dark:text-gray-100">
              {message.content}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="group flex flex-col items-start gap-1">
      {message.selectedModel && (
        <ModelBadge
          model={message.selectedModel}
          taskCategory={message.taskCategory}
          complexityScore={message.complexityScore}
          isError={message.isError}
        />
      )}
      <div
        className={`w-full ${
          message.isError
            ? "text-red-700 dark:text-red-400"
            : "text-gray-900 dark:text-gray-100"
        }`}
      >
        <MarkdownContent content={message.content} />
        {isStreaming && (
          <div className="mt-1 flex items-center gap-1.5 text-xs text-gray-400 dark:text-gray-500">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gray-400 dark:bg-gray-500" />
            {message.processingMessage
              ? message.processingMessage
              : message.searchingQuery
                ? `웹 검색 중: ${message.searchingQuery}`
                : "응답 생성 중..."}
          </div>
        )}
      </div>
      <div className="flex h-4 items-center gap-2 text-[11px] leading-4 text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-500">
        <span>{time}</span>
        <CopyButton text={message.content} />
      </div>
    </div>
  );
}
