// 로그인 토큰 관리.
// - access 토큰은 이 모듈의 변수(JS 메모리)에만 둔다. localStorage/쿠키에 저장하지 않는다
//   → 새로고침하면 사라지고, 앱 시작 때 /auth/reissue로 다시 받는다.
// - refresh 토큰은 백엔드가 HttpOnly 쿠키(Path=/auth)로 관리해 JS가 읽을 수 없다.
// - 모든 API 호출은 apiFetch를 거친다: Bearer 헤더를 붙이고, 401이면 한 번 재발급 후 재시도.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

let accessToken: string | null = null;
let expiresAt = 0; // ms
let refreshing: Promise<string | null> | null = null;
const listeners = new Set<(loggedIn: boolean) => void>();

// 로그인 상태가 바뀌면(재발급 실패 = 로그아웃) AuthGate에 알린다.
export function onAuthChange(fn: (loggedIn: boolean) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setLoggedOut() {
  accessToken = null;
  expiresAt = 0;
  listeners.forEach((fn) => fn(false));
}

/** refresh 쿠키로 새 access 토큰을 받는다. 동시에 여러 요청이 401을 맞아도 한 번만 호출한다. */
export function refreshAccessToken(): Promise<string | null> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(`${API_URL}/auth/reissue`, {
          method: "POST",
          credentials: "include", // refresh 쿠키는 /auth 요청에만 실린다
          headers: { "X-Requested-With": "joomini" },
        });
        if (!res.ok) {
          setLoggedOut();
          return null;
        }
        const data: { access_token: string; expires_in: number } = await res.json();
        accessToken = data.access_token;
        // 만료 30초 전부터는 미리 재발급한다.
        expiresAt = Date.now() + (data.expires_in - 30) * 1000;
        return accessToken;
      } catch {
        // 네트워크 오류는 로그아웃으로 보지 않는다 (서버 재시작 중 등).
        return null;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

/** 유효한 access 토큰. 없거나 곧 만료면 재발급한다. */
export async function getAccessToken(): Promise<string | null> {
  if (accessToken && Date.now() < expiresAt) return accessToken;
  return refreshAccessToken();
}

/** fetch와 같은 모양. Authorization 헤더를 붙이고 401이면 재발급 후 한 번 다시 보낸다. */
export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string | null) => {
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(url, { ...init, headers });
  };
  const res = await send(await getAccessToken());
  if (res.status !== 401) return res;
  const fresh = await refreshAccessToken();
  return fresh ? send(fresh) : res;
}

/** Google 로그인 시작 - 백엔드가 Cognito(→ Google)로 보낸다. */
export function startLogin() {
  window.location.href = `${API_URL}/auth/login`;
}

/** refresh 토큰 폐기 + 쿠키 삭제 후 Cognito 로그아웃(→ 다시 우리 첫 화면). */
export async function logout() {
  let next = "/";
  try {
    const res = await fetch(`${API_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: { "X-Requested-With": "joomini" },
    });
    if (res.ok) next = (await res.json()).logout_url ?? "/";
  } catch {
    // 서버에 못 닿아도 화면에서는 로그아웃 처리한다.
  }
  accessToken = null;
  expiresAt = 0;
  window.location.href = next;
}
