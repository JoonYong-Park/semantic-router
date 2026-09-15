"use client";

import { useEffect, useRef } from "react";
import Chart from "chart.js/auto";
import { UsageChartPoint, UsagePeriod } from "@/lib/types";

const INPUT_COLOR = "#5B8DB8"; // 차분한 남색 - 다크 배경에서도 튀지 않도록 채도를 낮춤
const OUTPUT_COLOR = "#6B9E8F"; // 차분한 청록
// 그리드/눈금 색은 라이트/다크 어느 배경에서도 무난하도록 중간 톤 회색으로 고정
const GRID_COLOR = "rgba(148, 163, 184, 0.12)";
const TICK_COLOR = "#9ca3af"; // gray-400

// 라벨이 너무 빽빽하면 오늘(24개)은 3시간 간격, 월간(30개)은 5일 간격만 표시.
// 주간(7개)/연간(12개)은 다 표시해도 안 빽빽해서 그대로 둔다.
function tickStep(period: UsagePeriod): number {
  if (period === "today") return 3;
  if (period === "month") return 5;
  return 1;
}

function formatK(value: number): string {
  return value >= 1000 ? `${Math.round(value / 1000)}k` : `${value}`;
}

export default function UsageChart({
  period,
  data,
}: {
  period: UsagePeriod;
  data: UsageChartPoint[];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    chartRef.current?.destroy();

    const step = tickStep(period);

    chartRef.current = new Chart(canvas, {
      type: "bar",
      data: {
        labels: data.map((d) => d.label),
        datasets: [
          {
            label: "입력 토큰",
            data: data.map((d) => d.input),
            backgroundColor: INPUT_COLOR,
            stack: "tokens",
          },
          {
            label: "출력 토큰",
            data: data.map((d) => d.output),
            backgroundColor: OUTPUT_COLOR,
            stack: "tokens",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: {
              color: TICK_COLOR,
              autoSkip: false,
              maxRotation: 0,
              minRotation: 0,
              callback: (_value, index) => (index % step === 0 ? data[index]?.label ?? "" : ""),
            },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            grid: { color: GRID_COLOR },
            ticks: {
              color: TICK_COLOR,
              maxTicksLimit: 5,
              callback: (value) => formatK(Number(value)),
            },
          },
        },
        plugins: {
          legend: {
            labels: {
              color: TICK_COLOR,
              boxWidth: 10,
              boxHeight: 10,
              borderRadius: 2,
              useBorderRadius: true,
              font: { size: 12 },
            },
          },
          tooltip: {
            callbacks: {
              afterBody: (items) => {
                const idx = items[0]?.dataIndex ?? 0;
                const point = data[idx];
                return point ? `총 ${(point.input + point.output).toLocaleString()} 토큰` : "";
              },
            },
          },
        },
      },
    });

    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, [period, data]);

  return (
    <div className="h-56 w-full">
      <canvas ref={canvasRef} />
    </div>
  );
}
