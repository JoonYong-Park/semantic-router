import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// React SPA 빌드 설정. `npm run build` 결과(dist/)를 S3에 올리고 CloudFront가 서빙한다.
// dist/assets/ 의 파일 이름에는 내용 해시가 붙어 오래 캐시해도 안전하고, index.html만 매번 새로 받는다.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { host: "0.0.0.0", port: 3000 },
  preview: { host: "0.0.0.0", port: 3000 },
  build: { outDir: "dist", sourcemap: false },
});
