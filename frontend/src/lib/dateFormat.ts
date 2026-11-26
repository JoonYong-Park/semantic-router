import { format, isSameDay } from "date-fns";
import { ko } from "date-fns/locale";

export function formatDateDivider(iso: string): string {
  return format(new Date(iso), "yyyy년 M월 d일", { locale: ko });
}

export function formatHoverTime(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  // 오늘이면 시간만(예: 오전 2:30), 하루라도 지났으면 날짜까지 같이 표시
  // (연도가 다르면 연도도 포함) - 날짜 없이 시간만 보면 언제인지 헷갈리므로.
  if (isSameDay(date, now)) {
    return format(date, "a h:mm", { locale: ko });
  }
  const datePattern = date.getFullYear() === now.getFullYear() ? "M월 d일" : "yyyy년 M월 d일";
  return format(date, `${datePattern} a h:mm`, { locale: ko });
}

export function isDifferentDay(a: string, b: string): boolean {
  return !isSameDay(new Date(a), new Date(b));
}

// "2026-09-15" 같은 YYYY-MM-DD 문자열용. new Date(ymd)로 바로 파싱하면 UTC
// 자정으로 해석되어 브라우저 시간대에 따라 하루 밀릴 수 있어, 연/월/일을
// 직접 뽑아 로컬 Date로 만든다.
export function formatMonthDay(ymd: string): string {
  const [y, m, d] = ymd.split("-").map(Number);
  return format(new Date(y, m - 1, d), "M월 d일", { locale: ko });
}
