"use client";

import { useEffect, useState } from "react";
import { getUsageQuota, getUsageStats } from "@/lib/api";
import { formatMonthDay } from "@/lib/dateFormat";
import { UsagePeriod, UsageQuota, UsageStats } from "@/lib/types";
import UsageChart from "./UsageChart";

const PERIOD_TABS: { value: UsagePeriod; label: string }[] = [
  { value: "today", label: "오늘" },
  { value: "week", label: "주간" },
  { value: "month", label: "월간" },
  { value: "year", label: "연간" },
];

// A-1 진행바/링 공통 기준: 0~70% 기본, 70~90% 경고, 90%~ 위험
// 기본색은 그래프의 입력 토큰 색(#5B8DB8)과 통일하고, 경고/위험 단계도 채도를 낮춘 톤을 쓴다.
function usageColors(percent: number): { bar: string; text: string } {
  if (percent >= 90) return { bar: "bg-red-600", text: "text-red-700 dark:text-red-400" };
  if (percent >= 70) return { bar: "bg-amber-600", text: "text-amber-700 dark:text-amber-400" };
  return { bar: "bg-[#5B8DB8]", text: "text-[#5B8DB8]" };
}

export default function UsageStatsModal({ onClose }: { onClose: () => void }) {
  const [quota, setQuota] = useState<UsageQuota | null>(null);
  const [period, setPeriod] = useState<UsagePeriod>("today");
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getUsageQuota()
      .then(setQuota)
      .catch(() => {
        // 조회 실패해도 아래 기간별 통계는 별개로 계속 시도한다
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    getUsageStats(period)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
  }, [period]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const colors = usageColors(quota?.percent ?? 0);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-6 shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">토큰 사용량</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="닫기"
            className="flex h-7 w-7 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200"
          >
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>

        {quota && (
          <div className="mb-6 rounded-xl bg-gray-50 p-4 dark:bg-gray-800/60">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-medium text-gray-700 dark:text-gray-300">남은 토큰</span>
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {formatMonthDay(quota.cycle_start)} ~ {formatMonthDay(quota.cycle_end)} · 갱신까지{" "}
                {quota.days_left}일
              </span>
            </div>
            <p className="mb-2 text-[28px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
              {quota.remaining.toLocaleString()}
              <span className="ml-1 text-[13px] font-normal text-gray-400 dark:text-gray-500">
                / {quota.limit.toLocaleString()} 토큰
              </span>
            </p>
            <div className="mb-1 h-2 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
              <div
                className={`h-full rounded-full ${colors.bar}`}
                style={{ width: `${Math.min(quota.percent, 100)}%` }}
              />
            </div>
            <p className={`text-xs ${colors.text}`}>
              {quota.used.toLocaleString()} 사용 · {quota.percent}%
            </p>
            {quota.percent > 90 && (
              <p className="mt-2 text-xs text-red-700 dark:text-red-400">
                이번 결제 주기 토큰 사용량이 90%를 초과했습니다.
              </p>
            )}
          </div>
        )}

        <div className="mb-4 flex gap-1">
          {PERIOD_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              onClick={() => setPeriod(tab.value)}
              className={`rounded-full px-3.5 py-1.5 text-sm transition-colors ${
                period === tab.value
                  ? "bg-gray-100 font-medium text-gray-900 dark:bg-gray-800 dark:text-white"
                  : "text-gray-500 hover:bg-gray-50 dark:text-gray-400 dark:hover:bg-gray-800/60"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {stats && (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
                <p className="text-xs text-gray-400 dark:text-gray-500">입력 토큰</p>
                <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                  {stats.summary.input_tokens.toLocaleString()}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
                <p className="text-xs text-gray-400 dark:text-gray-500">출력 토큰</p>
                <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                  {stats.summary.output_tokens.toLocaleString()}
                </p>
              </div>
              <div className="rounded-lg bg-gray-50 px-3.5 py-2.5 dark:bg-gray-800/60">
                <p className="text-xs text-gray-400 dark:text-gray-500">총 토큰</p>
                <p className="text-[20px] font-semibold leading-tight text-gray-900 dark:text-gray-100">
                  {stats.summary.total_tokens.toLocaleString()}
                </p>
              </div>
            </div>

            <UsageChart period={period} data={stats.chart} />

            {stats.by_model.length === 0 ? (
              <p className="py-8 text-center text-[13px] text-gray-400 dark:text-gray-500">
                아직 사용 기록이 없습니다
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-gray-200 text-xs text-gray-400 dark:border-gray-700 dark:text-gray-500">
                      <th className="py-2 font-medium">모델</th>
                      <th className="py-2 font-medium">호출 횟수</th>
                      <th className="py-2 font-medium">입력</th>
                      <th className="py-2 font-medium">출력</th>
                      <th className="py-2 font-medium">합계</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.by_model.map((m) => (
                      <tr
                        key={m.model}
                        className="border-b border-gray-100 text-gray-700 dark:border-gray-800 dark:text-gray-300"
                      >
                        <td className="py-2">{m.model}</td>
                        <td className="py-2">{m.calls}</td>
                        <td className="py-2">{m.input.toLocaleString()}</td>
                        <td className="py-2">{m.output.toLocaleString()}</td>
                        <td className="py-2 font-medium">{m.total.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {loading && !stats && (
          <p className="py-8 text-center text-sm text-gray-400 dark:text-gray-500">불러오는 중...</p>
        )}
      </div>
    </div>
  );
}
