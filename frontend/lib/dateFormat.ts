import { format, isSameDay } from "date-fns";
import { ko } from "date-fns/locale";

export function formatDateDivider(iso: string): string {
  return format(new Date(iso), "yyyy년 M월 d일", { locale: ko });
}

export function formatHoverTime(iso: string): string {
  // 예: 오전 2:30
  return format(new Date(iso), "a h:mm", { locale: ko });
}

export function isDifferentDay(a: string, b: string): boolean {
  return !isSameDay(new Date(a), new Date(b));
}
