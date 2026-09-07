"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Conversation } from "@/lib/types";

// 텍스트는 width 트랜지션(아래 duration-300, 300ms)이 다 끝나기 전, 박스가
// 거의 다 늘어났을 즈음 미리 보여준다 (완전히 끝날 때까지 기다리면 너무 늦게
// 나타나는 느낌이라 살짝 당김).
const LABEL_REVEAL_DELAY_MS = 130;

function PanelToggleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <rect x="3" y="4" width="18" height="16" rx="2" stroke="currentColor" strokeWidth="2" />
      <line x1="9" y1="4" x2="9" y2="20" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <line
        x1="21"
        y1="21"
        x2="16.65"
        y2="16.65"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
      <path
        d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 11-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 110-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 112.83-2.83l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 114 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 112.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 110 4h-.09a1.65 1.65 0 00-1.51 1z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
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

// 라이트/다크 모드에 맞춰 사이드바 톤도 같이 바뀐다 (라이트: 옅은 회색, 다크: 예전과 동일한 짙은 회색).
// collapsed일 땐 아이콘만 남은 얇은 레일로 줄어들고, 이때는 앱 아이콘 자체가
// 펼치기 버튼을 겸한다(따로 접기 아이콘을 안 둠).
//
// <aside>를 collapsed 여부로 통째로 갈아치우지 않고 하나만 유지한다 - 예전엔
// collapsed/펼침 상태마다 완전히 다른 <aside>를 반환해서, 토글할 때마다 React가
// DOM을 통째로 교체하며 width 트랜지션 도중 내용이 세로로 튀어 보이는 문제가
// 있었다. 지금은 폭(width)만 트랜지션 대상이고, 내부 라벨/목록만 조건부로 바뀐다.
export default function Sidebar({
  conversations,
  activeId,
  disabled,
  collapsed,
  onToggleCollapsed,
  onSelect,
  onNew,
  onDelete,
  onOpenPersonalInstruction,
  onOpenImportMemory,
}: {
  conversations: Conversation[];
  activeId: string | null;
  disabled: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onOpenPersonalInstruction: () => void;
  onOpenImportMemory: () => void;
}) {
  // 접힘: collapsed가 true 되는 즉시 글자를 감춰서(펼침 텍스트가 먼저 사라지고
  // 나서 박스가 줄어드는 것처럼) 매끄럽게 보인다.
  // 펼침: 반대로 collapsed가 false 되자마자 전체 텍스트를 렌더링하면, 아직
  // 60px밖에 안 늘어난 박스(overflow-hidden) 안에서 글자가 트랜지션 내내
  // 잘렸다 드러났다 하면서 "움직이는" 것처럼 보인다. 그래서 펼칠 때는 width
  // 트랜지션이 끝날 때까지 기다렸다가 한 번에 텍스트를 보여준다.
  const [showFullContent, setShowFullContent] = useState(!collapsed);

  useEffect(() => {
    if (collapsed) {
      setShowFullContent(false);
      return;
    }
    const timer = setTimeout(() => setShowFullContent(true), LABEL_REVEAL_DELAY_MS);
    return () => clearTimeout(timer);
  }, [collapsed]);

  const showFull = !collapsed && showFullContent;

  // 설정 팝업(개인 지침 / 다른 AI에서 가져오기 메뉴)은 <aside>가 overflow-hidden이라
  // (너비 트랜지션 텍스트 클리핑용) 그 안에 그냥 두면 사이드바 폭 밖으로 튀어나가는
  // 부분이 잘려서 안 보인다. 그래서 document.body에 포탈로 띄우고, 버튼의
  // getBoundingClientRect()로 위치만 계산해서 fixed로 앉힌다.
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<{ left: number; bottom: number } | null>(null);
  const settingsBtnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as Node;
      if (
        settingsBtnRef.current?.contains(target) ||
        menuRef.current?.contains(target)
      ) {
        return;
      }
      setMenuOpen(false);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [menuOpen]);

  function handleToggleSettingsMenu() {
    if (!menuOpen && settingsBtnRef.current) {
      const rect = settingsBtnRef.current.getBoundingClientRect();
      setMenuPos({ left: rect.left, bottom: window.innerHeight - rect.top + 8 });
    }
    setMenuOpen((prev) => !prev);
  }

  return (
    <aside
      className={`flex shrink-0 flex-col overflow-hidden border-r border-gray-200 bg-gray-50 text-gray-700 transition-[width] duration-300 ease-in-out dark:border-transparent dark:bg-gray-900 dark:text-gray-200 ${
        collapsed ? "w-[52px]" : "w-[260px]"
      }`}
    >
      <div className="flex h-9 items-center gap-2 px-2 pt-2">
        {/* 로고 아이콘은 항상 같은 36x36 슬롯 - collapsed일 때만 클릭 가능한
            버튼(펼치기)이고, 펼쳐진 뒤에는 장식용으로만 쓴다(접기는 오른쪽의
            별도 버튼). 크기/위치가 두 상태에서 완전히 같아서 안 움직인다. */}
        {collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label="사이드바 펼치기"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg hover:bg-gray-200 dark:hover:bg-gray-800"
          >
            <img src="/icons/npia.png" alt="NPIA" className="h-6 w-6 object-contain" />
          </button>
        ) : (
          <div className="flex h-9 w-9 shrink-0 items-center justify-center">
            <img src="/icons/npia.png" alt="" className="h-6 w-6 object-contain" />
          </div>
        )}
        {showFull && (
          <>
            <span className="flex-1 truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
              NPIA
            </span>
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label="사이드바 접기"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-200 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-100"
            >
              <PanelToggleIcon />
            </button>
          </>
        )}
      </div>

      <div className="flex flex-col gap-1 px-2 pb-2 pt-1">
        <button
          type="button"
          onClick={onNew}
          disabled={disabled}
          title="새 채팅"
          aria-label="새 채팅"
          className={`flex h-9 shrink-0 items-center gap-2 rounded-lg text-sm hover:bg-gray-200 disabled:opacity-40 dark:hover:bg-gray-800 ${
            collapsed ? "w-9" : "w-full border border-gray-300 pr-3 dark:border-gray-700"
          }`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center">
            <PlusIcon />
          </span>
          {showFull && "새 채팅"}
        </button>
        <button
          type="button"
          title="채팅 검색 (준비 중)"
          aria-label="채팅 검색 (준비 중)"
          className={`flex h-9 shrink-0 cursor-default items-center gap-2 rounded-lg text-sm text-gray-400 dark:text-gray-500 ${
            collapsed ? "w-9" : "w-full"
          }`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center">
            <SearchIcon />
          </span>
          {showFull && "채팅 검색"}
        </button>
      </div>

      {/* 컬랩스 여부와 상관없이 항상 렌더링되는 flex-1 스페이서 - 이게 있어야
          아래 설정 버튼이 접힘/펼침 모두에서 사이드바 맨 아래에 고정된다.
          목록 내용 자체는 기존과 동일하게 showFull일 때만 채운다. */}
      <nav className="flex-1 overflow-y-auto px-2 pb-2">
        {showFull && (
          <>
            {conversations.length === 0 && (
              <p className="px-2 py-4 text-center text-xs text-gray-400 dark:text-gray-500">
                아직 채팅방이 없습니다
              </p>
            )}
            <ul className="space-y-0.5">
              {conversations.map((c) => (
                <li key={c.id} className="group relative">
                  <button
                    type="button"
                    onClick={() => !disabled && onSelect(c.id)}
                    disabled={disabled}
                    className={`w-full truncate rounded-lg px-3 py-2 pr-8 text-left text-sm disabled:opacity-40 ${
                      c.id === activeId
                        ? "bg-gray-200 text-gray-900 dark:bg-gray-800 dark:text-white"
                        : "text-gray-600 hover:bg-gray-200/70 dark:text-gray-300 dark:hover:bg-gray-800/70"
                    }`}
                  >
                    {c.title || "새 채팅"}
                  </button>
                  <button
                    type="button"
                    aria-label="채팅방 삭제"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (!disabled) onDelete(c.id);
                    }}
                    disabled={disabled}
                    className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-1.5 text-gray-500 hover:bg-gray-300 hover:text-gray-900 group-hover:block dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-100"
                  >
                    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
                      <path
                        d="M6 6l12 12M18 6L6 18"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>

      <div className="flex flex-col gap-1 border-t border-gray-200 px-2 py-2 dark:border-gray-800">
        <button
          type="button"
          ref={settingsBtnRef}
          onClick={handleToggleSettingsMenu}
          title="설정"
          aria-label="설정"
          className={`flex h-9 shrink-0 items-center gap-2 rounded-lg text-sm text-gray-700 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-800 ${
            collapsed ? "w-9" : "w-full"
          }`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center">
            <SettingsIcon />
          </span>
          {showFull && "설정"}
        </button>
      </div>

      {menuOpen &&
        menuPos &&
        createPortal(
          <div
            ref={menuRef}
            style={{ position: "fixed", left: menuPos.left, bottom: menuPos.bottom }}
            className="z-50 w-48 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
          >
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onOpenPersonalInstruction();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <FileTextIcon />
              개인 지침
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuOpen(false);
                onOpenImportMemory();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <ArrowDownToLineIcon />
              다른 AI에서 가져오기
            </button>
          </div>,
          document.body
        )}
    </aside>
  );
}
