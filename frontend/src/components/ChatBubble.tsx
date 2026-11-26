import { useState } from "react";
import { attachmentFileUrl, downloadAuthed } from "@/lib/api";
import { useAuthedObjectUrl } from "@/lib/useAuthedObjectUrl";
import { AttachmentOut, ChatMessage } from "@/lib/types";
import { formatHoverTime } from "@/lib/dateFormat";
import { AttachmentChip, AttachmentTypeIcon } from "./AttachmentChip";
import MarkdownContent from "./MarkdownContent";
import ModelBadge from "./ModelBadge";
import SourceChips from "./SourceChips";
import { splitSources } from "@/lib/sources";

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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M12 4v11m0 0l-4-4m4 4l4-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M5 19h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

// 모델이 만든 파일 카드 - 카드 본문 클릭은 기존 첨부 뷰어(미리보기), 오른쪽
// 버튼은 다운로드(?download=1, 한글 파일명은 백엔드 Content-Disposition이 처리).
function GeneratedFileCard({
  attachment,
  conversationId,
  onOpen,
}: {
  attachment: AttachmentOut;
  conversationId?: string;
  onOpen?: () => void;
}) {
  const ext = attachment.original_name.split(".").pop()?.toUpperCase() ?? "";
  const isImage = attachment.mime_type.startsWith("image/");
  // 파일 API는 로그인 토큰이 필요해 blob URL로 받아 미리보기에 쓴다.
  const { src: imageSrc } = useAuthedObjectUrl(
    isImage && conversationId ? attachmentFileUrl("chat", conversationId, attachment.id) : null
  );
  return (
    <div className="flex w-full max-w-sm flex-col gap-2">
      {isImage && conversationId && (
        // 생성 이미지는 카드 위에 바로 미리보기 - 클릭하면 기존 뷰어로 크게 본다.
        <button type="button" onClick={onOpen} disabled={!onOpen} className="block overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700">
          {imageSrc ? (
            <img src={imageSrc} alt={attachment.original_name} className="max-h-80 w-full object-contain" />
          ) : (
            <div className="h-40 w-full animate-pulse bg-gray-100 dark:bg-gray-800" />
          )}
        </button>
      )}
    <div className="flex w-full items-center gap-3 rounded-xl border border-gray-200 bg-white px-3 py-2.5 dark:border-gray-700 dark:bg-gray-900">
      <button
        type="button"
        onClick={onOpen}
        disabled={!onOpen}
        className="flex min-w-0 flex-1 items-center gap-3 text-left disabled:cursor-default"
      >
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">
          <AttachmentTypeIcon filename={attachment.original_name} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100" title={attachment.original_name}>
            {attachment.original_name}
          </span>
          <span className="block text-xs text-gray-500 dark:text-gray-400">
            {ext} · {formatFileSize(attachment.size_bytes)}
          </span>
        </span>
      </button>
      {conversationId && (
        <button
          type="button"
          onClick={() =>
            downloadAuthed(attachmentFileUrl("chat", conversationId, attachment.id, true), attachment.original_name).catch(
              (err) => alert(err instanceof Error ? err.message : "다운로드하지 못했습니다.")
            )
          }
          aria-label={`${attachment.original_name} 다운로드`}
          title="다운로드"
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
        >
          <DownloadIcon />
        </button>
      )}
    </div>
    </div>
  );
}

export default function ChatBubble({
  message,
  isStreaming = false,
  onAttachmentClick,
  conversationId,
}: {
  message: ChatMessage;
  isStreaming?: boolean;
  onAttachmentClick?: (attachmentId: string) => void;
  conversationId?: string;
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

  // 답변 끝 "출처:" 목록은 본문에서 빼고 칩으로 보여 준다 (복사 버튼은 원문 그대로)
  const { body, sources } = splitSources(message.content);

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
        <MarkdownContent content={body} />
        <SourceChips sources={sources} />
        {message.attachments && message.attachments.length > 0 && (
          <div className="mt-2 flex flex-col gap-2">
            {message.attachments.map((a) => (
              <GeneratedFileCard
                key={a.id}
                attachment={a}
                conversationId={conversationId}
                onOpen={onAttachmentClick ? () => onAttachmentClick(a.id) : undefined}
              />
            ))}
          </div>
        )}
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
