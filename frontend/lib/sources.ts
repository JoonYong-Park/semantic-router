// 답변 끝의 출처 목록을 떼어 내 칩으로 보여 주기 위한 파서.
// 웹 검색 툴은 모델에게 답변 끝에 아래 형식으로 출처를 적게 한다(backend/app/tools/web_search.py):
//   ---
//   출처:
//   - [1] 제목 (https://...)
// 이 블록을 본문에서 빼고 {제목, URL, 사이트 이름}으로 돌려준다. 저장된 옛 답변에도 그대로 적용된다.

export interface Source {
  index: number;
  title: string;
  url: string;
  host: string;
  siteName: string;
}

// "출처:", "**출처**", "출처 :" 같은 머리글 줄
const HEADER = /^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*(?:출처|참고 ?자료|Sources?)\s*(?:\*\*)?\s*:?\s*(?:\*\*)?\s*$/i;
// "- [1] 제목 (URL)" / "* [1] [제목](URL)" / "1. 제목 - URL" 등
const ITEM_PAREN = /^\s*(?:[-*]|\d+\.)?\s*(?:\[(\d+)\])?\s*(.+?)\s*\(\s*<?(https?:\/\/[^\s)>]+)>?\s*\)\s*$/;
const ITEM_LINK = /^\s*(?:[-*]|\d+\.)?\s*(?:\[(\d+)\])?\s*\[([^\]]+)\]\(\s*(https?:\/\/[^\s)]+)\s*\)\s*$/;
const ITEM_BARE = /^\s*(?:[-*]|\d+\.)?\s*(?:\[(\d+)\])?\s*(.*?)\s*[-–:]?\s*<?(https?:\/\/[^\s>]+)>?\s*$/;

// 자주 나오는 도메인은 사람이 읽는 이름으로
const SITE_NAMES: Record<string, string> = {
  "yna.co.kr": "연합뉴스", "newsis.com": "뉴시스", "news1.kr": "뉴스1",
  "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
  "hani.co.kr": "한겨레", "khan.co.kr": "경향신문", "mk.co.kr": "매일경제", "hankyung.com": "한국경제",
  "kbs.co.kr": "KBS", "imbc.com": "MBC", "sbs.co.kr": "SBS", "ytn.co.kr": "YTN", "jtbc.co.kr": "JTBC",
  "weather.go.kr": "기상청", "kma.go.kr": "기상청", "naver.com": "네이버", "daum.net": "다음",
  "namu.wiki": "나무위키", "wikipedia.org": "위키백과", "tistory.com": "티스토리", "velog.io": "velog",
  "github.com": "GitHub", "youtube.com": "YouTube", "gsretail.com": "GS리테일",
  "aws.amazon.com": "AWS", "cloud.google.com": "Google Cloud", "learn.microsoft.com": "Microsoft Learn",
  "openai.com": "OpenAI", "anthropic.com": "Anthropic", "nextjs.org": "Next.js", "python.org": "Python",
  "open-meteo.com": "Open-Meteo",
};

// co.kr, go.kr처럼 두 칸짜리 최상위 도메인
const SECOND_LEVEL = new Set(["co", "go", "or", "ac", "ne", "re", "pe", "com", "net", "org", "gov", "edu"]);

function baseDomain(host: string): string {
  const parts = host.replace(/^www\./, "").split(".");
  if (parts.length <= 2) return parts.join(".");
  const tld = parts[parts.length - 1];
  const sld = parts[parts.length - 2];
  return tld.length === 2 && SECOND_LEVEL.has(sld) ? parts.slice(-3).join(".") : parts.slice(-2).join(".");
}

function siteNameOf(host: string): string {
  const h = host.replace(/^www\./, "");
  // 가장 구체적인 이름부터: docs.aws.amazon.com → aws.amazon.com(AWS) → amazon.com
  const labels = h.split(".");
  for (let i = 0; i < labels.length - 1; i++) {
    const name = SITE_NAMES[labels.slice(i).join(".")];
    if (name) return name;
  }
  return baseDomain(h);
}

function parseItem(line: string, fallbackIndex: number): Source | null {
  const m = line.match(ITEM_LINK) ?? line.match(ITEM_PAREN) ?? line.match(ITEM_BARE);
  if (!m) return null;
  let url: URL;
  try {
    url = new URL(m[3]);
  } catch {
    return null;
  }
  const host = url.hostname;
  const title = (m[2] || "").replace(/^[-–:]\s*/, "").trim() || host;
  return { index: m[1] ? Number(m[1]) : fallbackIndex, title, url: url.toString(), host, siteName: siteNameOf(host) };
}

/** 본문과 출처 목록으로 나눈다. 출처 블록이 없으면 sources는 빈 배열이고 본문은 그대로다. */
export function splitSources(content: string): { body: string; sources: Source[] } {
  const lines = content.split("\n");
  let header = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (HEADER.test(lines[i])) {
      header = i;
      break;
    }
  }
  if (header === -1) return { body: content, sources: [] };

  const sources: Source[] = [];
  for (const line of lines.slice(header + 1)) {
    if (!line.trim()) continue;
    const item = parseItem(line, sources.length + 1);
    if (!item) return { body: content, sources: [] }; // 출처 목록이 아닌 내용이 섞여 있으면 손대지 않는다
    if (!sources.some((s) => s.url === item.url)) sources.push(item);
  }

  // 머리글 바로 앞의 구분선(---)과 빈 줄도 함께 뺀다
  let end = header;
  while (end > 0 && (!lines[end - 1].trim() || /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(lines[end - 1]))) end--;
  return { body: lines.slice(0, end).join("\n"), sources };
}

export function faviconUrl(host: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=64`;
}
