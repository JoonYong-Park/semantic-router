import type { Mode } from "./types";

// 채팅 입력창의 기본 모드. auto 라우팅 모델을 끈 배포(예: 첫 Lambda 배포)에서는
// 빌드할 때 NEXT_PUBLIC_DEFAULT_MODE=openai 처럼 수동 모드를 기본값으로 둔다.
const MODES: Mode[] = ["auto", "openai", "google", "anthropic"];
const fromEnv = process.env.NEXT_PUBLIC_DEFAULT_MODE as Mode | undefined;
export const DEFAULT_MODE: Mode = fromEnv && MODES.includes(fromEnv) ? fromEnv : "auto";
