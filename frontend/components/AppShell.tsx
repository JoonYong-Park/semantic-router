"use client";

// 라우트가 바뀌어도(=/, /c/[id], /projects/[id] 사이를 오가도) 절대 다시
// 마운트되면 안 되는 부분 - 사이드바(채팅/프로젝트 목록, 드래그앤드롭, 고정),
// 설정류 모달들, "메모리 업데이트" 플로팅 버튼 - 을 전부 여기 하나로 모은
// 클라이언트 레이아웃. app/layout.tsx가 {children}을 이 컴포넌트로 감싼다.
//
// conversations/projects 같은 전역 목록 상태는 여기서만 소유하고, 리프
// 페이지(app/page.tsx, app/c/[id]/page.tsx, app/projects/[id]/page.tsx)는
// AppDataContext(useAppData())로 읽고 바꾼다 - React 트리를 관통하는 Context는
// Next.js가 {children}을 내부적으로 어떻게 감싸든(스트리밍 바운더리 등) 영향을
// 안 받지만, 레이아웃이 children에 직접 props를 얹어 내려보내는 방식
// (cloneElement 등)은 App Router에서 신뢰할 수 없어서 이 방식을 택했다.
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "@/components/Sidebar";
import SearchModal from "@/components/SearchModal";
import SettingsWindow, { SettingsTab } from "@/components/SettingsWindow";
import MemoryUpdateButton from "@/components/MemoryUpdateButton";
import { AppDataContext, AppDataContextValue } from "@/contexts/AppDataContext";
import {
  createProject,
  deleteConversation,
  deleteProject,
  listConversations,
  listProjects,
  updateConversation,
  updateConversationProject,
  updateProject,
} from "@/lib/api";
import { Conversation, Project } from "@/lib/types";

