// React SPA 진입점. 주소별 화면은 React Router가 고른다.
//
//   /               새 채팅 (HomePage)
//   /c/:id          채팅방 (ChatPage)
//   /projects/:id   프로젝트 (ProjectPage)
//   /privacy        개인정보처리방침 (로그인 없이, 앱 화면 밖)
//
// 배포에서는 index.html 하나를 S3에 두고, CloudFront가 파일이 아닌 경로(/c/abc 등)를 모두
// index.html로 돌려준다(infra/cloudfront.yaml SpaRouting). 그 뒤 이 라우터가 주소를 읽어 화면을 그린다.
import { Fragment, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Navigate, Outlet, RouterProvider, useParams } from "react-router";
import AppShell from "@/components/AppShell";
import AuthGate from "@/components/AuthGate";
import ChatPage from "@/pages/ChatPage";
import HomePage from "@/pages/HomePage";
import PrivacyPage from "@/pages/PrivacyPage";
import ProjectPage from "@/pages/ProjectPage";
import "./index.css";

// 사이드바, 설정 창 같은 공통 부분(AppShell)은 화면이 바뀌어도 다시 마운트되지 않는다.
// 공개 페이지(/privacy)는 AuthGate가 AppShell 없이 page만 보여준다.
function RootLayout() {
  return (
    <AuthGate page={<Outlet />}>
      <AppShell>
        <Outlet />
      </AppShell>
    </AuthGate>
  );
}

// 다른 채팅방(/c/a에서 /c/b)으로 가면 화면 상태(메시지, 입력, 스트리밍)를 처음부터 시작한다.
// React Router는 같은 라우트면 컴포넌트를 재사용하므로 id를 key로 줘서 새로 마운트한다.
function KeyedById({ render }: { render: () => JSX.Element }) {
  const { id } = useParams();
  return <Fragment key={id}>{render()}</Fragment>;
}

const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      { path: "/", element: <HomePage /> },
      { path: "/c/:id", element: <KeyedById render={() => <ChatPage />} /> },
      { path: "/projects/:id", element: <KeyedById render={() => <ProjectPage />} /> },
      { path: "/privacy", element: <PrivacyPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
