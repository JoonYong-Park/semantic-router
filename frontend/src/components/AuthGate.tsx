// 로그인 여부에 따라 앱(AppShell) 또는 로그인 화면을 보여준다.
// 시작할 때 /auth/reissue로 access 토큰을 받아 본다 - refresh 쿠키가 살아 있으면 바로 로그인 상태.
// 이후 재발급이 실패하면(refresh 만료/폐기) onAuthChange로 알림을 받아 로그인 화면으로 돌아간다.
// PUBLIC_PATHS(개인정보처리방침 등)는 로그인 없이, 앱 화면(AppShell) 밖에서 page만 보여준다.
// 게스트 모드(lib/guest.ts): 로그인 없이 더미 데이터로 앱을 둘러본다. 서버 요청은 없고 쓰기는 막힌다.
import { useLocation } from "react-router";
import { useEffect, useState } from "react";
import { onAuthChange, refreshAccessToken, startLogin } from "@/lib/auth";
import { enterGuest, isGuest } from "@/lib/guest";

const PUBLIC_PATHS = ["/privacy"];

type Status = "checking" | "in" | "out" | "guest";

export default function AuthGate({ children, page }: { children: React.ReactNode; page: React.ReactNode }) {
  const isPublic = PUBLIC_PATHS.includes(useLocation().pathname);
  const [status, setStatus] = useState<Status>("checking");
  const [loginError, setLoginError] = useState(false);

  useEffect(() => {
    if (isPublic) return;
    if (isGuest()) {
      setStatus("guest");
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (params.has("login_error")) {
      setLoginError(true);
      window.history.replaceState(null, "", window.location.pathname);
    }
    const off = onAuthChange((loggedIn) => setStatus(loggedIn ? "in" : "out"));
    refreshAccessToken().then((token) => setStatus(token ? "in" : "out"));
    return () => {
      off();
    };
  }, [isPublic]);

  if (isPublic) {
    return <>{page}</>;
  }
  if (status === "checking") {
    return <div className="h-screen w-full bg-white dark:bg-gray-950" />;
  }
  if (status === "out") {
    return (
      <LoginScreen
        error={loginError}
        onGuest={() => {
          enterGuest();
          setStatus("guest");
        }}
      />
    );
  }
  if (status === "guest") {
    return <>{children}</>; // 게스트 안내는 사이드바(Sidebar.tsx)에 있다
  }
  return <>{children}</>;
}

function LoginScreen({ error, onGuest }: { error: boolean; onGuest: () => void }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-gray-50 px-4 dark:bg-gray-950">
      <div className="w-full max-w-sm rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <img src="/icon.png" alt="" className="mx-auto h-12 w-12 rounded-xl" />
        <h1 className="mt-4 text-xl font-semibold text-gray-900 dark:text-gray-100">Joomini</h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">질문에 맞는 모델을 골라 답하는 AI 채팅</p>

        {error && (
          <p className="mt-5 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/40 dark:text-red-400">
            로그인하지 못했습니다. 다시 시도해 주세요.
          </p>
        )}

        <button
          type="button"
          onClick={startLogin}
          className="mt-6 flex w-full items-center justify-center gap-3 rounded-xl border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-800 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
        >
          <GoogleIcon />
          Google 계정으로 계속하기
        </button>

        <button
          type="button"
          onClick={onGuest}
          className="mt-3 w-full rounded-xl px-4 py-2.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
        >
          로그인 없이 둘러보기
        </button>

        <a href="/privacy" className="mt-6 inline-block text-xs text-gray-400 hover:underline dark:text-gray-500">
          개인정보처리방침
        </a>
      </div>
    </main>
  );
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
