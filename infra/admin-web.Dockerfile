ARG CADDY_BASE_IMAGE=caddy:2.10-alpine

FROM node:24-alpine AS build

WORKDIR /workspace

COPY package.json package-lock.json ./
COPY apps/admin-web/package.json ./apps/admin-web/package.json
COPY apps/miniapp/package.json ./apps/miniapp/package.json
COPY packages/api-client/package.json ./packages/api-client/package.json
COPY packages/design-tokens/package.json ./packages/design-tokens/package.json
COPY packages/scoresheet-domain/package.json ./packages/scoresheet-domain/package.json

RUN npm ci --no-audit \
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

FROM python:3.14-slim AS django-static

WORKDIR /app
COPY apps/api/pyproject.toml apps/api/requirements.lock ./
COPY apps/api/config ./config
COPY apps/api/core/*.py ./core/
COPY apps/api/core/assets ./core/assets
COPY apps/api/core/migrations ./core/migrations
COPY apps/api/core/scoresheet_v2 ./core/scoresheet_v2
COPY apps/api/core/services ./core/services
COPY apps/api/manage.py ./
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps -e . \
    && test ! -e /app/core/tests \
    && test ! -e /app/core/management \
    && python manage.py collectstatic --noinput

FROM ${CADDY_BASE_IMAGE}

COPY --from=build /workspace/apps/admin-web/dist /srv/admin-web
COPY --from=django-static /app/staticfiles /srv/django-static
