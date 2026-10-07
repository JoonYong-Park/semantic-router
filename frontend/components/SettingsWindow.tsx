"use client";

// 예전엔 사이드바 "설정" 버튼 → 드롭다운 메뉴 → 항목 클릭 → 각자 따로 뜨는
// 모달(SettingsModal/ImportMemoryModal/UsageStatsModal/MemoryModal) 구조였다.
// 이 컴포넌트가 그 네 개(+다크모드 토글)를 "설정"이라는 창 하나 안의 탭으로
// 통합한다 - 왼쪽에 탭 목록, 오른쪽에 선택된 탭의 내용. 오버레이 클릭/Escape로
// 닫는 것도 여기 하나에서만 처리하고, 개별 탭은 더 이상 자기 창을 안 가진다.
import { useEffect, useRef, useState } from "react";
import {
  deleteImportedMemory,
  deleteAvatar,
  deleteInstruction,
  updateCurrentUser,
  uploadAvatar,
  deleteMemoryFact,
  getMemoryOverview,
  getSettings,
  getUsageQuota,
  getUsageStats,
  importMemory,
  updateMemoryTopic,
  updateSettings,
} from "@/lib/api";
import UserAvatar from "@/components/UserAvatar";
import { formatHoverTime, formatMonthDay } from "@/lib/dateFormat";
import {
  MemoryCategory,
  MemoryOverview,
  MemoryTopic,
  UsagePeriod,
  UsageQuota,
  UsageStats,
  User,
} from "@/lib/types";
import UsageChart from "./UsageChart";

export type SettingsTab = "account" | "personal" | "import" | "usage" | "memory" | "appearance";

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
      <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <rect x="9" y="9" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
      <path
        d="M5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path d="M5 13l4 4L19 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FileTextIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path
        d="M14 3H7a1 1 0 00-1 1v16a1 1 0 001 1h10a1 1 0 001-1V8l-5-5z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M14 3v5h5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="9" y1="13" x2="15" y2="13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="9" y1="17" x2="15" y2="17" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function ArrowDownToLineIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path d="M12 4v11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M7 11l5 5 5-5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1="5" y1="20" x2="19" y2="20" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function BarChartIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <line x1="5" y1="20" x2="5" y2="10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="12" y1="20" x2="12" y2="4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <line x1="19" y1="20" x2="19" y2="14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function BrainIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path
        d="M9 4a3 3 0 00-3 3v1a3 3 0 00-2 2.8V13a3 3 0 002 2.8V17a3 3 0 003 3h1M15 4a3 3 0 013 3v1a3 3 0 012 2.8V13a3 3 0 01-2 2.8V17a3 3 0 01-3 3h-1M12 4v16"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="2" />
      <path
        d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
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

function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="2" />
      <path d="M4 21a8 8 0 0116 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

const TABS: { id: SettingsTab; label: string; icon: () => JSX.Element }[] = [
  { id: "account", label: "계정 설정", icon: UserIcon },
  { id: "personal", label: "개인 지침", icon: FileTextIcon },
  { id: "import", label: "다른 AI에서 가져오기", icon: ArrowDownToLineIcon },
  { id: "usage", label: "토큰 사용량", icon: BarChartIcon },
  { id: "memory", label: "메모리", icon: BrainIcon },
  { id: "appearance", label: "화면", icon: MoonIcon },
];

const PERSONAL_PLACEHOLDER = `예시:
저는 NRS GLOBAL SCM팀 담당자입니다.
전문 용어는 풀어서 설명해주세요.
답변은 짧고 핵심만 알려주세요.
표나 번호 목록 형식을 선호합니다.`;

