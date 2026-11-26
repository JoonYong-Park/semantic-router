// 첨부파일 칩 UI - 입력창(업로드 중/취소 가능)과 전송된 메시지(읽기 전용)
// 양쪽에서 재사용한다.

// 파일명 중간을 잘라도 확장자는 항상 보이게 한다 (예: "아주긴이름...pdf").
// CSS text-overflow만 쓰면 뒤쪽 확장자가 먼저 잘려서 무슨 파일인지 안 보이는
// 문제가 있어 JS로 직접 자른다.
export function truncateFilename(name: string, maxBaseLen = 14): string {
  const dotIdx = name.lastIndexOf(".");
  if (dotIdx <= 0) {
    return name.length > maxBaseLen ? `${name.slice(0, maxBaseLen)}...` : name;
  }
  const base = name.slice(0, dotIdx);
  const ext = name.slice(dotIdx + 1);
  if (base.length <= maxBaseLen) return name;
  return `${base.slice(0, maxBaseLen)}...${ext}`;
}

type AttachmentCategory = "image" | "table" | "document";

function attachmentCategory(filename: string): AttachmentCategory {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp"].includes(ext)) return "image";
  if (["xlsx", "csv"].includes(ext)) return "table";
  return "document";
}

// 앱 전체에서 쓰는 것과 동일한 스타일(viewBox 24, stroke=currentColor,
// strokeWidth 2, round cap/join)로 맞춘 파일 종류 아이콘 - 이모지 대신 사용.
export function AttachmentTypeIcon({ filename }: { filename: string }) {
  const category = attachmentCategory(filename);

  if (category === "image") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 shrink-0">
        <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="2" />
        <circle cx="8.5" cy="9.5" r="1.5" stroke="currentColor" strokeWidth="2" />
        <path
          d="M3 16l5-5 4 4 3-3 6 6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }

  if (category === "table") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 shrink-0">
        <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="2" />
        <line x1="3" y1="10" x2="21" y2="10" stroke="currentColor" strokeWidth="2" />
        <line x1="9" y1="10" x2="9" y2="20" stroke="currentColor" strokeWidth="2" />
        <line x1="15" y1="10" x2="15" y2="20" stroke="currentColor" strokeWidth="2" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5 shrink-0">
      <path
        d="M7 3h7l4 4v13a1 1 0 01-1 1H7a1 1 0 01-1-1V4a1 1 0 011-1z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M14 3v4h4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="9" y1="13" x2="15" y2="13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="9" y1="17" x2="15" y2="17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

export function AttachmentChip({
  name,
  progress,
  error,
  onRemove,
  onClick,
}: {
  name: string;
  progress?: number; // 지정하면 업로드 중 표시 (0~100)
  error?: string; // 지정하면 실패 표시
  onRemove?: () => void; // 지정하면 취소(x) 버튼 표시
  onClick?: () => void; // 지정하면 클릭해서 미리보기 뷰어를 열 수 있게 함
}) {
  const inner = (
    <>
      <AttachmentTypeIcon filename={name} />
      <span title={name}>{truncateFilename(name)}</span>
      {progress !== undefined && <span className="text-gray-400">{progress}%</span>}
      {error && (
        <span className="text-red-500" title={error}>
          실패
        </span>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={(e) => {
            // 칩 자체의 onClick(미리보기 열기)과 취소 버튼 클릭이 서로
            // 간섭하지 않도록 이벤트 전파를 막는다.
            e.stopPropagation();
            onRemove();
          }}
          aria-label={`${name} 첨부 취소`}
          className="flex h-4 w-4 items-center justify-center rounded-full text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700"
        >
          ×
        </button>
      )}
    </>
  );

  const className =
    "flex items-center gap-1.5 rounded-full border border-gray-200 bg-gray-50 py-1 pl-2.5 pr-1.5 text-xs text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300";

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`${className} cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-800`}
      >
        {inner}
      </button>
    );
  }

  return <div className={className}>{inner}</div>;
}
