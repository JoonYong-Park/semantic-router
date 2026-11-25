// 게스트 모드 (로그인 없이 둘러보기).
// - 서버에 요청을 보내지 않는다. apiFetch가 게스트일 때 이 파일의 guestResponse로 응답을 만든다
//   → 백엔드 인증(401)과 비용(모델 호출)에 손대지 않고, 화면만 그대로 보여 준다.
// - 조회(GET)는 아래 더미 데이터로 답하고, 쓰기(POST/PUT/PATCH/DELETE)는 403으로 막는다.
// - 플래그는 sessionStorage에 둔다: 새로고침해도 유지되고, 탭을 닫으면 사라진다.

import type {
  Conversation,
  DBMessage,
  MemoryOverview,
  Project,
  ProjectFilesOut,
  SearchResult,
  Settings,
  UsageQuota,
  UsageStats,
  User,
} from "./types";

const GUEST_KEY = "joomini_guest";
export const GUEST_READONLY_MESSAGE = "게스트 모드에서는 둘러보기만 할 수 있어요. 로그인하면 바로 사용할 수 있어요.";

export function isGuest(): boolean {
  try {
    return typeof window !== "undefined" && window.sessionStorage.getItem(GUEST_KEY) === "1";
  } catch {
    return false;
  }
}

export function enterGuest(): void {
  try {
    window.sessionStorage.setItem(GUEST_KEY, "1");
  } catch {
    // 저장이 막힌 브라우저(사생활 보호 모드 등)는 이번 화면에서만 게스트로 본다.
  }
}

export function exitGuest(): void {
  try {
    window.sessionStorage.removeItem(GUEST_KEY);
  } catch {
    // 무시
  }
}

// --- 더미 데이터 (시각은 "지금" 기준으로 만들어 날짜 구분선이 자연스럽게 보이게 한다) ---

const now = Date.now();
const ago = (hours: number) => new Date(now - hours * 3600_000).toISOString();

const PROJECTS: Project[] = [
  {
    id: "guest-p-portfolio",
    name: "포트폴리오 준비",
    instruction: "답변은 면접에서 말하듯 짧고 구체적으로. 숫자와 근거를 함께 적어 줘.",
    max_capacity_bytes: 50 * 1024 * 1024,
    pinned: true,
    sort_order: 0,
    color: "blue",
    created_at: ago(24 * 20),
    updated_at: ago(3),
  },
  {
    id: "guest-p-aws",
    name: "AWS 서버리스 스터디",
    instruction: "AWS 공식 문서 기준으로 설명하고, 비용과 제한도 함께 알려 줘.",
    max_capacity_bytes: 50 * 1024 * 1024,
    pinned: false,
    sort_order: 1,
    color: "orange",
    created_at: ago(24 * 12),
    updated_at: ago(26),
  },
];

const CONVERSATIONS: Conversation[] = [
  { id: "guest-c-router", title: "질문마다 모델을 고르는 원리", updated_at: ago(1), project_id: null, pinned: true, sort_order: 0 },
  { id: "guest-c-math", title: "확률 문제 풀이", updated_at: ago(5), project_id: null, pinned: false, sort_order: 0 },
  { id: "guest-c-resume", title: "자기소개 1분 버전 다듬기", updated_at: ago(3), project_id: "guest-p-portfolio", pinned: false, sort_order: 0 },
  { id: "guest-c-coldstart", title: "Lambda 콜드 스타트 줄이기", updated_at: ago(26), project_id: "guest-p-aws", pinned: false, sort_order: 0 },
  { id: "guest-c-dynamo", title: "DynamoDB 단일 테이블 설계", updated_at: ago(50), project_id: "guest-p-aws", pinned: false, sort_order: 0 },
  { id: "guest-c-trip", title: "부산 1박 2일 일정", updated_at: ago(24 * 4), project_id: null, pinned: false, sort_order: 0 },
];

function msg(
  id: string,
  role: "user" | "assistant",
  content: string,
  hoursAgo: number,
  model?: [string, string, number],
): DBMessage {
  return {
    id,
    role,
    content,
    model_used: model?.[0] ?? null,
    task_category: model?.[1] ?? null,
    complexity_score: model?.[2] ?? null,
    created_at: ago(hoursAgo),
    attachments: [],
  };
}

