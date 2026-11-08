"use client";

import {
  DragEvent,
  MouseEvent as ReactMouseEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Conversation, Project, ProjectColor, User } from "@/lib/types";
import UserAvatar from "@/components/UserAvatar";

// 마우스로 드래그해서 조절할 수 있는 사이드바 폭의 하한/상한, 그리고
// 펼쳐졌을 때 기본값(예전에 고정값이던 260px를 그대로 기본값으로 둠).
// 드래그해서 MIN보다 더 왼쪽으로 줄이면 접힘(collapsed) 상태로 넘어간다.
const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 420;
const DEFAULT_SIDEBAR_WIDTH = 260;
const COLLAPSED_WIDTH = 52;
const SIDEBAR_WIDTH_STORAGE_KEY = "sidebarWidth";

// 프로젝트 폴더 아이콘 색상 - "default"는 기존처럼 테마 기본 회색을 그대로 쓴다.
const PROJECT_COLOR_CLASSES: Record<ProjectColor, string> = {
  default: "text-gray-500 dark:text-gray-400",
  red: "text-red-500 dark:text-red-400",
  orange: "text-orange-500 dark:text-orange-400",
  yellow: "text-yellow-500 dark:text-yellow-400",
  green: "text-green-500 dark:text-green-400",
  blue: "text-blue-500 dark:text-blue-400",
  purple: "text-purple-500 dark:text-purple-400",
  pink: "text-pink-500 dark:text-pink-400",
};

// 채팅 항목을 드래그할 때 dataTransfer에 실어 보내는 커스텀 MIME 타입 -
// 브라우저 기본 파일 드래그(예: 파일 첨부 드롭존)와 절대 안 겹치게
// 이 앱 전용 타입으로 구분한다.
const CONVERSATION_DRAG_TYPE = "application/x-joomini-conversation-id";

// "고정"/"프로젝트" 섹션 안에서 드래그로 순서만 바꿀 때 쓰는 별도 MIME 타입 -
// 값은 "proj:<id>" 또는 "conv:<id>" 형태로 어떤 종류를 옮기는지까지 담는다.
// CONVERSATION_DRAG_TYPE(채팅을 프로젝트에 편입/해제하는 용도)과는 별개라
// 같은 드래그에 둘 다 실어도 서로 안 헷갈린다.
const REORDER_DRAG_TYPE = "application/x-joomini-reorder-key";

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

function UserIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="2" />
      <path d="M4 21a8 8 0 0116 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function LogOutIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path
        d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
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