export default function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  // "채팅 검색"은 설정과 무관한 별도 사이드바 버튼이라 자기 state를 따로 둔다.
  const [searchOpen, setSearchOpen] = useState(false);
  // "설정" 버튼은 이제 개인 지침/가져오기/토큰 사용량/메모리/화면(다크모드)을
  // 탭으로 묶은 창 하나를 연다(예전엔 드롭다운 메뉴 → 항목별 개별 모달).
  // null이면 닫힌 상태, 열려있으면 어느 탭으로 열지(+ 메모리 탭이면 어느
  // 프로젝트를 펼쳐둘지)를 들고 있는다.
  const [settingsWindow, setSettingsWindow] = useState<{
    tab: SettingsTab;
    memoryProjectId?: string | null;
  } | null>(null);
  // /(홈) 또는 /c/[id] 페이지가 실제 스트리밍 여부를 보고해오는 값 - 사이드바
  // 네비게이션(다른 채팅 선택/새 채팅/삭제)을 막는 데 쓴다(기존 activeId===id
  // 가드와 동일한 정책, 4번 항목).
  const [isStreaming, setIsStreaming] = useState(false);

  // "/c/{id}" 형태일 때만 id를 뽑아 Sidebar의 활성 항목 강조에 쓴다.
  const conversationIdMatch = pathname.match(/^\/c\/([^/]+)$/);
  const activeConversationId = conversationIdMatch ? conversationIdMatch[1] : null;
  // "/projects/{id}" 형태일 때만 id를 뽑아, 그 프로젝트를 지웠을 때 지금
  // 보고 있는 페이지면 홈으로 돌려보내는 데 쓴다.
  const projectIdMatch = pathname.match(/^\/projects\/([^/]+)$/);
  const activeProjectId = projectIdMatch ? projectIdMatch[1] : null;

  useEffect(() => {
    listConversations()
      .then(setConversations)
      .catch(() => setConversations([]));

    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));

    try {
      setSidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
    } catch {
      // localStorage 접근 불가(프라이빗 브라우징 등)해도 기본값(펼침)으로 그냥 진행
    }
  }, []);

  function toggleSidebar() {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("sidebarCollapsed", next ? "1" : "0");
      } catch {
        // 저장 실패해도 화면 전환 자체는 정상 동작하게 무시
      }
      return next;
    });
  }

  async function refreshConversations() {
    try {
      setConversations(await listConversations());
    } catch {
      // 목록 갱신 실패는 조용히 무시 (다음 액션에서 다시 시도됨)
    }
  }

  function handleNewChat() {
    if (isStreaming) return;
    router.push("/");
  }

  async function handleDeleteConversation(id: string) {
    if (isStreaming) return;
    setConversations((prev) => prev.filter((c) => c.id !== id));
    // 지금 보고 있던 채팅을 지운 거면 기본 화면으로 - 예전엔 activeId를 null로
    // 되돌리는 것으로 처리했는데, 이제는 그 자체가 라우팅 이동이다.
    if (activeConversationId === id) {
      router.push("/");
    }
    try {
      await deleteConversation(id);
    } catch {
      // 실패해도 목록에선 이미 지웠으니, 다음 refreshConversations 때 다시 나타나면
      // 그때 사용자가 인지할 수 있음 (데모 범위에선 재시도 UI까진 안 만듦)
    }
  }

  async function handleMoveConversationToProject(
    conversationId: string,
    projectId: string | null
  ) {
    setConversations((prev) =>
      prev.map((c) => (c.id === conversationId ? { ...c, project_id: projectId } : c))
    );
    try {
      await updateConversationProject(conversationId, projectId);
    } catch {
      // 실패하면 다음 refreshConversations 때 실제 상태로 다시 맞춰짐 (데모 범위)
    }
  }

  async function handleRenameConversation(id: string, title: string) {
    setConversations((prev) => prev.map((c) => (c.id === id ? { ...c, title } : c)));
    try {
      await updateConversation(id, { title });
    } catch {
      // 실패해도 조용히 무시 (데모 범위 - 다음 refreshConversations 때 원상복구됨)
    }
  }

  // "고정" 섹션은 프로젝트/채팅을 한 순서 공간(sort_order)에 같이 늘어놓으므로,
  // 새로 고정할 땐 그 공간에서 가장 뒤(맨 끝)에 붙도록 현재 최댓값+1을 매긴다.
  function nextPinnedSortOrder(): number {
    const values = [
      ...conversations.filter((c) => c.pinned).map((c) => c.sort_order),
      ...projects.filter((p) => p.pinned).map((p) => p.sort_order),
    ];
    return values.length === 0 ? 0 : Math.max(...values) + 1;
  }

  async function handleTogglePinConversation(id: string, pinned: boolean) {
    const sortOrder = pinned ? nextPinnedSortOrder() : undefined;
    setConversations((prev) =>
      prev.map((c) =>
        c.id === id
          ? { ...c, pinned, ...(sortOrder !== undefined ? { sort_order: sortOrder } : {}) }
          : c
      )
    );
    try {
      await updateConversation(id, {
        pinned,
        ...(sortOrder !== undefined ? { sort_order: sortOrder } : {}),
      });
    } catch {
      // 실패해도 조용히 무시
    }
  }

  async function handleTogglePinProject(id: string, pinned: boolean) {
    const sortOrder = pinned ? nextPinnedSortOrder() : undefined;
    setProjects((prev) =>
      prev.map((p) =>
        p.id === id
          ? { ...p, pinned, ...(sortOrder !== undefined ? { sort_order: sortOrder } : {}) }
          : p
      )
    );
    try {
      await updateProject(id, {
        pinned,
        ...(sortOrder !== undefined ? { sort_order: sortOrder } : {}),
      });
    } catch {
      // 실패해도 조용히 무시
    }
  }

  async function handleReorderPinned(
    orderedItems: { type: "project" | "conversation"; id: string }[]
  ) {
    const projectOrders = new Map<string, number>();
    const conversationOrders = new Map<string, number>();
    orderedItems.forEach((item, index) => {
      if (item.type === "project") projectOrders.set(item.id, index);
      else conversationOrders.set(item.id, index);
    });
    setProjects((prev) =>
      prev.map((p) => (projectOrders.has(p.id) ? { ...p, sort_order: projectOrders.get(p.id)! } : p))
    );
    setConversations((prev) =>
      prev.map((c) =>
        conversationOrders.has(c.id) ? { ...c, sort_order: conversationOrders.get(c.id)! } : c
      )
    );
    try {
      await Promise.all([
        ...Array.from(projectOrders.entries()).map(([id, sort_order]) =>
          updateProject(id, { sort_order })
        ),
        ...Array.from(conversationOrders.entries()).map(([id, sort_order]) =>
          updateConversation(id, { sort_order })
        ),
      ]);
    } catch {
      // 실패해도 조용히 무시 - 다음 새로고침 때 서버 값으로 다시 맞춰짐
    }
  }

  async function handleReorderProjects(orderedProjectIds: string[]) {
    const orders = new Map(orderedProjectIds.map((id, index) => [id, index]));
    setProjects((prev) =>
      prev.map((p) => (orders.has(p.id) ? { ...p, sort_order: orders.get(p.id)! } : p))
    );
    try {
      await Promise.all(
        Array.from(orders.entries()).map(([id, sort_order]) => updateProject(id, { sort_order }))
      );
    } catch {
      // 실패해도 조용히 무시
    }
  }

  async function handleNewProject() {
    try {
      const project = await createProject("새 프로젝트");
      setProjects((prev) => [...prev, project]);
      router.push(`/projects/${project.id}`);
    } catch {
      // 생성 실패해도 조용히 무시 - 사이드바 "+" 버튼을 다시 누르면 재시도됨
    }
  }

  async function handleRenameProject(id: string, name: string) {
    setProjects((prev) => prev.map((p) => (p.id === id ? { ...p, name } : p)));
    try {
      await updateProject(id, { name });
    } catch {
      // 실패해도 조용히 무시 (데모 범위)
    }
  }

  // 채팅 삭제와 달리 되돌릴 수 없는 데이터(참고자료/메모리/소속 채팅방
  // 전체)가 같이 지워지므로 confirm()으로 한 번 더 확인한다 - 예전 프로젝트
  // 관리 모달에 있던 것과 동일한 문구.
  async function handleDeleteProject(id: string) {
    const project = projects.find((p) => p.id === id);
    if (
      !confirm(
        `"${project?.name ?? "이 프로젝트"}" 프로젝트를 삭제하시겠습니까?\n소속된 채팅방과 참고자료, 메모리가 모두 함께 삭제되며 되돌릴 수 없습니다.`
      )
    ) {
      return;
    }
    const deletedConvIds = new Set(
      conversations.filter((c) => c.project_id === id).map((c) => c.id)
    );
    setProjects((prev) => prev.filter((p) => p.id !== id));
    setConversations((prev) => prev.filter((c) => c.project_id !== id));
    // 지금 이 프로젝트 페이지를 보고 있었거나, 삭제된 소속 채팅방을 보고
    // 있었으면 홈으로.
    if (activeProjectId === id || (activeConversationId && deletedConvIds.has(activeConversationId))) {
      router.push("/");
    }
    try {
      await deleteProject(id);
    } catch {
      // 실패해도 목록에선 이미 지웠으니 조용히 무시 (데모 범위)
    }
  }

  // 프로젝트 페이지의 "메모리 바로가기" - 설정 창을 메모리 탭, 그 프로젝트
  // 섹션이 펼쳐진 채로 연다.
  function openMemoryForProject(projectId: string) {
    setSettingsWindow({ tab: "memory", memoryProjectId: projectId });
  }

  const contextValue: AppDataContextValue = {
    conversations,
    setConversations,
    refreshConversations,
    projects,
    setProjects,
    isStreaming,
    setIsStreaming,
    handleMoveConversationToProject,
    handleRenameConversation,
    handleTogglePinConversation,
    handleTogglePinProject,
    handleReorderProjects,
    handleReorderPinned,
    openMemoryForProject,
  };

  return (
    <AppDataContext.Provider value={contextValue}>
      <div className="flex h-screen">
        <Sidebar
          conversations={conversations}
          activeId={activeConversationId}
          disabled={isStreaming}
          collapsed={sidebarCollapsed}
          onToggleCollapsed={toggleSidebar}
          onSelect={(id) => router.push(`/c/${id}`)}
          onNew={handleNewChat}
          onDelete={handleDeleteConversation}
          onOpenSettings={() => setSettingsWindow({ tab: "personal" })}
          onOpenSearch={() => setSearchOpen(true)}
          projects={projects}
          onOpenProject={(id) => router.push(`/projects/${id}`)}
          onNewProject={handleNewProject}
          onMoveConversationToProject={handleMoveConversationToProject}
          onRenameConversation={handleRenameConversation}
          onTogglePinConversation={handleTogglePinConversation}
          onTogglePinProject={handleTogglePinProject}
          onReorderProjects={handleReorderProjects}
          onReorderPinned={handleReorderPinned}
          onRenameProject={handleRenameProject}
          onDeleteProject={handleDeleteProject}
        />

        {searchOpen && (
          <SearchModal
            onClose={() => setSearchOpen(false)}
            onSelectConversation={(id) => {
              setSearchOpen(false);
              router.push(`/c/${id}`);
            }}
          />
        )}
        {settingsWindow && (
          <SettingsWindow
            initialTab={settingsWindow.tab}
            initialMemoryProjectId={settingsWindow.memoryProjectId}
            onClose={() => setSettingsWindow(null)}
          />
        )}

        <MemoryUpdateButton />

        {children}
      </div>
    </AppDataContext.Provider>
  );
}