const MESSAGES: Record<string, DBMessage[]> = {
  "guest-c-router": [
    msg("m1", "user", "Joomini는 질문마다 어떤 모델을 쓸지 어떻게 정해?", 1.1),
    msg(
      "m2",
      "assistant",
      "두 단계로 정해요.\n\n1. **분야 분류:** 질문을 14개 분야(수학, 컴퓨터 과학, 법 등) 중 하나로 분류해요. 분야마다 잘하는 회사(OpenAI, Google, Anthropic)가 달라요.\n2. **복잡도 측정:** 미리 점수를 매겨 둔 예시 질문들과 비교(kNN)해서 0~1 사이 복잡도를 구해요. 복잡할수록 큰 모델을 써요.\n\n그래서 간단한 인사는 작은 모델이, 어려운 증명은 큰 모델이 답해요. 비용은 줄이고 품질은 지키는 구조예요.",
      1,
      ["Claude Opus 5.5", "computer science", 0.46],
    ),
  ],
  "guest-c-math": [
    msg("m1", "user", "주사위 두 개를 던져 합이 7이 나올 확률은?", 5.1),
    msg(
      "m2",
      "assistant",
      "전체 경우의 수는 6 × 6 = **36**가지예요.\n\n합이 7인 경우는 (1,6), (2,5), (3,4), (4,3), (5,2), (6,1)로 **6**가지예요.\n\n따라서 확률은 6/36 = **1/6** (약 16.7%)이에요.",
      5,
      ["Gemini 3.8 Flash", "math", 0.21],
    ),
  ],
  "guest-c-resume": [
    msg("m1", "user", "서버리스로 옮긴 경험을 1분 자기소개에 넣고 싶어. 짧게 다듬어 줘.", 3.1),
    msg(
      "m2",
      "assistant",
      "이렇게 말해 보세요.\n\n> 질문에 맞는 AI 모델을 골라 주는 서비스를 혼자 만들었습니다. 처음엔 서버 한 대에서 돌렸는데, 사용량이 들쭉날쭉해서 AWS Lambda와 DynamoDB로 옮겼고, 쓰지 않을 때는 비용이 거의 0이 되게 했습니다. 배포는 GitHub Actions에서 OIDC로 AWS 키 없이 자동으로 되게 만들었고, 로그인은 Cognito와 Google을 붙여 토큰을 HttpOnly 쿠키와 메모리에 나눠 보관했습니다.\n\n숫자(비용, 응답 시간)를 하나 넣으면 더 좋아요.",
      3,
      ["GPT-6.1 Sol", "business", 0.38],
    ),
  ],
  "guest-c-coldstart": [
    msg("m1", "user", "컨테이너 이미지 Lambda가 처음 호출될 때 느려. 줄이는 방법은?", 26.2),
    msg(
      "m2",
      "assistant",
      "효과가 큰 순서로 정리하면:\n\n- **이미지 줄이기:** 쓰지 않는 패키지(예: 큰 ML 라이브러리)를 빼면 다운로드와 초기화가 빨라져요.\n- **무거운 import 미루기:** 모델 로딩처럼 오래 걸리는 작업은 필요할 때 처음 한 번만 해요.\n- **미리 깨워 두기:** EventBridge로 몇 분마다 호출하거나, 비용을 내고 Provisioned Concurrency를 써요.\n- **메모리 늘리기:** Lambda는 메모리에 비례해 CPU도 늘어서 초기화가 빨라져요.",
      26,
      ["Claude Sonnet 5.5", "engineering", 0.52],
    ),
  ],
  "guest-c-dynamo": [
    msg("m1", "user", "대화방이랑 메시지를 DynamoDB 테이블 하나에 넣으려면 키를 어떻게 잡아?", 50.2),
    msg(
      "m2",
      "assistant",
      "사용자별로 묶는 게 기본이에요.\n\n| 항목 | PK | SK |\n|---|---|---|\n| 대화방 | `USER#{id}` | `CONV#{convId}` |\n| 메시지 | `CONV#{convId}` | `MSG#{시각}#{msgId}` |\n| 프로젝트 | `USER#{id}` | `PROJ#{projId}` |\n\n이렇게 하면 \"내 대화방 목록\"과 \"대화방의 메시지 순서대로\"를 각각 Query 한 번으로 읽을 수 있어요. 다른 사용자의 데이터는 PK가 달라 섞이지 않아요.",
      50,
      ["GPT-6 Astra", "computer science", 0.71],
    ),
  ],
  "guest-c-trip": [
    msg("m1", "user", "부산 1박 2일, 대중교통으로 다닐 일정 짜 줘.", 24 * 4 + 0.2),
    msg(
      "m2",
      "assistant",
      "**1일차:** 부산역 → 초량 이바구길 → 남포동·자갈치 시장 점심 → 감천문화마을 → 광안리 야경\n\n**2일차:** 해운대 해변 산책 → 동백섬 → 해리단길 점심 → 블루라인파크 해변열차 → 부산역\n\n지하철 1·2호선과 버스로 모두 갈 수 있어요.",
      24 * 4,
      ["Gemini 3.5 Flash Lite", "other", 0.12],
    ),
  ],
};

