FROM node:24-alpine AS build

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/admin-web/package.json ./apps/admin-web/package.json
COPY apps/miniapp/package.json ./apps/miniapp/package.json
COPY packages/api-client/package.json ./packages/api-client/package.json
COPY packages/design-tokens/package.json ./packages/design-tokens/package.json
COPY packages/scoresheet-domain/package.json ./packages/scoresheet-domain/package.json

RUN npm ci \
    --workspace @pkuba/admin-web \
    --workspace @pkuba/api-client \
    --workspace @pkuba/design-tokens \
    --workspace @pkuba/scoresheet-domain \
    --include-workspace-root

COPY apps/admin-web ./apps/admin-web
COPY packages/api-client ./packages/api-client
COPY packages/design-tokens ./packages/design-tokens
COPY packages/scoresheet-domain ./packages/scoresheet-domain

RUN npm --workspace @pkuba/design-tokens run build \
    && npm --workspace @pkuba/scoresheet-domain run build \
    && npm --workspace @pkuba/api-client run build \
    && npm --workspace @pkuba/admin-web run build

FROM caddy:2.10-alpine

COPY --from=build /workspace/apps/admin-web/dist /srv/admin-web
