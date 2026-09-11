import { Mode, Size } from "./types";

export interface ModelOption {
  mode: Mode;
  size: Size;
  label: string;
  group: string;
}

// backend/app/models_config.py의 MODEL_CATALOG와 반드시 같은 순서/이름으로 유지
export const MODEL_OPTIONS: ModelOption[] = [
  { mode: "openai", size: "large", label: "GPT-5.6 Sol", group: "OpenAI" },
  { mode: "openai", size: "medium", label: "GPT-5.6 Terra", group: "OpenAI" },
  { mode: "openai", size: "small", label: "GPT-5.6 Luna", group: "OpenAI" },
  { mode: "google", size: "large", label: "Gemini 3.1 Pro", group: "Google" },
  { mode: "google", size: "medium", label: "Gemini 3.6 Flash", group: "Google" },
  { mode: "google", size: "small", label: "Gemini 3.5 Flash Lite", group: "Google" },
  { mode: "anthropic", size: "large", label: "Claude Opus 5", group: "Anthropic" },
  { mode: "anthropic", size: "medium", label: "Claude Sonnet 5", group: "Anthropic" },
  {
    mode: "anthropic",
    size: "small",
    label: "Claude Haiku 4.5",
    group: "Anthropic",
  },
];

export interface CompanyIcon {
  src: string;
  alt: string;
  invertInDark: boolean;
}

export const COMPANY_ICON: Record<Exclude<Mode, "auto">, CompanyIcon> = {
  openai: { src: "/icons/openai.png", alt: "OpenAI", invertInDark: true },
  google: { src: "/icons/google.webp", alt: "Google", invertInDark: false },
  anthropic: {
    src: "/icons/anthropic.webp",
    alt: "Anthropic",
    invertInDark: true,
  },
};

const LABEL_PREFIX_TO_COMPANY: [string, Exclude<Mode, "auto">][] = [
  ["GPT", "openai"],
  ["Gemini", "google"],
  ["Claude", "anthropic"],
];

export function companyFromLabel(label: string): Exclude<Mode, "auto"> | null {
  return (
    LABEL_PREFIX_TO_COMPANY.find(([prefix]) => label.startsWith(prefix))?.[1] ??
    null
  );
}

export function labelFor(mode: Mode, size: Size): string {
  return (
    MODEL_OPTIONS.find((m) => m.mode === mode && m.size === size)?.label ??
    `${mode}/${size}`
  );
}

export const SIZE_LABEL: Record<Size, string> = {
  small: "Small",
  medium: "Medium",
  large: "Large",
};

// ModelBadge처럼 model_used(display_name)만 문자열로 받는 곳에서, 그 모델이
// 몇 사이즈짜리인지 역으로 찾을 때 사용. complexity_score 임계값을 프론트에
// 다시 두지 않고, 이미 유지 중인 MODEL_OPTIONS 카탈로그에서 그대로 찾는다.
export function sizeFromLabel(label: string): Size | null {
  return MODEL_OPTIONS.find((m) => m.label === label)?.size ?? null;
}