const USER: User = { id: "guest", email: "guest@joomini", name: "게스트", role: "guest", avatar_updated_at: null };

const SETTINGS: Settings = { personal_instruction: null, imported_memory: null };

const QUOTA: UsageQuota = {
  limit: 1_000_000,
  used: 0,
  remaining: 1_000_000,
  percent: 0,
  cycle_start: ago(24 * 10),
  cycle_end: new Date(now + 20 * 24 * 3600_000).toISOString(),
  days_left: 20,
};

const MEMORY: MemoryOverview = {
  global: [
    {
      category: "나",
      topics: [
        {
          id: "guest-mem-1",
          name: "관심 분야",
          summary: "AWS 서버리스와 LLM 서비스 개발에 관심이 많다.",
          details: ["Lambda, DynamoDB로 서비스를 운영 중", "면접 준비 중"],
          updated_at: ago(3),
        },
      ],
    },
  ],
  projects: [
    {
      project_id: "guest-p-aws",
      project_name: "AWS 서버리스 스터디",
      topics: [
        {
          id: "guest-mem-2",
          name: "학습 범위",
          summary: "Lambda 콜드 스타트, DynamoDB 키 설계를 공부했다.",
          details: [],
          updated_at: ago(26),
        },
      ],
    },
  ],
};

function emptyStats(period: string): UsageStats {
  return {
    period: (period as UsageStats["period"]) || "week",
    range_label: "게스트 모드",
    summary: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
    chart: [],
    by_model: [],
  };
}

function search(query: string): SearchResult[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const results: SearchResult[] = [];
  for (const conv of CONVERSATIONS) {
    const hit = (MESSAGES[conv.id] ?? []).find((m) => m.content.toLowerCase().includes(q));
    if (hit || (conv.title ?? "").toLowerCase().includes(q)) {
      const text = hit?.content ?? conv.title ?? "";
      results.push({ conversation_id: conv.id, title: conv.title, snippet: text.slice(0, 80), matched_role: hit?.role ?? "user" });
    }
  }
  return results;
}

// --- 요청 → 응답 ---

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function route(path: string, params: URLSearchParams): unknown | undefined {
  let m: RegExpMatchArray | null;
  if (path === "/conversations") return CONVERSATIONS;
  if ((m = path.match(/^\/conversations\/([^/]+)\/messages$/))) return MESSAGES[m[1]] ?? [];
  if (path === "/projects") return PROJECTS;
  if ((m = path.match(/^\/projects\/([^/]+)$/))) return PROJECTS.find((p) => p.id === m![1]);
  if ((m = path.match(/^\/projects\/([^/]+)\/files$/))) {
    const files: ProjectFilesOut = { files: [], used_bytes: 0, max_bytes: 50 * 1024 * 1024, percent: 0 };
    return files;
  }
  if (path === "/users/me") return USER;
  if (path === "/settings") return SETTINGS;
  if (path === "/settings/memory") return MEMORY;
  if (path === "/usage/quota") return QUOTA;
  if (path === "/usage/stats") return emptyStats(params.get("period") ?? "week");
  if (path === "/search") return search(params.get("query") ?? "");
  return undefined;
}

/** apiFetch가 게스트일 때 부른다. 서버로 나가는 요청은 없다. */
export function guestResponse(url: string, init: RequestInit = {}, apiUrl: string): Response {
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD") {
    return json({ detail: GUEST_READONLY_MESSAGE }, 403);
  }
  const full = new URL(url, window.location.origin);
  const base = new URL(apiUrl, window.location.origin).pathname.replace(/\/$/, "");
  const path = full.pathname.startsWith(base) ? full.pathname.slice(base.length) || "/" : full.pathname;
  // 진행 중인 응답 이어 받기: 게스트에게는 없다.
  if (/^\/conversations\/[^/]+\/stream$/.test(path)) return new Response(null, { status: 204 });
  const body = route(path, full.searchParams);
  return body === undefined ? json({ detail: "게스트 모드에서는 볼 수 없는 항목이에요." }, 404) : json(body);
}
