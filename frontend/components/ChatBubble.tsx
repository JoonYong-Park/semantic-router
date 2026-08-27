import { ChatMessage } from "@/lib/types";
import ModelBadge from "./ModelBadge";

export default function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[75%] ${isUser ? "items-end" : "items-start"} flex flex-col`}>
        {!isUser && message.selectedModel && (
          <ModelBadge
            model={message.selectedModel}
            category={message.category}
            isError={message.isError}
          />
        )}
        <div
          className={`whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
            isUser
              ? "bg-gray-900 text-white rounded-br-sm"
              : message.isError
                ? "bg-red-50 text-red-700 border border-red-200 rounded-bl-sm"
                : "bg-white text-gray-900 border border-gray-200 rounded-bl-sm"
          }`}
        >
          {message.content}
        </div>
      </div>
    </div>
  );
}
