"use client";

import { useEffect, useRef, useState } from "react";
import { COMPANY_ICON, MODEL_OPTIONS, SIZE_LABEL, labelFor } from "@/lib/models";
import { Mode, Size } from "@/lib/types";

function CompanyIconImg({ mode }: { mode: Exclude<Mode, "auto"> }) {
  const icon = COMPANY_ICON[mode];
  return (
    <img
      src={icon.src}
      alt={icon.alt}
      className={`h-3.5 w-3.5 shrink-0 ${icon.invertInDark ? "dark:invert" : ""}`}
    />
  );
}

const GROUP_TO_MODE: Record<string, Exclude<Mode, "auto">> = {
  OpenAI: "openai",
  Google: "google",
  Anthropic: "anthropic",
};

export default function ModelPicker({
  mode,
  size,
  onSelect,
}: {
  mode: Mode;
  size: Size;
  onSelect: (mode: Mode, size: Size) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const currentLabel = mode === "auto" ? "Auto" : labelFor(mode, size);

  const groups = Array.from(new Set(MODEL_OPTIONS.map((m) => m.group)));

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-1.5 rounded-full border border-gray-300 px-3 text-xs font-medium text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800"
      >
        {mode !== "auto" && <CompanyIconImg mode={mode} />}
        {currentLabel}
        {mode !== "auto" && (
          <span className="text-gray-400 dark:text-gray-500">
            ({SIZE_LABEL[size]})
          </span>
        )}
        <svg
          className={`h-3.5 w-3.5 text-gray-400 transition-transform dark:text-gray-500 ${open ? "" : "rotate-180"}`}
          viewBox="0 0 20 20"
          fill="currentColor"
        >
          <path
            fillRule="evenodd"
            d="M14.77 12.79a.75.75 0 01-1.06-.02L10 8.832l-3.71 3.938a.75.75 0 11-1.08-1.04l4.24-4.5a.75.75 0 011.08 0l4.24 4.5a.75.75 0 01-.02 1.06z"
            clipRule="evenodd"
          />
        </svg>
      </button>

      {open && (
        <div className="absolute bottom-full right-0 z-10 mb-2 w-64 rounded-xl border border-gray-200 bg-white py-1.5 shadow-lg dark:border-gray-700 dark:bg-gray-800">
          <button
            type="button"
            onClick={() => {
              onSelect("auto", size);
              setOpen(false);
            }}
            className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700 ${
              mode === "auto"
                ? "font-semibold text-gray-900 dark:text-white"
                : "text-gray-700 dark:text-gray-300"
            }`}
          >
            <span>Auto</span>
            <span className="text-[11px] text-gray-400 dark:text-gray-500">
              질문 분석 후 자동 선택
            </span>
          </button>

          <div className="my-1 border-t border-gray-100 dark:border-gray-700" />

          {groups.map((group) => (
            <div key={group}>
              <div className="flex items-center gap-1.5 px-3 pb-0.5 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                <CompanyIconImg mode={GROUP_TO_MODE[group]} />
                {group}
              </div>
              {MODEL_OPTIONS.filter((m) => m.group === group).map((opt) => {
                const selected =
                  mode === opt.mode && size === opt.size && mode !== "auto";
                return (
                  <button
                    key={`${opt.mode}-${opt.size}`}
                    type="button"
                    onClick={() => {
                      onSelect(opt.mode, opt.size);
                      setOpen(false);
                    }}
                    className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700 ${
                      selected
                        ? "font-semibold text-gray-900 dark:text-white"
                        : "text-gray-700 dark:text-gray-300"
                    }`}
                  >
                    <span>{opt.label}</span>
                    <span className="text-[11px] font-normal text-gray-400 dark:text-gray-500">
                      {SIZE_LABEL[opt.size]}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