// 계정 정보 - 이름은 수정할 수 있고, 이메일은 계정 식별자라 변경할 수 없다(읽기 전용 표시).
function AccountPanel({
  user,
  onUserChange,
}: {
  user: User | null;
  onUserChange: (user: User) => void;
}) {
  const [name, setName] = useState(user?.name ?? "");
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function showFlash(message: string) {
    setFlash(message);
    setTimeout(() => setFlash(null), 2500);
  }

  async function handleAvatarFile(file: File | undefined) {
    if (!file) return;
    setAvatarBusy(true);
    try {
      onUserChange(await uploadAvatar(file));
    } catch (e) {
      showFlash(e instanceof Error ? e.message : "사진을 올리지 못했습니다.");
    } finally {
      setAvatarBusy(false);
      // 같은 파일을 다시 골라도 change가 발생하도록 비운다.
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleAvatarDelete() {
    setAvatarBusy(true);
    try {
      onUserChange(await deleteAvatar());
    } catch {
      showFlash("사진을 삭제하지 못했습니다.");
    } finally {
      setAvatarBusy(false);
    }
  }

  // 사용자 정보가 탭이 열린 뒤에 도착하는 경우(첫 로딩)에도 입력칸에 채운다.
  useEffect(() => {
    setName(user?.name ?? "");
  }, [user?.name]);

  const trimmed = name.trim();
  const changed = user !== null && trimmed !== "" && trimmed !== user.name;

  async function handleSave() {
    if (!changed) return;
    setSaving(true);
    try {
      onUserChange(await updateCurrentUser({ name: trimmed }));
      showFlash("저장됨");
    } catch {
      showFlash("저장 실패");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">계정 설정</h3>
      <div className="flex max-w-md flex-col gap-4">
        <div className="flex items-center gap-4">
          <UserAvatar user={user} size="h-16 w-16" textSize="text-2xl" />
          <div className="flex flex-col items-start gap-1">
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={user === null || avatarBusy}
                className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 disabled:opacity-40 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
              >
                {avatarBusy ? "처리 중..." : "사진 변경"}
              </button>
              {user?.avatar_updated_at && (
                <button
                  type="button"
                  onClick={handleAvatarDelete}
                  disabled={avatarBusy}
                  className="rounded-lg px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-800"
                >
                  삭제
                </button>
              )}
            </div>
            <p className="text-xs text-gray-400 dark:text-gray-500">PNG, JPG, WEBP 등 · 최대 5MB</p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => handleAvatarFile(e.target.files?.[0])}
          />
        </div>
        <div>
          <label htmlFor="account-name" className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
            이름
          </label>
          <input
            id="account-name"
            type="text"
            value={name}
            maxLength={100}
            disabled={user === null || saving}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleSave();
            }}
            className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-gray-400 disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
          />
        </div>
        <div>
          <label htmlFor="account-email" className="mb-1 block text-xs font-medium text-gray-500 dark:text-gray-400">
            이메일
          </label>
          <div className="relative">
            <input
              id="account-email"
              type="email"
              value={user?.email ?? ""}
              readOnly
              disabled
              title="이메일은 변경할 수 없습니다"
              className="w-full cursor-not-allowed rounded-lg border border-gray-200 bg-gray-100 px-3 py-2 pr-9 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-800/60 dark:text-gray-400"
            />
            <svg
              viewBox="0 0 24 24"
              fill="none"
              className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400 dark:text-gray-500"
            >
              <rect x="5" y="11" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="2" />
              <path d="M8 11V8a4 4 0 118 0v3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </div>
          <p className="mt-1 text-xs text-gray-400 dark:text-gray-500">이메일은 변경할 수 없습니다.</p>
        </div>
      </div>
      <div className="mt-4 flex max-w-md items-center justify-end gap-2">
        {flash && <span className="text-xs text-gray-400 dark:text-gray-500">{flash}</span>}
        <button
          type="button"
          onClick={handleSave}
          disabled={!changed || saving}
          className="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40 dark:bg-white dark:text-gray-900"
        >
          저장
        </button>
      </div>
    </div>
  );
}

