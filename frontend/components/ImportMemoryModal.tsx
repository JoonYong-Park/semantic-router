"use client";

import { useEffect, useState } from "react";
import { deleteImportedMemory, getSettings, importMemory } from "@/lib/api";

const IMPORT_PROMPT = `지금까지의 대화 기록을 바탕으로 사용자에 대해 파악한 정보를 정리해줘.
다른 AI 어시스턴트로 이 정보를 이전하는 데 사용할 예정이야.

작성 규칙:
- 1인칭(나, 내, 저) 및 2인칭(너, 당신) 표현을 사용하지 마.
  대신 '사용자'라고 표현해.
- 확실하게 확인된 정보만 포함해. 추측이나 단발성 언급은 제외해.
- 사용자가 직접 말한 표현은 가능한 한 원문 그대로 유지해.
- 정보가 없는 카테고리는 '확인된 정보 없음'으로 표시해.
- 서론, 끝맺음, 불필요한 설명 없이 아래 형식만 출력해.

출력 형식 (카테고리 순서 고정):

[기본 정보]
이름, 직업, 소속, 거주 지역 등 기본적인 인적 사항

[관심사 및 선호]
지속적으로 관심을 갖거나 즐기는 활동, 주제, 스타일
(일회성 구매나 단순 언급 제외, 반복적으로 나타난 것만)

[업무 및 프로젝트]
현재 진행 중인 업무, 프로젝트, 목표

[주요 관계]
대화에서 반복적으로 언급된 사람 (가족, 동료, 지인 등)

[요청 사항]
AI에게 명시적으로 요청한 행동 규칙만 포함
('항상 ~해줘', '~하지 마' 형태)
대화 중 일시적 요청이 아닌, 지속적으로 적용되길 원하는 것만`;

// SettingsModal(개인 지침)과 동일하게 단일 화면만 쓴다 - 이미 가져온 데이터가
// 있어도 별도의 "가져옴" 요약 화면 대신, textarea에 그 내용을 그대로 채워서
// 저장 전과 똑같은 폼을 보여준다.
export default function ImportMemoryModal({ onClose }: { onClose: () => void }) {
  const [resultText, setResultText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    getSettings()
      .then((s) => setResultText(s.imported_memory ?? ""))
      .catch(() => {
        // 조회 실패해도 빈 폼으로 그냥 열어둔다
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

  async function handleCopyPrompt() {
    try {
      await navigator.clipboard.writeText(IMPORT_PROMPT);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 접근 실패 시에도 조용히 무시 (프롬프트는 화면에 그대로 보임)
    }
  }

  async function handleSave() {
    if (!resultText.trim()) return;
    setSaving(true);
    try {
      await importMemory(resultText.trim());
      onClose();
    } catch {
      // 저장 실패 시 모달을 열어둬서 재시도할 수 있게 한다
    } finally {
      setSaving(false);
    }
  }

  async function handleReset() {
    if (!confirm("가져온 정보를 삭제하시겠습니까?")) return;
    setSaving(true);
    try {
      await deleteImportedMemory();
      setResultText("");
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
            다른 AI에서 가져오기
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
          사용 중인 AI에서 아래 프롬프트를 복사해서 실행한 후 결과를 붙여넣어 주세요
        </p>

        <p className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
          1. 프롬프트 복사
        </p>
        <div className="relative mb-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 pb-9 dark:border-gray-700 dark:bg-gray-950">
          <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-xs text-gray-600 dark:text-gray-400">
            {IMPORT_PROMPT}
          </pre>
          <button
            type="button"
            onClick={handleCopyPrompt}
            className="absolute bottom-2 right-2 rounded-lg bg-gray-900 px-2.5 py-1 text-xs font-medium text-white dark:bg-white dark:text-gray-900"
          >
            {copied ? "복사됨 ✓" : "복사"}
          </button>
        </div>

        <p className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
          2. 결과 붙여넣기
        </p>
        <textarea
          rows={6}
          value={resultText}
          onChange={(e) => setResultText(e.target.value)}
          disabled={loading || saving}
          placeholder="결과를 여기에 붙여넣어 주세요"
          className="mb-3 w-full resize-none rounded-xl border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none placeholder:text-gray-400 focus:border-gray-400 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 dark:placeholder:text-gray-500"
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
            disabled={loading || saving || !resultText.trim()}
            className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-gray-900"
          >
            저장
          </button>
        </div>
      </div>
    </div>
  );
}
