"use client";

import { useEffect, useState } from "react";
import { deleteMemoryFact, getMemoryOverview } from "@/lib/api";
import { MemoryFact, MemoryOverview } from "@/lib/types";

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={`h-4 w-4 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
    >
      <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M4 7h16M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3m2 0v13a1 1 0 01-1 1H8a1 1 0 01-1-1V7h10z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MemoryRow({
  memory,
  onDelete,
}: {
  memory: MemoryFact;
  onDelete: (id: string) => void;
}) {
  return (
    <li className="group flex items-start justify-between gap-2 rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800/60">
      <span className="flex-1 text-sm text-gray-700 dark:text-gray-300">{memory.fact}</span>
      <button
        type="button"
        onClick={() => onDelete(memory.id)}
        aria-label="메모리 삭제"
        className="mt-0.5 shrink-0 text-gray-300 opacity-0 hover:text-red-600 group-hover:opacity-100 dark:text-gray-600 dark:hover:text-red-400"
      >
        <TrashIcon />
      </button>
    </li>
  );
}

export default function MemoryModal({
  onClose,
  initialOpenProjectId,
}: {
  onClose: () => void;
  // 지정하면 그 프로젝트의 메모리 섹션을 펼친 채로 열고 스크롤해서 보여준다
  // (프로젝트 페이지의 "메모리 바로가기" 버튼용).
  initialOpenProjectId?: string | null;
}) {
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [openProjects, setOpenProjects] = useState<Set<string>>(
    initialOpenProjectId ? new Set([initialOpenProjectId]) : new Set()
  );

  useEffect(() => {
    getMemoryOverview()
      .then(setOverview)
      .catch(() => setOverview(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!initialOpenProjectId || loading) return;
    document
      .getElementById(`memory-project-${initialOpenProjectId}`)
      ?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  function toggleProject(id: string) {
    setOpenProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleDelete(id: string) {
    // 실패해도 다른 모달들과 동일하게 UI는 이미 지운 상태로 둔다(데모 범위).
    setOverview((prev) => {
      if (!prev) return prev;
      return {
        global: prev.global.filter((m) => m.id !== id),
        projects: prev.projects.map((p) => ({
          ...p,
          memories: p.memories.filter((m) => m.id !== id),
        })),
      };
    });
    deleteMemoryFact(id).catch(() => {
      // 조용히 무시
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-5 shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">메모리</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <CloseIcon />
          </button>
        </div>

        {loading && (
          <p className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
        )}

        {!loading && overview && (
          <div className="flex flex-col gap-5">
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                전체 채팅에서 추출된 메모리
              </h3>
              {overview.global.length === 0 ? (
                <p className="py-3 text-center text-sm text-gray-400 dark:text-gray-500">
                  아직 추출된 메모리가 없습니다
                </p>
              ) : (
                <ul className="space-y-0.5">
                  {overview.global.map((m) => (
                    <MemoryRow key={m.id} memory={m} onDelete={handleDelete} />
                  ))}
                </ul>
              )}
            </div>

            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                프로젝트별 메모리
              </h3>
              {overview.projects.length === 0 ? (
                <p className="py-3 text-center text-sm text-gray-400 dark:text-gray-500">
                  아직 프로젝트가 없습니다
                </p>
              ) : (
                <ul className="space-y-1">
                  {overview.projects.map((p) => {
                    const open = openProjects.has(p.project_id);
                    return (
                      <li
                        key={p.project_id}
                        id={`memory-project-${p.project_id}`}
                        className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700"
                      >
                        <button
                          type="button"
                          onClick={() => toggleProject(p.project_id)}
                          className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-gray-700 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800/60"
                        >
                          <ChevronIcon open={open} />
                          <span className="flex-1 truncate">{p.project_name}</span>
                          <span className="text-xs font-normal text-gray-400 dark:text-gray-500">
                            {p.memories.length}개
                          </span>
                        </button>
                        {open && (
                          <div className="border-t border-gray-100 px-3 py-2 dark:border-gray-800">
                            {p.memories.length === 0 ? (
                              <p className="py-2 text-center text-xs text-gray-400 dark:text-gray-500">
                                아직 추출된 메모리가 없습니다
                              </p>
                            ) : (
                              <ul className="space-y-0.5">
                                {p.memories.map((m) => (
                                  <MemoryRow key={m.id} memory={m} onDelete={handleDelete} />
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
