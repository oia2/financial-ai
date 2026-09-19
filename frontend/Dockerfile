# Frontend-контейнер закрывает две роли архитектурной диаграммы: Frontend и Nginx.
# Node собирает статику, nginx её отдаёт и проксирует /api на backend-api.
FROM node:22-alpine AS builder

WORKDIR /app

RUN corepack enable

# pnpm-workspace.yaml несёт onlyBuiltDependencies: без него pnpm считает
# пропущенные build-скрипты (esbuild, msw) ошибкой сборки.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY tsconfig.json vite.config.ts index.html ./
# public/ содержит статические файлы, которые Vite копирует в dist как есть
# (иконка вкладки). Без него сборка внутри образа их теряет.
COPY public ./public
COPY src ./src
RUN pnpm build


FROM nginx:1.27-alpine AS runtime

# Конфиг монтируется из deployments/docker-compose/nginx/nginx.conf,
# поэтому правится без пересборки образа. Копия внутри образа — запасной вариант
# для запуска без compose.
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# Адрес именно 127.0.0.1, а не localhost: в образе `localhost` разрешается
# сначала в ::1, а nginx слушает только IPv4 — проверка получала «connection
# refused», и контейнер числился нездоровым всё время работы, хотя страницу
# отдавал. Проверено на стенде 2026-09-19.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
