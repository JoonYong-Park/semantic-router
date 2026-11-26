/// <reference types="vite/client" />

// 빌드할 때 넣는 설정 (Dockerfile ARG, scripts/deploy_frontend.sh)
interface ImportMetaEnv {
  /** API 주소. 배포는 같은 도메인의 /api, 로컬은 http://localhost:8000 */
  readonly VITE_API_URL?: string;
  /** 채팅 기본 모드 (auto, openai, google, anthropic). 비우면 auto */
  readonly VITE_DEFAULT_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
