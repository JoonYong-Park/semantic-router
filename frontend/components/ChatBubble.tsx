import { ChatMessage } from "@/lib/types";
import MarkdownContent from "./MarkdownContent";
import ModelBadge from "./ModelBadge";

export default function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[75%] whitespace-pre-wrap rounded-2xl bg-gray-100 px-4 py-2.5 text-sm leading-relaxed text-gray-900 dark:bg-gray-800 dark:text-gray-100">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-1">
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
      </div>
    </div>
  );
}
