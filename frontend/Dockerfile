# Joomini 프론트 (React SPA, Vite). 결과물은 서버가 필요 없는 정적 파일(dist/)이다.
# 단계(target)
#   dist         : 빌드 결과만 꺼낸다. 배포 스크립트가 이걸 S3에 올린다.
#                  docker build --target dist --output type=local,dest=out frontend
#   runner (기본): 로컬 개발용. nginx가 dist를 서빙하고, 파일이 아닌 경로는 index.html로 돌려준다
#                  (배포에서 CloudFront Function이 하는 일과 같다).

FROM node:20-slim AS deps
WORKDIR /app
COPY package.json ./
RUN npm install

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# 빌드할 때 코드에 들어가는 설정. 배포는 같은 도메인의 /api 를 쓴다.
ARG VITE_API_URL=http://localhost:8000
ARG VITE_DEFAULT_MODE=
ENV VITE_API_URL=$VITE_API_URL VITE_DEFAULT_MODE=$VITE_DEFAULT_MODE
# 타입 검사(tsc) 후 빌드
RUN npm run build

FROM scratch AS dist
COPY --from=builder /app/dist /

FROM nginx:1.27-alpine AS runner
COPY --from=builder /app/dist /usr/share/nginx/html
RUN printf '%s\n' \
  'server {' \
  '  listen 3000;' \
  '  root /usr/share/nginx/html;' \
  '  location / { try_files $uri $uri/ /index.html; }' \
  '}' > /etc/nginx/conf.d/default.conf \
  && chmod -R a+rX /usr/share/nginx/html
EXPOSE 3000