function PersonalInstructionPanel() {
  const [instruction, setInstruction] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [flash, setFlash] = useState<"저장됨" | "삭제됨" | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setInstruction(s.personal_instruction ?? ""))
      .catch(() => {
        // 조회 실패해도 빈 textarea로 그냥 열어둔다 (재입력해서 저장하면 됨)
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    setSaving(true);
    try {
      await updateSettings({ personal_instruction: instruction });
      setFlash("저장됨");
      setTimeout(() => setFlash(null), 1500);
    } catch {
      // 저장 실패 시 입력값은 그대로 둬서 다시 시도할 수 있게 한다
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
      setFlash("삭제됨");
      setTimeout(() => setFlash(null), 1500);
    } catch {
      // 삭제 실패 시 그대로 둬서 다시 시도할 수 있게 한다
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">개인 지침</h3>
      <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
        ASAP가 나에 대해 알아야 할 것과 원하는 답변 방식을 알려주세요
      </p>
      <textarea
        rows={10}
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        disabled={loading || saving}
        placeholder={PERSONAL_PLACEHOLDER}
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
        <div className="flex items-center gap-2">
          {flash && <span className="text-xs text-gray-400 dark:text-gray-500">{flash}</span>}
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

function ImportMemoryPanel() {
  const [resultText, setResultText] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [flash, setFlash] = useState<"저장됨" | "삭제됨" | null>(null);

  useEffect(() => {
    getSettings()
      .then((s) => setResultText(s.imported_memory ?? ""))
      .catch(() => {
        // 조회 실패해도 빈 폼으로 그냥 열어둔다
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleCopyPrompt() {
    try {
      await navigator.clipboard.writeText(IMPORT_PROMPT);
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    } catch {
      // 클립보드 접근 실패 시에도 조용히 무시 (프롬프트는 화면에 그대로 보임)
    }
  }

  async function handleSave() {
    if (!resultText.trim()) return;
    setSaving(true);
    try {
      await importMemory(resultText.trim());
      setFlash("저장됨");
      setTimeout(() => setFlash(null), 1500);
    } catch {
      // 저장 실패 시 그대로 둬서 다시 시도할 수 있게 한다
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
      setFlash("삭제됨");
      setTimeout(() => setFlash(null), 1500);
    } catch {
      // 삭제 실패 시 그대로 둬서 다시 시도할 수 있게 한다
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">
        다른 AI에서 가져오기
      </h3>
      <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
        사용 중인 AI에서 아래 프롬프트를 복사해서 실행한 후 결과를 붙여넣어 주세요
      </p>

      <p className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">1. 프롬프트 복사</p>
      <div className="relative mb-3 rounded-xl border border-gray-200 bg-gray-50 px-3 py-2 pb-9 dark:border-gray-700 dark:bg-gray-950">
        <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap text-xs text-gray-600 dark:text-gray-400">
          {IMPORT_PROMPT}
        </pre>
        <button
          type="button"
          onClick={handleCopyPrompt}
          aria-label={copied ? "복사됨" : "복사"}
          className="absolute bottom-2 right-2 flex items-center gap-1 rounded-lg bg-gray-900 px-2.5 py-1 text-xs font-medium text-white dark:bg-white dark:text-gray-900"
        >
          {copied ? <CheckIcon /> : <CopyIcon />}
          {copied ? "복사됨" : "복사"}
        </button>
      </div>

      <p className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">2. 결과 붙여넣기</p>
      <textarea
        rows={8}
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
        <div className="flex items-center gap-2">
          {flash && <span className="text-xs text-gray-400 dark:text-gray-500">{flash}</span>}
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

const PERIOD_TABS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "오늘" },
  { value: "week", label: "주간" },
  { value: "month", label: "월간" },
  { value: "year", label: "연간" },
];

function usageColors(percent: number): { bar: string; text: string } {
  if (percent >= 90) return { bar: "bg-red-600", text: "text-red-700 dark:text-red-400" };
  if (percent >= 70) return { bar: "bg-amber-600", text: "text-amber-700 dark:text-amber-400" };
  return { bar: "bg-[#5B8DB8]", text: "text-[#5B8DB8]" };
}

function UsagePanel() {
  const [quota, setQuota] = useState<UsageQuota | null>(null);
  const [period, setPeriod] = useState<UsagePeriod>("today");
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getUsageQuota()
      .then(setQuota)
      .catch(() => {
        // 조회 실패해도 아래 기간별 통계는 별개로 계속 시도한다
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    getUsageStats(period)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
  }, [period]);

  const colors = usageColors(quota?.percent ?? 0);

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">토큰 사용량</h3>

      {quota && (
        <div className="mb-6 rounded-xl bg-gray-50 p-4 dark:bg-gray-800/60">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span className="font-medium text-gray-700 dark:text-gray-300">사용한 토큰</span>
            <span className="text-xs text-gray-400 dark:text-gray-500">
              {formatMonthDay(quota.cycle_start)} ~ {formatMonthDay(quota.cycle_end)} · 갱신까지{" "}
              {quota.days_left}일
            </span>
          </div>
          <p className="mb-2 text-[28px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
            {quota.used.toLocaleString()}
            <span className="ml-1 text-[13px] font-normal text-gray-400 dark:text-gray-500">
              / {quota.limit.toLocaleString()} 토큰
            </span>
          </p>
          <div className="mb-1 h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
            <div
              className={`h-full rounded-full ${colors.bar}`}
              style={{ width: `${Math.min(quota.percent, 100)}%` }}
            />
          </div>
          <p className={`text-xs ${colors.text}`}>
            {quota.percent}% 사용 · {quota.remaining.toLocaleString()} 토큰 남음
          </p>
          {quota.percent > 90 && (
            <p className="mt-2 text-xs text-red-700 dark:text-red-400">
              이번 결제 주기 토큰 사용량이 90%를 초과했습니다.
            </p>
          )}
        </div>
      )}

      <div className="mb-4 flex gap-1">
        {PERIOD_TABS.map((tab) => (
          <button
            key={tab.value}
            type="button"
            onClick={() => setPeriod(tab.value)}
            className={`rounded-full px-3.5 py-1.5 text-sm transition-colors ${
              period === tab.value
                ? "bg-gray-100 font-medium text-gray-900 dark:bg-gray-800 dark:text-white"
                : "text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800/60"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {stats && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
              <p className="text-xs text-gray-400 dark:text-gray-500">입력 토큰</p>
              <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                {stats.summary.input_tokens.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
              <p className="text-xs text-gray-400 dark:text-gray-500">출력 토큰</p>
              <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                {stats.summary.output_tokens.toLocaleString()}
              </p>
            </div>
            <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
              <p className="text-xs text-gray-400 dark:text-gray-500">총 토큰</p>
              <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                {stats.summary.total_tokens.toLocaleString()}
              </p>
            </div>
          </div>

          <UsageChart period={period} data={stats.chart} />

          {stats.by_model.length === 0 ? (
            <p className="py-8 text-center text-[13px] text-gray-400 dark:text-gray-500">
              아직 사용 기록이 없습니다
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-xs text-gray-400 dark:border-gray-700 dark:text-gray-500">
                    <th className="py-2 font-medium">모델</th>
                    <th className="py-2 font-medium">호출 횟수</th>
                    <th className="py-2 font-medium">입력</th>
                    <th className="py-2 font-medium">출력</th>
                    <th className="py-2 font-medium">합계</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_model.map((m) => (
                    <tr
                      key={m.model}
                      className="border-b border-gray-100 text-gray-700 dark:border-gray-800 dark:text-gray-300"
                    >
                      <td className="py-2">{m.model}</td>
                      <td className="py-2">{m.calls}</td>
                      <td className="py-2">{m.input.toLocaleString()}</td>
                      <td className="py-2">{m.output.toLocaleString()}</td>
                      <td className="py-2 font-medium">{m.total.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {loading && !stats && (
        <p className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
      )}
    </div>
  );
}

const MEMORY_CATEGORY_LABELS: Record<MemoryCategory, string> = {
  나: "나",
  주제: "주제",
  영역: "영역",
  사람: "사람",
};

type MemorySelection = { scope: "global" | { projectId: string }; topic: MemoryTopic };

function MemoryTopicRow({
  topic,
  onOpen,
  onDelete,
}: {
  topic: MemoryTopic;
  onOpen: () => void;
  onDelete: () => void;
}) {
  return (
    <tr
      onClick={onOpen}
      className="group cursor-pointer border-t border-gray-100 hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-800/60"
    >
      <td className="px-2 py-1.5 text-sm text-gray-700 dark:text-gray-300">{topic.name}</td>
      <td className="max-w-0 truncate px-2 py-1.5 text-sm text-gray-500 dark:text-gray-400">
        {topic.summary}
      </td>
      <td className="whitespace-nowrap px-2 py-1.5 text-xs text-gray-400 dark:text-gray-500">
        {formatHoverTime(topic.updated_at)}
      </td>
      <td className="px-2 py-1.5">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          aria-label="메모리 삭제"
          className="shrink-0 text-gray-300 opacity-0 hover:text-red-600 group-hover:opacity-100 dark:text-gray-600 dark:hover:text-red-400"
        >
          <TrashIcon />
        </button>
      </td>
    </tr>
  );
}

function MemoryTopicTable({
  topics,
  onOpen,
  onDelete,
}: {
  topics: MemoryTopic[];
  onOpen: (topic: MemoryTopic) => void;
  onDelete: (id: string) => void;
}) {
  if (topics.length === 0) {
    return (
      <p className="py-3 text-center text-sm text-gray-400 dark:text-gray-500">
        아직 추출된 메모리가 없습니다
      </p>
    );
  }
  return (
    <table className="w-full table-fixed border-collapse">
      <tbody>
        {topics.map((t) => (
          <MemoryTopicRow key={t.id} topic={t} onOpen={() => onOpen(t)} onDelete={() => onDelete(t.id)} />
        ))}
      </tbody>
    </table>
  );
}

function MemoryTopicDetail({
  selection,
  onBack,
  onChanged,
  onDelete,
}: {
  selection: MemorySelection;
  onBack: () => void;
  onChanged: (topic: MemoryTopic) => void;
  onDelete: (id: string) => void;
}) {
  const { topic } = selection;
  const [summary, setSummary] = useState(topic.summary);
  const [details, setDetails] = useState<string[]>(topic.details);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setSummary(topic.summary);
    setDetails(topic.details);
  }, [topic]);

  async function save(nextSummary: string, nextDetails: string[]) {
    setSaving(true);
    try {
      const updated = await updateMemoryTopic(topic.id, { summary: nextSummary, details: nextDetails });
      onChanged(updated);
    } catch {
      // 저장 실패해도 입력값은 그대로 둬서 다시 blur하면 재시도됨
    } finally {
      setSaving(false);
    }
  }

  function handleSummaryBlur() {
    if (summary === topic.summary) return;
    save(summary, details);
  }

  function handleDetailBlur(nextDetails: string[]) {
    if (JSON.stringify(nextDetails) === JSON.stringify(topic.details)) return;
    save(summary, nextDetails);
  }

  function updateDetail(index: number, value: string) {
    setDetails((prev) => prev.map((d, i) => (i === index ? value : d)));
  }

  function removeDetail(index: number) {
    const next = details.filter((_, i) => i !== index);
    setDetails(next);
    handleDetailBlur(next);
  }

  function addDetail() {
    setDetails((prev) => [...prev, ""]);
  }

  return (
    <div>
      <button
        type="button"
        onClick={onBack}
        className="mb-3 flex items-center gap-1 text-sm text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
      >
        <ChevronIcon open={false} />
        목록으로
      </button>

      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{topic.name}</h3>
        {saving && <span className="text-xs text-gray-400 dark:text-gray-500">저장 중...</span>}
      </div>
      <p className="mb-4 text-xs text-gray-400 dark:text-gray-500">
        마지막 업데이트: {formatHoverTime(topic.updated_at)}
      </p>

      <div className="mb-4">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
          요약
        </h4>
        <textarea
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          onBlur={handleSummaryBlur}
          rows={2}
          className="w-full resize-none rounded-lg border border-gray-200 bg-transparent px-2 py-1.5 text-sm text-gray-700 outline-none focus:border-gray-400 dark:border-gray-700 dark:text-gray-300 dark:focus:border-gray-500"
        />
      </div>

      <div className="mb-4">
        <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
          세부 정보
        </h4>
        <ul className="space-y-1.5">
          {details.map((d, i) => (
            <li key={i} className="group flex items-start gap-1.5">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-gray-300 dark:bg-gray-600" />
              <textarea
                value={d}
                onChange={(e) => updateDetail(i, e.target.value)}
                onBlur={() => handleDetailBlur(details)}
                rows={1}
                className="flex-1 resize-none rounded-lg border border-transparent bg-transparent px-1.5 py-1 text-sm text-gray-700 outline-none hover:border-gray-200 focus:border-gray-400 dark:text-gray-300 dark:hover:border-gray-700 dark:focus:border-gray-500"
              />
              <button
                type="button"
                onClick={() => removeDetail(i)}
                aria-label="세부 정보 삭제"
                className="mt-1 shrink-0 text-gray-300 opacity-0 hover:text-red-600 group-hover:opacity-100 dark:text-gray-600 dark:hover:text-red-400"
              >
                <TrashIcon />
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={addDetail}
          className="mt-2 text-xs text-gray-400 hover:text-gray-700 dark:text-gray-500 dark:hover:text-gray-200"
        >
          + 세부 정보 추가
        </button>
      </div>

      <button
        type="button"
        onClick={() => onDelete(topic.id)}
        className="text-sm text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300"
      >
        이 메모리 삭제
      </button>
    </div>
  );
}

function MemoryPanel({ initialOpenProjectId }: { initialOpenProjectId?: string | null }) {
  const [overview, setOverview] = useState<MemoryOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [openProjects, setOpenProjects] = useState<Set<string>>(
    initialOpenProjectId ? new Set([initialOpenProjectId]) : new Set()
  );
  const [selection, setSelection] = useState<MemorySelection | null>(null);

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

  function toggleProject(id: string) {
    setOpenProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleDelete(id: string) {
    // 실패해도 다른 설정 탭들과 동일하게 UI는 이미 지운 상태로 둔다(데모 범위).
    setOverview((prev) => {
      if (!prev) return prev;
      return {
        global: prev.global.map((g) => ({ ...g, topics: g.topics.filter((t) => t.id !== id) })),
        projects: prev.projects.map((p) => ({
          ...p,
          topics: p.topics.filter((t) => t.id !== id),
        })),
      };
    });
    setSelection((prev) => (prev && prev.topic.id === id ? null : prev));
    deleteMemoryFact(id).catch(() => {
      // 조용히 무시
    });
  }

  function handleTopicChanged(updated: MemoryTopic) {
    setOverview((prev) => {
      if (!prev) return prev;
      return {
        global: prev.global.map((g) => ({
          ...g,
          topics: g.topics.map((t) => (t.id === updated.id ? updated : t)),
        })),
        projects: prev.projects.map((p) => ({
          ...p,
          topics: p.topics.map((t) => (t.id === updated.id ? updated : t)),
        })),
      };
    });
    setSelection((prev) => (prev ? { ...prev, topic: updated } : prev));
  }

  if (selection) {
    return (
      <MemoryTopicDetail
        selection={selection}
        onBack={() => setSelection(null)}
        onChanged={handleTopicChanged}
        onDelete={handleDelete}
      />
    );
  }

  return (
    <div>
      <h3 className="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">메모리</h3>

      {loading && (
        <p className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
      )}

      {!loading && overview && (
        <div className="flex flex-col gap-5">
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              전체 채팅에서 추출된 메모리
            </h4>
            <div className="flex flex-col gap-4">
              {overview.global.map((group) => (
                <div key={group.category}>
                  <h5 className="mb-1 text-xs font-medium text-gray-500 dark:text-gray-400">
                    {MEMORY_CATEGORY_LABELS[group.category]}
                  </h5>
                  <MemoryTopicTable
                    topics={group.topics}
                    onOpen={(topic) => setSelection({ scope: "global", topic })}
                    onDelete={handleDelete}
                  />
                </div>
              ))}
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              프로젝트별 메모리
            </h4>
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
                          {p.topics.length}개
                        </span>
                      </button>
                      {open && (
                        <div className="border-t border-gray-100 px-3 py-2 dark:border-gray-800">
                          <MemoryTopicTable
                            topics={p.topics}
                            onOpen={(topic) => setSelection({ scope: { projectId: p.project_id }, topic })}
                            onDelete={handleDelete}
                          />
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
  );
}

function AppearancePanel() {
  const [theme, setThemeState] = useState<"light" | "dark">("light");

  useEffect(() => {
    setThemeState(document.documentElement.classList.contains("dark") ? "dark" : "light");
  }, []);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setThemeState(next);
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem("theme", next);
    } catch {
      // 프라이빗 브라우징 등에서 localStorage가 막혀있어도 화면 전환 자체는 되게 무시
    }
  }

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">화면</h3>
      <p className="mb-4 text-sm text-gray-500 dark:text-gray-400">라이트/다크 모드를 전환합니다.</p>
      <div className="flex items-center justify-between rounded-xl border border-gray-200 px-4 py-3 dark:border-gray-700">
        <div className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          {theme === "dark" ? <MoonIcon /> : <SunIcon />}
          {theme === "dark" ? "다크 모드" : "라이트 모드"}
        </div>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label="테마 전환"
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
            theme === "dark" ? "bg-gray-900 dark:bg-gray-100" : "bg-gray-300 dark:bg-gray-600"
          }`}
        >
          <span
            className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform dark:bg-gray-900 ${
              theme === "dark" ? "translate-x-5" : "translate-x-0"
            }`}
          />
        </button>
      </div>
    </div>
  );
}

export default function SettingsWindow({
  initialTab = "personal",
  initialMemoryProjectId = null,
  user = null,
  onUserChange,
  onClose,
}: {
  initialTab?: SettingsTab;
  initialMemoryProjectId?: string | null;
  user?: User | null;
  onUserChange: (user: User) => void;
  onClose: () => void;
}) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="flex h-[90vh] w-full max-w-4xl overflow-hidden rounded-2xl bg-white shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="scroll-thin flex w-48 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-gray-100 bg-gray-50/70 p-2 dark:border-gray-800 dark:bg-gray-950/40">
          <h2 className="mb-2 px-2 pt-1 text-sm font-semibold text-gray-900 dark:text-gray-100">설정</h2>
          {TABS.map((tab) => {
            const Icon = tab.icon;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-left text-sm ${
                  activeTab === tab.id
                    ? "bg-gray-200 font-medium text-gray-900 dark:bg-gray-800 dark:text-white"
                    : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800/60"
                }`}
              >
                <Icon />
                <span className="truncate">{tab.label}</span>
              </button>
            );
          })}
        </div>

        <div className="scroll-thin relative min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="absolute right-4 top-4 flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <CloseIcon />
          </button>

          {activeTab === "account" && <AccountPanel user={user} onUserChange={onUserChange} />}
          {activeTab === "personal" && <PersonalInstructionPanel />}
          {activeTab === "import" && <ImportMemoryPanel />}
          {activeTab === "usage" && <UsagePanel />}
          {activeTab === "memory" && <MemoryPanel initialOpenProjectId={initialMemoryProjectId} />}
          {activeTab === "appearance" && <AppearancePanel />}
        </div>
      </div>
    </div>
  );
}
