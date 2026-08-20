#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_file="$repo_root/.env.wsl.local"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Run scripts/deploy-wsl.ps1 from Windows first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

compose=(
  docker compose
  --project-name pkuba-wsl
  --project-directory "$repo_root"
  --env-file "$env_file"
  -f "$repo_root/infra/compose.wsl.yml"
)

for base_image in python:3.13-slim node:24-alpine caddy:2.10-alpine; do
  if ! docker image inspect "$base_image" >/dev/null 2>&1; then
    docker pull "$base_image"
  fi
done

"${compose[@]}" up -d --build
"${compose[@]}" exec -T api python manage.py migrate --noinput

if [[ -d "${PKUBA_LEGACY_SOURCE:-}" ]]; then
  "${compose[@]}" run --rm \
    -v "$PKUBA_LEGACY_SOURCE:/legacy:ro" \
    api python manage.py import_legacy_2026 --source /legacy
else
  "${compose[@]}" exec -T api python manage.py seed_demo --if-empty
fi

"${compose[@]}" exec -T \
  -e PKUBA_BOOTSTRAP_ADMIN_PASSWORD="$PKUBA_LOCAL_ADMIN_PASSWORD" \
  api python manage.py create_local_admin \
  "$PKUBA_LOCAL_ADMIN_USERNAME" \
  --password-env PKUBA_BOOTSTRAP_ADMIN_PASSWORD

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error \
    "http://localhost:${PKUBA_WEB_PORT}/api/v1/health" >/dev/null; then
    exit 0
  fi
  sleep 1
done

echo "PKUBA WSL web service did not become ready in 60 seconds." >&2
exit 1
