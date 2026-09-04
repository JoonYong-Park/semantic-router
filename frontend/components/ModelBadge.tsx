import { COMPANY_ICON, SIZE_LABEL_KO, companyFromLabel, sizeFromLabel } from "@/lib/models";

function CompanyIconImg({ model }: { model: string }) {
  const company = companyFromLabel(model);
  if (!company) return null;

  const icon = COMPANY_ICON[company];
  return (
    <img
      src={icon.src}
      alt={icon.alt}
      className={`h-3.5 w-3.5 shrink-0 ${icon.invertInDark ? "dark:invert" : ""}`}
    />
  );
}

export default function ModelBadge({
  model,
  taskCategory,
  complexityScore,
  isError = false,
}: {
  model: string;
  taskCategory?: string | null;
  complexityScore?: number | null;
  isError?: boolean;
}) {
  const colorClass = isError
    ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
    : "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300";
  const subColorClass = isError
    ? "bg-red-200 dark:bg-red-900"
    : "bg-gray-200 dark:bg-gray-700";
  const size = sizeFromLabel(model);

  return (
    <div
      className={`mb-1 inline-flex flex-wrap items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${colorClass}`}
    >
      <span className="inline-flex items-center gap-1.5">
        <CompanyIconImg model={model} />
        {isError ? `${model}에게 요청했지만 실패했습니다` : model}
        {!isError && size && (
          <span className="font-normal text-gray-400 dark:text-gray-500">
            ({SIZE_LABEL_KO[size]} 모델)
          </span>
        )}
      </span>
      {taskCategory && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${subColorClass}`}>
          {taskCategory}
        </span>
      )}
      {typeof complexityScore === "number" && (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-normal ${subColorClass}`}>
          복잡도: {complexityScore.toFixed(2)}
        </span>
      )}
    </div>
  );
}