function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={`h-3 w-3 shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
    >
      <path d="M9 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path
        d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChatBubbleIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
      <path
        d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 20l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DotsIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className="h-3.5 w-3.5">
      <circle cx="12" cy="5" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="12" cy="19" r="1.6" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M9 4h6l-.5 6.5L18 14v2h-5.2v5l-.8 2-.8-2v-5H6v-2l3.5-3.5L9 4z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" className="h-3.5 w-3.5">
      <path
        d="M4 7h16M9 7V4h6v3m-8 0l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 일반 채팅 목록/프로젝트 안 채팅 목록에서 공유하는 한 줄 - 이름 표시(또는
// 이름 변경 중이면 입력창), 고정 표시, "⋮" 옵션 버튼(고정/이름 변경/삭제).
// 삭제는 예전엔 별도 X 버튼이었지만 지금은 이 메뉴 안 항목(빨간색)으로 통합됨.
// size로 두 목록의 글자 크기 차이만 반영한다.
function ConversationRow({
  conversation,
  isActive,
  size,
  isRenaming,
  renameValue,
  onRenameChange,
  onCommitRename,
  onCancelRename,
  onSelect,
  onDragStart,
  onOpenMenu,
  onReorderOver,
  onReorderDropTarget,
}: {
  conversation: Conversation;
  isActive: boolean;
  size: "sm" | "xs";
  isRenaming: boolean;
  renameValue: string;
  onRenameChange: (value: string) => void;
  onCommitRename: () => void;
  onCancelRename: () => void;
  onSelect: () => void;
  onDragStart: (e: DragEvent<HTMLLIElement>) => void;
  onOpenMenu: (triggerEl: HTMLElement) => void;
  // "고정" 섹션에서 이 채팅 위로 다른 항목을 끌어다 놓아 순서를 바꿀 때만 넘어옴.
  onReorderOver?: (e: DragEvent<HTMLLIElement>) => void;
  onReorderDropTarget?: (e: DragEvent<HTMLLIElement>) => void;
}) {
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isRenaming) renameInputRef.current?.focus();
  }, [isRenaming]);

  const textSize = size === "xs" ? "text-xs" : "text-sm";
  // 왼쪽 여백은 안 둔다 - 앞의 말풍선 아이콘 + 컨테이너 gap-0.5만으로 프로젝트
  // 행(아이콘 다음 바로 이름 버튼)과 동일한 간격이 나온다. 오른쪽만 클릭 영역
  // 확보용으로 남겨둔다.
  const padding = size === "xs" ? "py-1.5 pr-2" : "py-2 pr-3";

  return (
    <li
      className={`group relative flex items-center gap-0.5 rounded-lg ${
        isActive ? "bg-gray-200 dark:bg-gray-800" : "hover:bg-gray-200/70 dark:hover:bg-gray-800/70"
      }`}
      draggable={!isRenaming}
      onDragStart={onDragStart}
      onDragOver={onReorderOver}
      onDrop={onReorderDropTarget}
    >
      <span
        className={`flex shrink-0 items-center justify-center text-gray-400 dark:text-gray-500 ${
          size === "xs" ? "h-7 w-7" : "h-9 w-9"
        }`}
      >
        <ChatBubbleIcon />
      </span>
      {isRenaming ? (
        <input
          ref={renameInputRef}
          value={renameValue}
          onChange={(e) => onRenameChange(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={onCommitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onCommitRename();
            }
            if (e.key === "Escape") {
              e.preventDefault();
              onCancelRename();
            }
          }}
          className={`min-w-0 flex-1 rounded-lg bg-white ${padding} ${textSize} text-gray-900 outline-none ring-1 ring-gray-400 dark:bg-gray-950 dark:text-gray-100`}
        />
      ) : (
        <button
          type="button"
          onClick={onSelect}
          className={`min-w-0 flex-1 truncate rounded-lg ${padding} text-left ${textSize} ${
            isActive ? "text-gray-900 dark:text-white" : "text-gray-600 dark:text-gray-300"
          }`}
        >
          {conversation.title || "새 채팅"}
        </button>
      )}
      {!isRenaming && (
        <button
          type="button"
          data-conv-menu-trigger="true"
          aria-label="채팅 옵션"
          onClick={(e) => {
            e.stopPropagation();
            onOpenMenu(e.currentTarget);
          }}
          className="hidden shrink-0 rounded p-1.5 text-gray-500 hover:bg-gray-300 hover:text-gray-900 group-hover:block dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-100"
        >
          <DotsIcon />
        </button>
      )}
    </li>
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
  user,
  onOpenSettings,
  onOpenAccount,
  onOpenSearch,
  projects,
  onOpenProject,
  onNewProject,
  onMoveConversationToProject,
  onRenameConversation,
  onTogglePinConversation,
  onTogglePinProject,
  onReorderProjects,
  onReorderPinned,
  onRenameProject,
  onDeleteProject,
}: {
  conversations: Conversation[];
  activeId: string | null;
  disabled: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  // 로그인한 사용자 - 맨 아래 사용자 버튼의 이름/아이콘(첫 글자)과 메뉴 헤더에 쓴다.
  // 아직 못 불러왔으면 null.
  user: User | null;
  // 개인 지침/가져오기/토큰 사용량/메모리/화면(다크모드)을 탭으로 묶은
  // 설정 창을 연다(기본 탭은 호출하는 쪽에서 정함).
  onOpenSettings: () => void;
  // 같은 설정 창을 계정 탭으로 연다.
  onOpenAccount: () => void;
  onOpenSearch: () => void;
  projects: Project[];
  onOpenProject: (id: string) => void;
  onNewProject: () => void;
  // projectId가 null이면 프로젝트에서 해제(일반 채팅으로 되돌림).
  onMoveConversationToProject: (conversationId: string, projectId: string | null) => void;
  onRenameConversation: (conversationId: string, title: string) => void;
  onTogglePinConversation: (conversationId: string, pinned: boolean) => void;
  onTogglePinProject: (projectId: string, pinned: boolean) => void;
  // "프로젝트" 섹션(핀 안 된 프로젝트끼리) 드래그로 순서를 바꾼 결과.
  onReorderProjects: (orderedProjectIds: string[]) => void;
  // "고정" 섹션(핀 된 프로젝트 + 핀 된 채팅 통합) 드래그로 순서를 바꾼 결과.
  onReorderPinned: (orderedItems: { type: "project" | "conversation"; id: string }[]) => void;
  onRenameProject: (projectId: string, name: string) => void;
  onDeleteProject: (projectId: string) => void;
}) {
  // 드래그 중인 채팅이 프로젝트 행 위에 있을 때 그 행에만 강조 표시.
  const [dragOverProjectId, setDragOverProjectId] = useState<string | null>(null);
  const [dragOverUnassign, setDragOverUnassign] = useState(false);
  // 폴더 아이콘을 눌러서 펼친 프로젝트들 - 여러 개 동시에 펼쳐둘 수 있음.
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(new Set());

  // 핀 된 프로젝트/채팅은 맨 위 "고정" 섹션에서 통합 순서(sort_order)로 보여주고,
  // 각자 원래 섹션(프로젝트/채팅)에는 중복으로 안 보이게 뺀다.
  const pinnedProjects = projects.filter((p) => p.pinned);
  const unpinnedProjects = [...projects.filter((p) => !p.pinned)].sort(
    (a, b) => a.sort_order - b.sort_order
  );
  const pinnedConversations = conversations.filter((c) => c.pinned);
  const pinnedItems: { type: "project" | "conversation"; id: string; sortOrder: number }[] = [
    ...pinnedProjects.map((p) => ({ type: "project" as const, id: p.id, sortOrder: p.sort_order })),
    ...pinnedConversations.map((c) => ({
      type: "conversation" as const,
      id: c.id,
      sortOrder: c.sort_order,
    })),
  ].sort((a, b) => a.sortOrder - b.sortOrder);

  // 프로젝트에 편입된 채팅은 해당 프로젝트 행을 펼쳤을 때만 보이고, 아래
  // 일반 채팅 목록에는 안 뜬다(중복 표시 방지). 핀 된 채팅은 위 "고정" 섹션에서
  // 보이므로 여기서도 제외한다.
  const unassignedConversations = conversations.filter(
    (c) => c.project_id === null && !c.pinned
  );

  function toggleProjectExpanded(projectId: string) {
    setExpandedProjectIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }

  function handleConversationDragStart(e: DragEvent<HTMLLIElement>, conversationId: string) {
    e.dataTransfer.setData(CONVERSATION_DRAG_TYPE, conversationId);
    e.dataTransfer.setData(REORDER_DRAG_TYPE, `conv:${conversationId}`);
    e.dataTransfer.effectAllowed = "move";
  }

  function handleProjectDragOver(e: DragEvent<HTMLDivElement>) {
    if (
      !e.dataTransfer.types.includes(CONVERSATION_DRAG_TYPE) &&
      !e.dataTransfer.types.includes(REORDER_DRAG_TYPE)
    ) {
      return;
    }
    e.preventDefault();
  }

  function handleProjectDrop(e: DragEvent<HTMLDivElement>, projectId: string) {
    setDragOverProjectId(null);
    // REORDER_DRAG_TYPE(같은 섹션 안 순서 변경)이 같이 실려 있으면 그쪽을 우선한다 -
    // 프로젝트 행 자체를 다른 프로젝트 행 위에 놓은 경우.
    if (e.dataTransfer.types.includes(REORDER_DRAG_TYPE)) {
      e.preventDefault();
      const key = e.dataTransfer.getData(REORDER_DRAG_TYPE);
      handleReorderDrop(key, `proj:${projectId}`);
      return;
    }
    if (!e.dataTransfer.types.includes(CONVERSATION_DRAG_TYPE)) return;
    e.preventDefault();
    const conversationId = e.dataTransfer.getData(CONVERSATION_DRAG_TYPE);
    if (conversationId) onMoveConversationToProject(conversationId, projectId);
  }

  // "고정"/"프로젝트" 섹션 안에서의 순서 변경 - draggedKey를 targetKey 바로
  // 앞으로 옮긴다(흔히 쓰는 단순 리스트 재배치 방식). 두 섹션은 서로 다른
  // 순서 공간이라 같은 섹션 안에서 놓였을 때만 의미가 있다 - targetKey가
  // 어느 배열에 속하는지로 어느 섹션인지 판단한다.
  function handleReorderDrop(draggedKey: string, targetKey: string) {
    if (!draggedKey || draggedKey === targetKey) return;
    const inPinned = pinnedItems.some((it) => `${it.type === "project" ? "proj" : "conv"}:${it.id}` === targetKey);
    if (inPinned) {
      const ids = pinnedItems.map((it) => (it.type === "project" ? `proj:${it.id}` : `conv:${it.id}`));
      if (!ids.includes(draggedKey)) return;
      const without = ids.filter((k) => k !== draggedKey);
      const targetIndex = without.indexOf(targetKey);
      without.splice(targetIndex, 0, draggedKey);
      onReorderPinned(
        without.map((k) => {
          const [kind, id] = k.split(":");
          return { type: kind === "proj" ? ("project" as const) : ("conversation" as const), id };
        })
      );
      return;
    }
    // 프로젝트 섹션(핀 안 된 프로젝트끼리) - draggedKey/targetKey 둘 다 "proj:"여야 함.
    if (!draggedKey.startsWith("proj:") || !targetKey.startsWith("proj:")) return;
    const ids = unpinnedProjects.map((p) => `proj:${p.id}`);
    if (!ids.includes(draggedKey) || !ids.includes(targetKey)) return;
    const without = ids.filter((k) => k !== draggedKey);
    const targetIndex = without.indexOf(targetKey);
    without.splice(targetIndex, 0, draggedKey);
    onReorderProjects(without.map((k) => k.slice("proj:".length)));
  }

  function handleReorderRowDragStart(e: DragEvent<HTMLElement>, key: string) {
    e.dataTransfer.setData(REORDER_DRAG_TYPE, key);
    e.dataTransfer.effectAllowed = "move";
  }

  function handleReorderRowDragOver(e: DragEvent<HTMLElement>) {
    if (!e.dataTransfer.types.includes(REORDER_DRAG_TYPE)) return;
    e.preventDefault();
  }

  function handleReorderRowDrop(e: DragEvent<HTMLElement>, targetKey: string) {
    if (!e.dataTransfer.types.includes(REORDER_DRAG_TYPE)) return;
    e.preventDefault();
    e.stopPropagation();
    const draggedKey = e.dataTransfer.getData(REORDER_DRAG_TYPE);
    handleReorderDrop(draggedKey, targetKey);
  }

  function handleUnassignDrop(e: DragEvent<HTMLButtonElement>) {
    if (!e.dataTransfer.types.includes(CONVERSATION_DRAG_TYPE)) return;
    e.preventDefault();
    setDragOverUnassign(false);
    const conversationId = e.dataTransfer.getData(CONVERSATION_DRAG_TYPE);
    if (conversationId) onMoveConversationToProject(conversationId, null);
  }

  // 맨 아래 사용자 버튼의 메뉴(설정/계정 설정/로그아웃) - 채팅 옵션 메뉴와 같은
  // 방식(document.body 포탈 + 트리거 위치로 좌표 계산)인데, 버튼이 화면 맨
  // 아래라서 위쪽으로 펼친다(bottom 기준).
  const [userMenuPos, setUserMenuPos] = useState<{ left: number; bottom: number } | null>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!userMenuPos) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (userMenuRef.current?.contains(target)) return;
      // 트리거를 다시 누른 건 토글로 처리(바깥 클릭으로 닫힌 직후 다시 열리는 것 방지)
      if (target.closest("[data-user-menu-trigger]")) return;
      setUserMenuPos(null);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setUserMenuPos(null);
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [userMenuPos]);

  function handleToggleUserMenu(triggerEl: HTMLElement) {
    if (userMenuPos) {
      setUserMenuPos(null);
      return;
    }
    const rect = triggerEl.getBoundingClientRect();
    setUserMenuPos({ left: rect.left, bottom: window.innerHeight - rect.top + 6 });
  }

  const userName = user?.name ?? "사용자";

  // 채팅 항목의 "⋮" 옵션 메뉴(고정/이름 변경) - 설정 메뉴와 동일하게
  // document.body에 포탈로 띄우고 트리거 버튼의 위치로 좌표만 계산한다.
  const [convMenuId, setConvMenuId] = useState<string | null>(null);
  const [convMenuPos, setConvMenuPos] = useState<{ left: number; top: number } | null>(null);
  const convMenuRef = useRef<HTMLDivElement>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  useEffect(() => {
    if (!convMenuId) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (convMenuRef.current?.contains(target)) return;
      // 메뉴를 연 트리거 버튼 자체를 다시 누른 것까지 "바깥 클릭"으로 잡으면
      // mousedown(닫기) 직후 그 버튼의 onClick(토글)이 이어서 실행되면서
      // 다시 열려버린다 - data 속성으로 트리거는 예외 처리.
      if (target.closest("[data-conv-menu-trigger]")) return;
      setConvMenuId(null);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [convMenuId]);

  function handleOpenConvMenu(conversationId: string, triggerEl: HTMLElement) {
    const rect = triggerEl.getBoundingClientRect();
    const menuWidth = 160;
    setConvMenuPos({ left: Math.max(8, rect.right - menuWidth), top: rect.bottom + 4 });
    setConvMenuId((prev) => (prev === conversationId ? null : conversationId));
  }

  function startRename(conversationId: string) {
    const conv = conversations.find((c) => c.id === conversationId);
    setRenamingId(conversationId);
    setRenameValue(conv?.title ?? "");
    setConvMenuId(null);
  }

  function commitRename(conversationId: string) {
    const trimmed = renameValue.trim();
    setRenamingId(null);
    if (trimmed) onRenameConversation(conversationId, trimmed);
  }

  function cancelRename() {
    setRenamingId(null);
  }

  // 프로젝트 행의 "⋮" 옵션 메뉴 - 고정/고정 해제, 이름 변경, 삭제. 채팅
  // 항목의 메뉴(⋮)와 동일한 구성/위치 계산 방식.
  const [projMenuId, setProjMenuId] = useState<string | null>(null);
  const [projMenuPos, setProjMenuPos] = useState<{ left: number; top: number } | null>(null);
  const projMenuRef = useRef<HTMLDivElement>(null);
  const [renamingProjectId, setRenamingProjectId] = useState<string | null>(null);
  const [renameProjectValue, setRenameProjectValue] = useState("");
  const renameProjectInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!projMenuId) return;
    function onMouseDown(e: MouseEvent) {
      const target = e.target as HTMLElement;
      if (projMenuRef.current?.contains(target)) return;
      if (target.closest("[data-proj-menu-trigger]")) return;
      setProjMenuId(null);
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [projMenuId]);

  useEffect(() => {
    if (renamingProjectId) renameProjectInputRef.current?.focus();
  }, [renamingProjectId]);

  function handleOpenProjMenu(projectId: string, triggerEl: HTMLElement) {
    const rect = triggerEl.getBoundingClientRect();
    const menuWidth = 160;
    setProjMenuPos({ left: Math.max(8, rect.right - menuWidth), top: rect.bottom + 4 });
    setProjMenuId((prev) => (prev === projectId ? null : projectId));
  }

  function startRenameProject(projectId: string) {
    const project = projects.find((p) => p.id === projectId);
    setRenamingProjectId(projectId);
    setRenameProjectValue(project?.name ?? "");
    setProjMenuId(null);
  }

  function commitRenameProject(projectId: string) {
    const trimmed = renameProjectValue.trim();
    setRenamingProjectId(null);
    if (trimmed) onRenameProject(projectId, trimmed);
  }

  function cancelRenameProject() {
    setRenamingProjectId(null);
  }

  // "고정" / "프로젝트" / "채팅" 세 섹션 모두 헤더를 눌러 접고 펼 수 있다 -
  // 기본은 셋 다 펼침.
  const [pinnedSectionOpen, setPinnedSectionOpen] = useState(true);
  const [projectsSectionOpen, setProjectsSectionOpen] = useState(true);
  const [chatsSectionOpen, setChatsSectionOpen] = useState(true);

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

  // 사이드바 폭 드래그 조절 - 펼쳐진 상태에서만 의미가 있고(접힘은 항상
  // COLLAPSED_WIDTH 고정), localStorage에 저장해서 새로고침해도 유지한다.
  const [width, setWidth] = useState(DEFAULT_SIDEBAR_WIDTH);
  const [isResizing, setIsResizing] = useState(false);
  const widthRef = useRef(width);
  const resizeStartRef = useRef<{ startX: number; startWidth: number } | null>(null);
  // 드래그 도중 MIN보다 왼쪽으로 넘어가 이미 접힘 처리를 했는지 - 한 번의
  // 드래그에서 여러 번 토글되지 않게 막는 플래그.
  const collapsedDuringDragRef = useRef(false);

  useEffect(() => {
    widthRef.current = width;
  }, [width]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY);
      const parsed = saved ? Number(saved) : NaN;
      if (!Number.isNaN(parsed)) {
        setWidth(Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, parsed)));
      }
    } catch {
      // localStorage 접근 불가(프라이빗 브라우징 등)해도 기본값으로 그냥 진행
    }
  }, []);

  function handleResizeMouseDown(e: ReactMouseEvent<HTMLDivElement>) {
    if (collapsed) return;
    e.preventDefault();
    resizeStartRef.current = { startX: e.clientX, startWidth: width };
    collapsedDuringDragRef.current = false;
    setIsResizing(true);
  }

  useEffect(() => {
    if (!isResizing) return;

    function handleMouseMove(e: MouseEvent) {
      const start = resizeStartRef.current;
      if (!start) return;
      const newWidth = start.startWidth + (e.clientX - start.startX);

      if (newWidth < MIN_SIDEBAR_WIDTH) {
        // 최소 폭보다 더 줄이면 사이드바 자체를 접는다 - 리사이즈 핸들은
        // 접힌 상태에선 안 보이지만, 드래그 자체는 document 레벨 리스너라
        // 계속 추적되므로 마우스를 더 왼쪽으로 끌면 그대로 접힌 채 유지된다.
        if (!collapsedDuringDragRef.current) {
          collapsedDuringDragRef.current = true;
          onToggleCollapsed();
        }
        return;
      }

      if (collapsedDuringDragRef.current) {
        // 접힘 쪽으로 넘어갔다가 다시 최소 폭 이상으로 돌아오면 펼침 복구.
        collapsedDuringDragRef.current = false;
        onToggleCollapsed();
      }

      setWidth(Math.min(MAX_SIDEBAR_WIDTH, newWidth));
    }

    function handleMouseUp() {
      setIsResizing(false);
      resizeStartRef.current = null;
      try {
        localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(widthRef.current));
      } catch {
        // 저장 실패해도 이번 세션 안에서는 상태가 유지되니 무시
      }
    }

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isResizing]);

  // "고정"/"프로젝트" 두 섹션에서 공유하는 프로젝트 한 줄 - 드래그로 순서
  // 변경(reorderKey), 드롭 시 채팅 편입(handleProjectDrop), 폴더 토글로
  // 소속 채팅 펼치기, "⋮" 메뉴(고정)까지 전부 여기 하나로 묶는다.
  function renderProjectRow(p: Project) {
    const expanded = expandedProjectIds.has(p.id);
    const isRenaming = renamingProjectId === p.id;
    // 핀 된 채팅은 위 "고정" 섹션에 이미 보이므로 여기 펼친 목록에는 안 넣는다.
    const projectChats = conversations.filter((c) => c.project_id === p.id && !c.pinned);
    const reorderKey = `proj:${p.id}`;
    return (
      <li key={p.id}>
        <div
          draggable={!isRenaming}
          onDragStart={(e) => handleReorderRowDragStart(e, reorderKey)}
          onDragEnter={() => setDragOverProjectId(p.id)}
          onDragOver={handleProjectDragOver}
          onDragLeave={() => setDragOverProjectId((cur) => (cur === p.id ? null : cur))}
          onDrop={(e) => handleProjectDrop(e, p.id)}
          className={`group flex items-center gap-0.5 rounded-lg text-sm text-gray-600 hover:bg-gray-200/70 dark:text-gray-300 dark:hover:bg-gray-800/70 ${
            dragOverProjectId === p.id ? "ring-2 ring-inset ring-gray-400 dark:ring-gray-500" : ""
          }`}
        >
          {/* 폴더 아이콘 - 평소엔 폴더 모양이지만, 마우스를 올리면
              토글(화살표) 아이콘으로 바뀌어서 클릭하면 이 프로젝트에
              속한 채팅 목록만 펼치고 접는다(모달은 안 엶). */}
          <button
            type="button"
            onClick={() => toggleProjectExpanded(p.id)}
            aria-label={expanded ? "채팅 목록 접기" : "채팅 목록 펼치기"}
            className="group/folder relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-200 dark:text-gray-400 dark:hover:bg-gray-700"
          >
            <span
              className={`absolute inset-0 flex items-center justify-center opacity-100 group-hover/folder:opacity-0 ${
                PROJECT_COLOR_CLASSES[p.color] ?? PROJECT_COLOR_CLASSES.default
              }`}
            >
              <FolderIcon />
            </span>
            <span className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/folder:opacity-100">
              <ChevronIcon open={expanded} />
            </span>
          </button>
          {/* 프로젝트 이름 - 클릭하면 관리 페이지로 이동한다(이름 변경 중이면
              입력창으로 바뀜 - 채팅 항목 이름 변경과 동일한 방식). */}
          {isRenaming ? (
            <input
              ref={renameProjectInputRef}
              value={renameProjectValue}
              onChange={(e) => setRenameProjectValue(e.target.value)}
              onClick={(e) => e.stopPropagation()}
              onBlur={() => commitRenameProject(p.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitRenameProject(p.id);
                }
                if (e.key === "Escape") {
                  e.preventDefault();
                  cancelRenameProject();
                }
              }}
              className="min-w-0 flex-1 rounded-lg bg-white px-2 py-1.5 text-sm text-gray-900 outline-none ring-1 ring-gray-400 dark:bg-gray-950 dark:text-gray-100"
            />
          ) : (
            <button
              type="button"
              onClick={() => onOpenProject(p.id)}
              className="min-w-0 flex-1 truncate py-2 pr-1 text-left"
            >
              {p.name}
            </button>
          )}
          {!isRenaming && (
            <button
              type="button"
              data-proj-menu-trigger="true"
              aria-label="프로젝트 옵션"
              onClick={(e) => {
                e.stopPropagation();
                handleOpenProjMenu(p.id, e.currentTarget);
              }}
              className="hidden shrink-0 rounded p-1.5 mr-1 text-gray-500 hover:bg-gray-300 hover:text-gray-900 group-hover:block dark:text-gray-400 dark:hover:bg-gray-700 dark:hover:text-gray-100"
            >
              <DotsIcon />
            </button>
          )}
        </div>
        {expanded && (
          <ul className="ml-4 mt-0.5 space-y-0.5 border-l border-gray-200 pl-2 dark:border-gray-700">
            {projectChats.length === 0 ? (
              <li className="px-2 py-1 text-xs text-gray-400 dark:text-gray-500">채팅 없음</li>
            ) : (
              projectChats.map((c) => (
                <ConversationRow
                  key={c.id}
                  conversation={c}
                  isActive={c.id === activeId}
                  size="xs"
                  isRenaming={renamingId === c.id}
                  renameValue={renameValue}
                  onRenameChange={setRenameValue}
                  onCommitRename={() => commitRename(c.id)}
                  onCancelRename={cancelRename}
                  onSelect={() => onSelect(c.id)}
                  onDragStart={(e) => handleConversationDragStart(e, c.id)}
                  onOpenMenu={(el) => handleOpenConvMenu(c.id, el)}
                />
              ))
            )}
          </ul>
        )}
      </li>
    );
  }

  return (
    <aside
      style={{ width: collapsed ? COLLAPSED_WIDTH : width }}
      onClick={() => {
        // 접힌 상태에서는 사이드바 아무 데나 눌러도 펼쳐지게 한다(로고
        // 버튼 자체는 자기 onClick에서 stopPropagation하므로 중복 토글 안 됨).
        if (collapsed) onToggleCollapsed();
      }}
      className={`relative flex shrink-0 flex-col overflow-hidden border-r border-gray-200 bg-gray-50 text-gray-700 ease-in-out dark:border-transparent dark:bg-gray-900 dark:text-gray-200 ${
        isResizing ? "" : "transition-[width] duration-300"
      }`}
    >
      {!collapsed && (
        <div
          onMouseDown={handleResizeMouseDown}
          role="separator"
          aria-orientation="vertical"
          aria-label="사이드바 폭 조절"
          className="absolute inset-y-0 right-0 z-10 w-1.5 -translate-x-1/2 cursor-col-resize select-none hover:bg-gray-300 dark:hover:bg-gray-700"
        />
      )}
      <div className="flex h-11 items-center gap-2 px-2 pt-2">
        {/* 로고 아이콘은 항상 같은 40x40 슬롯 - collapsed일 때만 클릭 가능한
            버튼(펼치기)이고, 펼쳐진 뒤에는 장식용으로만 쓴다(접기는 오른쪽의
            별도 버튼). 크기/위치가 두 상태에서 완전히 같아서 안 움직인다. */}
        {collapsed ? (
          <button
            type="button"
            onClick={(e) => {
              // <aside>에도 "접혀있으면 눌러서 펼치기" onClick이 달려있어서,
              // 여기서 안 막으면 버블링으로 두 번 토글되어 결국 그대로 접힌
              // 채로 남는다.
              e.stopPropagation();
              onToggleCollapsed();
            }}
            aria-label="사이드바 펼치기"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg hover:bg-gray-200 dark:hover:bg-gray-800"
          >
            <img src="/icons/joomini.png" alt="Joomini" className="h-8 w-8 object-contain" />
          </button>
        ) : (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center">
            <img src="/icons/joomini.png" alt="" className="h-8 w-8 object-contain" />
          </div>
        )}
        {showFull && (
          <>
            <span className="flex-1 truncate text-lg font-semibold text-gray-900 dark:text-gray-100">
              Joomini
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
          title="새 채팅"
          aria-label="새 채팅"
          className={`flex h-9 shrink-0 items-center gap-2 rounded-lg text-sm hover:bg-gray-200 dark:hover:bg-gray-800 ${
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
          onClick={onOpenSearch}
          title="채팅 검색"
          aria-label="채팅 검색"
          className={`flex h-9 shrink-0 items-center gap-2 rounded-lg text-sm text-gray-700 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-800 ${
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
          "고정"/"프로젝트"/"채팅" 세 섹션을 전부 이 하나의 스크롤 영역
          안에 두어서, 목록이 많아져도 섹션별로 따로따로가 아니라 사이드바
          전체가 하나로 스크롤된다(위의 "새 채팅"/"채팅 검색" 버튼과 아래
          "설정" 버튼만 스크롤 밖에 고정됨). */}
      <nav className="scroll-thin flex-1 overflow-y-auto px-2 pb-2">
        {showFull && (
          <>
            {/* "고정" 섹션 - 핀 된 프로젝트/채팅을 통합 순서(sort_order)로
                보여준다. 핀 된 게 하나도 없으면 섹션 자체를 안 보여준다. */}
            {pinnedItems.length > 0 && (
              <div className="flex flex-col gap-1 py-2">
                <button
                  type="button"
                  onClick={() => setPinnedSectionOpen((v) => !v)}
                  className="flex items-center gap-1 px-1 text-xs font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                >
                  <ChevronIcon open={pinnedSectionOpen} />
                  고정
                </button>
                {pinnedSectionOpen && (
                  <ul className="space-y-0.5">
                    {pinnedItems.map((item) =>
                      item.type === "project" ? (
                        (() => {
                          const p = projects.find((proj) => proj.id === item.id);
                          return p ? <div key={`proj:${p.id}`}>{renderProjectRow(p)}</div> : null;
                        })()
                      ) : (
                        (() => {
                          const c = conversations.find((conv) => conv.id === item.id);
                          if (!c) return null;
                          const key = `conv:${c.id}`;
                          return (
                            <ConversationRow
                              key={key}
                              conversation={c}
                              isActive={c.id === activeId}
                              size="sm"
                              isRenaming={renamingId === c.id}
                              renameValue={renameValue}
                              onRenameChange={setRenameValue}
                              onCommitRename={() => commitRename(c.id)}
                              onCancelRename={cancelRename}
                              onSelect={() => onSelect(c.id)}
                              onDragStart={(e) => handleConversationDragStart(e, c.id)}
                              onOpenMenu={(el) => handleOpenConvMenu(c.id, el)}
                              onReorderOver={handleReorderRowDragOver}
                              onReorderDropTarget={(e) => handleReorderRowDrop(e, key)}
                            />
                          );
                        })()
                      )
                    )}
                  </ul>
                )}
              </div>
            )}

            {/* 프로젝트 섹션 - 드래그한 채팅을 놓을 수 있는 행들. */}
            <div className="flex flex-col gap-1 border-t border-gray-200 py-2 dark:border-gray-800">
              <div className="flex items-center justify-between px-1">
                <button
                  type="button"
                  onClick={() => setProjectsSectionOpen((v) => !v)}
                  className="flex items-center gap-1 text-xs font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300"
                >
                  <ChevronIcon open={projectsSectionOpen} />
                  프로젝트
                </button>
                <button
                  type="button"
                  onClick={onNewProject}
                  aria-label="새 프로젝트"
                  className="flex h-5 w-5 items-center justify-center rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
                >
                  <PlusIcon />
                </button>
              </div>
              {projectsSectionOpen &&
                (unpinnedProjects.length === 0 ? (
                  <p className="px-1 py-1 text-xs text-gray-400 dark:text-gray-500">
                    아직 프로젝트가 없습니다
                  </p>
                ) : (
                  <ul className="space-y-0.5">{unpinnedProjects.map((p) => renderProjectRow(p))}</ul>
                ))}
            </div>

            {/* 프로젝트에 편입된 채팅을 다시 여기로 드래그하면 해제(project_id
                null)된다 - 프로젝트 행으로 옮기는 것과 대칭. 헤더 클릭으로
                이 섹션 전체를 접고 펼 수도 있다. */}
            <div className="flex flex-col gap-1 border-t border-gray-200 py-2 dark:border-gray-800">
              <button
                type="button"
                onClick={() => setChatsSectionOpen((v) => !v)}
                onDragEnter={(e) => {
                  if (!e.dataTransfer.types.includes(CONVERSATION_DRAG_TYPE)) return;
                  e.preventDefault();
                  setDragOverUnassign(true);
                }}
                onDragOver={(e) => {
                  if (!e.dataTransfer.types.includes(CONVERSATION_DRAG_TYPE)) return;
                  e.preventDefault();
                }}
                onDragLeave={() => setDragOverUnassign(false)}
                onDrop={handleUnassignDrop}
                className={`flex w-full items-center gap-1 rounded-lg px-1 text-xs font-semibold uppercase tracking-wide text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300 ${
                  dragOverUnassign ? "bg-gray-200 dark:bg-gray-800" : ""
                }`}
              >
                <ChevronIcon open={chatsSectionOpen} />
                채팅
              </button>
              {chatsSectionOpen && (
                <>
                  {unassignedConversations.length === 0 && (
                    <p className="px-2 py-4 text-center text-xs text-gray-400 dark:text-gray-500">
                      아직 채팅방이 없습니다
                    </p>
                  )}
                  <ul className="space-y-0.5">
                    {unassignedConversations.map((c) => (
                      <ConversationRow
                        key={c.id}
                        conversation={c}
                        isActive={c.id === activeId}
                        size="sm"
                        isRenaming={renamingId === c.id}
                        renameValue={renameValue}
                        onRenameChange={setRenameValue}
                        onCommitRename={() => commitRename(c.id)}
                        onCancelRename={cancelRename}
                        onSelect={() => onSelect(c.id)}
                        onDragStart={(e) => handleConversationDragStart(e, c.id)}
                        onOpenMenu={(el) => handleOpenConvMenu(c.id, el)}
                      />
                    ))}
                  </ul>
                </>
              )}
            </div>
          </>
        )}
      </nav>

      <div className="flex flex-col gap-1 border-t border-gray-200 px-2 py-2 dark:border-gray-800">
        <button
          type="button"
          data-user-menu-trigger
          onClick={(e) => handleToggleUserMenu(e.currentTarget)}
          title={userName}
          aria-label={`${userName} 메뉴`}
          aria-haspopup="menu"
          aria-expanded={userMenuPos !== null}
          className={`flex h-9 shrink-0 items-center gap-2 rounded-lg text-sm text-gray-700 hover:bg-gray-200 dark:text-gray-300 dark:hover:bg-gray-800 ${
            collapsed ? "w-9" : "w-full"
          }`}
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center">
            <UserAvatar user={user} />
          </span>
          {showFull && <span className="truncate">{userName}</span>}
        </button>
      </div>

      {userMenuPos &&
        createPortal(
          <div
            ref={userMenuRef}
            role="menu"
            style={{ position: "fixed", left: userMenuPos.left, bottom: userMenuPos.bottom }}
            className="z-50 w-56 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
          >
            <div className="border-b border-gray-100 px-3 py-2 dark:border-gray-700">
              <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{userName}</p>
              {user?.email && (
                <p className="truncate text-xs text-gray-500 dark:text-gray-400">{user.email}</p>
              )}
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setUserMenuPos(null);
                onOpenSettings();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <SettingsIcon />
              설정
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setUserMenuPos(null);
                onOpenAccount();
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <UserIcon />
              계정 설정
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                // 로그인 기능이 아직 없어서 일부러 아무 동작도 하지 않는다.
                // 로그인이 붙으면 여기서 로그아웃(세션/토큰 삭제 후 로그인 화면 이동)을 처리한다.
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
            >
              <LogOutIcon />
              로그아웃
            </button>
          </div>,
          document.body
        )}

      {convMenuId &&
        convMenuPos &&
        (() => {
          const conv = conversations.find((c) => c.id === convMenuId);
          if (!conv) return null;
          return createPortal(
            <div
              ref={convMenuRef}
              style={{ position: "fixed", left: convMenuPos.left, top: convMenuPos.top }}
              className="z-50 w-40 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
            >
              <button
                type="button"
                onClick={() => {
                  onTogglePinConversation(conv.id, !conv.pinned);
                  setConvMenuId(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <PinIcon />
                {conv.pinned ? "고정 해제" : "고정"}
              </button>
              <button
                type="button"
                onClick={() => startRename(conv.id)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <PencilIcon />
                이름 변경
              </button>
              <button
                type="button"
                onClick={() => {
                  setConvMenuId(null);
                  if (!disabled) onDelete(conv.id);
                }}
                disabled={disabled}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-900/30"
              >
                <TrashIcon />
                삭제
              </button>
            </div>,
            document.body
          );
        })()}

      {projMenuId &&
        projMenuPos &&
        (() => {
          const proj = projects.find((p) => p.id === projMenuId);
          if (!proj) return null;
          return createPortal(
            <div
              ref={projMenuRef}
              style={{ position: "fixed", left: projMenuPos.left, top: projMenuPos.top }}
              className="z-50 w-40 overflow-hidden rounded-xl border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800"
            >
              <button
                type="button"
                onClick={() => {
                  onTogglePinProject(proj.id, !proj.pinned);
                  setProjMenuId(null);
                }}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <PinIcon />
                {proj.pinned ? "고정 해제" : "고정"}
              </button>
              <button
                type="button"
                onClick={() => startRenameProject(proj.id)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <PencilIcon />
                이름 변경
              </button>
              <button
                type="button"
                onClick={() => {
                  setProjMenuId(null);
                  if (!disabled) onDeleteProject(proj.id);
                }}
                disabled={disabled}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:text-red-400 dark:hover:bg-red-900/30"
              >
                <TrashIcon />
                삭제
              </button>
            </div>,
            document.body
          );
        })()}
    </aside>
  );
}
