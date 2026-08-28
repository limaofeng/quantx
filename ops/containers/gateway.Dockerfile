# syntax=docker/dockerfile:1.7
FROM node:20.20.2-bookworm-slim AS builder
WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/web/package.json ./apps/web/package.json
COPY apps/docs/package.json ./apps/docs/package.json
RUN --mount=type=cache,target=/root/.npm npm ci

COPY apps/web ./apps/web
COPY apps/docs ./apps/docs
ARG QUANTX_DOCS_VERSION=development
ARG VITE_APP_ENV=production
ENV QUANTX_DOCS_VERSION=${QUANTX_DOCS_VERSION} \
    QUANTX_DOCS_DISABLE_GIT=true \
    VITE_APP_ENV=${VITE_APP_ENV}
RUN npm run build

FROM caddy:2.11.4-alpine AS runtime
COPY ops/caddy/Caddyfile.k8s /etc/caddy/Caddyfile
COPY --from=builder /workspace/apps/web/dist /srv/web
COPY --from=builder /workspace/apps/docs/dist /srv/docs
RUN chown -R 10001:10001 /config /data /srv
USER 10001:10001
