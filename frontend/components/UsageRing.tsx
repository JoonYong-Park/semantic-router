"use client";

import { useEffect, useState } from "react";
import { getUsageQuota } from "@/lib/api";
import { UsageQuota } from "@/lib/types";

// UsageStatsModal의 usageColors와 동일한 기준(0~70/70~90/90~100) - 여기는
// SVG stroke에 바로 넣어야 해서 Tailwind 클래스 대신 hex로 둔다.
function ringColor(percent: number): string {
  if (percent >= 90) return "#ef4444"; // red-500
  if (percent >= 70) return "#f59e0b"; // amber-500
  return "#3b82f6"; // blue-500
}

const RADIUS = 15.9155; // 원 둘레가 딱 100이 되는 반지름 (percent를 그대로 dash 길이로 씀)
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export default function UsageRing() {
  const [quota, setQuota] = useState<UsageQuota | null>(null);

  useEffect(() => {
    getUsageQuota()
      .then(setQuota)
      .catch(() => {
        // 조회 실패 시 링을 그냥 숨긴다
      });
  }, []);

  if (!quota) return null;

  // 링 채움은 100%를 넘지 않게 clamp하되, 표시 텍스트는 토큰 사용량 페이지와
  // 동일하게 백엔드가 내려주는 소수점 값(quota.percent) 그대로 보여준다 -
  // 반올림해버리면 0.28%처럼 작은 값이 0%로 뭉개져 보인다.
  const percentClamped = Math.min(quota.percent, 100);
  const color = ringColor(quota.percent);
  const dash = (percentClamped / 100) * CIRCUMFERENCE;

  return (
    <div
      aria-label={`토큰 사용량 ${quota.percent}% 사용 중`}
      className="group relative mb-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-500 transition-colors hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800"
    >
      <svg viewBox="0 0 36 36" className="h-6 w-6 -rotate-90">
        <circle
          cx="18"
          cy="18"
          r={RADIUS}
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          className="text-gray-200 dark:text-gray-700"
        />
        <circle
          cx="18"
          cy="18"
          r={RADIUS}
          fill="none"
          stroke={color}
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${CIRCUMFERENCE}`}
        />
      </svg>
      {/* 브라우저 기본 title 툴팁은 뜨기까지 딜레이가 길어서, CSS만으로 즉시
          뜨는 커스텀 툴팁을 직접 그린다. */}
      <span className="pointer-events-none absolute bottom-full left-1/2 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-gray-900 px-2 py-1 text-xs text-white opacity-0 transition-opacity duration-100 group-hover:opacity-100 dark:bg-gray-700">
        {quota.percent}% 사용 중
      </span>
    </div>
  );
}
