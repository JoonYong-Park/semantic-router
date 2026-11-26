// 답변 끝 출처를 칩으로 보여 준다: [아이콘 사이트이름 +N].
// 출처가 하나면 칩을 누르면 바로 그 페이지로, 여러 개면 목록을 펼쳐서 고른다.
import { useEffect, useRef, useState } from "react";
import { faviconUrl, Source } from "@/lib/sources";

function Favicon({ host }: { host: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-gray-300 text-[9px] font-semibold uppercase text-gray-700 dark:bg-gray-600 dark:text-gray-100">
        {host.replace(/^www\./, "")[0]}
      </span>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={faviconUrl(host)}
      alt=""
      width={16}
      height={16}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className="h-4 w-4 shrink-0 rounded-full bg-white object-contain"
    />
  );
}

const CHIP =
  "inline-flex max-w-[16rem] items-center gap-1.5 rounded-full bg-gray-100 py-1 pl-1.5 pr-2.5 text-xs text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700";

export default function SourceChips({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (sources.length === 0) return null;
  const first = sources[0];
  const label = (
    <>
      <Favicon host={first.host} />
      <span className="truncate">{first.siteName}</span>
      {sources.length > 1 && <span className="shrink-0 text-gray-500 dark:text-gray-400">+{sources.length - 1}</span>}
    </>
  );

  return (
    <div ref={ref} className="relative mt-2 inline-block">
      {sources.length === 1 ? (
        <a href={first.url} target="_blank" rel="noreferrer" title={first.title} className={CHIP}>
          {label}
        </a>
      ) : (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`출처 ${sources.length}개 보기`}
          className={CHIP}
        >
          {label}
        </button>
      )}

      {open && (
        <div className="absolute left-0 z-30 mt-1 w-80 max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-900">
          {sources.map((s) => (
            <a
              key={s.url}
              href={s.url}
              target="_blank"
              rel="noreferrer"
              onClick={() => setOpen(false)}
              className="flex items-start gap-2.5 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-800"
            >
              <span className="mt-0.5">
                <Favicon host={s.host} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm text-gray-900 dark:text-gray-100">{s.title}</span>
                <span className="block truncate text-xs text-gray-500 dark:text-gray-400">{s.siteName}</span>
              </span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
