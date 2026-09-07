"use client";

import { useEffect, useState } from "react";
import { deleteInstruction, getSettings, updateSettings } from "@/lib/api";

const PLACEHOLDER = `예시:
저는 SCM팀 담당자입니다.
전문 용어는 풀어서 설명해주세요.
답변은 짧고 핵심만 알려주세요.
표나 번호 목록 형식을 선호합니다.`;

export default function SettingsModal({ onClose }: { onClose: () => void }) {
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => setInstruction(s.personal_instruction ?? ""))
      .catch(() => {
        // 조회 실패해도 빈 textarea로 그냥 열어둔다 (재입력해서 저장하면 됨)
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  async function handleSave() {
    setSaving(true);
    try {
      await updateSettings(instruction);
      onClose();
    } catch {
      // 저장 실패 시 모달을 열어둬서 재시도할 수 있게 한다
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!confirm("개인 지침을 삭제하시겠습니까?")) return;
    setSaving(true);
    try {
      await deleteInstruction();
      setInstruction("");
      onClose();
    } catch {
      // 삭제 실패 시 모달을 열어둬서 재시도할 수 있게 한다
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            개인 지침
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
          NPIA가 나에 대해 알아야 할 것과 원하는 답변 방식을 알려주세요
        </p>

        <textarea
          rows={6}
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          disabled={loading || saving}
          placeholder={PLACEHOLDER}
          className="w-full resize-none rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:border-gray-400 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:placeholder:text-gray-500"
        />

        <div className="mt-4 flex items-center justify-between">
          <button
            type="button"
            onClick={handleReset}
            disabled={loading || saving}
            className="rounded-lg px-3 py-2 text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800"
          >
            초기화
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={loading || saving}
            className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-gray-900"
          >
            저장
          </button>
        </div>
      </div>
    </div>
  );
}
