import { ChatMessage } from "@/lib/types";
import { formatHoverTime } from "@/lib/dateFormat";
import MarkdownContent from "./MarkdownContent";
import ModelBadge from "./ModelBadge";

export default function ChatBubble({
  message,
  isStreaming = false,
}: {
  message: ChatMessage;
  isStreaming?: boolean;
}) {
  const isUser = message.role === "user";
  const time = formatHoverTime(message.createdAt);

  if (isUser) {
    return (
      <div className="group flex items-end justify-end gap-2">
        <span className="w-9 shrink-0 pb-1 text-right text-[11px] text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-500">
          {time}
        </span>
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-gray-100 px-4 py-2.5 text-sm leading-relaxed text-gray-900 dark:bg-gray-800 dark:text-gray-100">
          {message.content}
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
            응답 생성 중...
          </div>
        )}
      </div>
      <div className="h-4 text-[11px] leading-4 text-gray-400 opacity-0 transition-opacity group-hover:opacity-100 dark:text-gray-500">
        {time}
      </div>
    </div>
  );
}
