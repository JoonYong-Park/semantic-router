FROM node:20-slim AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# 채팅 기본 모드 (비우면 auto). auto 라우팅을 끈 배포에서는 openai 등 수동 모드로.
ARG NEXT_PUBLIC_DEFAULT_MODE=
ENV NEXT_PUBLIC_DEFAULT_MODE=$NEXT_PUBLIC_DEFAULT_MODE
RUN npm run build

# AWS Lambda용: 같은 Next.js 서버를 Lambda Web Adapter가 HTTP 요청으로 연결해 준다.
#   docker build --target lambda --build-arg NEXT_PUBLIC_API_URL=https://... frontend
FROM node:20-slim AS lambda
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.8.4 /lambda-adapter /opt/extensions/lambda-adapter
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 AWS_LWA_PORT=3000
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
# Lambda는 root가 아닌 사용자로 실행한다. 로컬 체크아웃(예: Google Drive 동기화 폴더)은 파일이 600 권한일 수 있어
# 읽기 권한을 연다. 이게 없으면 Next.js가 public/ 을 읽지 못해(EACCES) 시작하자마자 종료된다.
RUN chmod -R a+rX /app
CMD ["node", "server.js"]

# 기본 (로컬)
FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
