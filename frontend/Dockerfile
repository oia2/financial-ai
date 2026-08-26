# Frontend-контейнер закрывает две роли архитектурной диаграммы: Frontend и Nginx.
# Node собирает статику, nginx её отдаёт и проксирует /api на backend-api.
FROM node:22-alpine AS builder

WORKDIR /app

RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN pnpm build


FROM nginx:1.27-alpine AS runtime

# Конфиг монтируется из deployments/docker-compose/nginx/nginx.conf,
# поэтому правится без пересборки образа. Копия внутри образа — запасной вариант
# для запуска без compose.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
