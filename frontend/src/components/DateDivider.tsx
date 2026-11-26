import { formatDateDivider } from "@/lib/dateFormat";

export default function DateDivider({ iso }: { iso: string }) {
  return (
    <div className="my-2 flex items-center gap-3 text-[11px] text-gray-400 dark:text-gray-500">
      <span className="h-px flex-1 bg-gray-100 dark:bg-gray-800" />
      {formatDateDivider(iso)}
      <span className="h-px flex-1 bg-gray-100 dark:bg-gray-800" />
    </div>
  );
}
