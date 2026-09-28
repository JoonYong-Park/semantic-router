import { ChatMessage } from "@/lib/types";
import { formatHoverTime } from "@/lib/dateFormat";
import { AttachmentChip } from "./AttachmentChip";
import MarkdownContent from "./MarkdownContent";
import ModelBadge from "./ModelBadge";

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
      <div className="h-4 text-[11px] leading-4 text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-500">
        {time}
      </div>
    </div>
  );
}
