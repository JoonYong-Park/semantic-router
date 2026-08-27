const MODEL_EMOJI: Record<string, string> = {
  GPT: "🟢",
  Gemini: "🔵",
  Claude: "🟠",
};

export default function ModelBadge({
  model,
  category,
  isError = false,
}: {
  model: string;
  category?: string | null;
  isError?: boolean;
}) {
  const emoji = MODEL_EMOJI[model] ?? "🤖";
  const colorClass = isError
    ? "bg-red-100 text-red-800"
    : "bg-amber-100 text-amber-900";
  const subColorClass = isError
    ? "bg-red-200"
    : "bg-amber-200";

  return (
    <div
      className={`mb-1 inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${colorClass}`}
    >
      <span>
        {emoji} {isError ? `${model}에게 요청했지만 실패했습니다` : `${model}가 응답했습니다`}
      </span>
      {category && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${subColorClass}`}>
          분류: {category}
        </span>
      )}
    </div>
  );
}
