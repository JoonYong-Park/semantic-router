// 사이드바(AppShell)가 소유한 conversations/projects 전역 상태를, 라우트별
// 리프 페이지(/, /c/[id], /projects/[id])에서도 읽고 바꿀 수 있게 하는 컨텍스트.
//
// 레이아웃(AppShell)은 페이지를 React Router의 <Outlet />으로 받아서 props를 직접
// 넘길 수 없다. 그래서 레이아웃에서 페이지 방향의 데이터 전달은 Context로 한다.
// AppShell.tsx가 Provider 역할을 겸한다.

import { createContext, Dispatch, SetStateAction, useContext } from "react";
import { Conversation, Project } from "@/lib/types";

export interface AppDataContextValue {
  conversations: Conversation[];
  setConversations: Dispatch<SetStateAction<Conversation[]>>;
  refreshConversations: () => Promise<void>;
  projects: Project[];
  setProjects: Dispatch<SetStateAction<Project[]>>;

  // 스트리밍 중에는 사이드바 네비게이션(새 채팅/다른 채팅 선택/삭제)이 막혀야
  // 하므로(기존 app/page.tsx 160행 가드와 동일한 정책), 실제 스트리밍 상태를
  // 갖고 있는 /(홈) 또는 /c/[id] 페이지가 이 값을 보고해서 AppShell이
  // Sidebar에 disabled로 내려준다.
  isStreaming: boolean;
  setIsStreaming: (value: boolean) => void;

  handleMoveConversationToProject: (
    conversationId: string,
    projectId: string | null
  ) => Promise<void>;
  handleRenameConversation: (id: string, title: string) => Promise<void>;
  handleTogglePinConversation: (id: string, pinned: boolean) => Promise<void>;
  handleTogglePinProject: (id: string, pinned: boolean) => Promise<void>;
  handleReorderProjects: (orderedProjectIds: string[]) => Promise<void>;
  handleReorderPinned: (
    orderedItems: { type: "project" | "conversation"; id: string }[]
  ) => Promise<void>;

  // 프로젝트 페이지의 "메모리 바로가기" - 메모리 설정 모달을 그 프로젝트가
  // 펼쳐진 채로 연다.
  openMemoryForProject: (projectId: string) => void;
}

export const AppDataContext = createContext<AppDataContextValue | null>(null);

export function useAppData(): AppDataContextValue {
  const ctx = useContext(AppDataContext);
  if (!ctx) {
    throw new Error("useAppData()는 AppShell(레이아웃) 안에서만 쓸 수 있습니다.");
  }
  return ctx;
}
