const EXAMPLE_PROMPTS = [
  "점심 메뉴 추천해줘", // 하
  "파이썬으로 이진 탐색 함수를 작성해줘", // 중
  "미토콘드리아의 역할을 설명해줘", // 중
  "분산 시스템에서 발생할 수 있는 데이터 정합성 문제를 CAP 정리 관점에서 분석하고, 실무에서 쓸 수 있는 해결 방안을 제시해줘", // 상
];

export default function EmptyState({
  onPick,
}: {
  onPick: (text: string) => void;
}) {
  return (
    <div className="mt-16 flex flex-col items-center text-center">
      <img src="/icons/npia.png" alt="" className="mb-3 h-12 w-auto" />
      <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
        Navigating Productivity with Intelligent Assistance
      </p>
      <p className="mt-1.5 text-base font-medium text-gray-700 dark:text-gray-200">
        지능형 AI 비서와 함께하는 생산성 극대화
      </p>

      <p className="mt-4 text-sm text-gray-400 dark:text-gray-500">
        무엇을 도와드릴까요?
      </p>

      <div className="mt-6 grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
        {EXAMPLE_PROMPTS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            className="rounded-xl border border-gray-200 px-3 py-2.5 text-left text-xs text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-900"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}
