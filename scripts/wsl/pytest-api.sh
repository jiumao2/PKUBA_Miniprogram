#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_file="$repo_root/.env.wsl.local"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Run scripts/deploy-wsl.ps1 first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

extra_mounts=()
if [[ -n "${PKUBA_ROSTER_REFERENCE_XLSX:-}" ]]; then
  if [[ ! -f "$PKUBA_ROSTER_REFERENCE_XLSX" ]]; then
    echo "Roster reference workbook not found: $PKUBA_ROSTER_REFERENCE_XLSX" >&2
    exit 1
  fi
  extra_mounts+=(
    -v "$PKUBA_ROSTER_REFERENCE_XLSX:/reference/roster.xlsx:ro"
    -e "PKUBA_ROSTER_REFERENCE_XLSX=/reference/roster.xlsx"
  )
fi

exec docker run --rm \
  --network pkuba-wsl_default \
  -v "$repo_root/apps/api:/app" \
  -e "DATABASE_URL=postgresql://pkuba:${PKUBA_DB_PASSWORD}@db:5432/pkuba" \
  -e "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}" \
  "${extra_mounts[@]}" \
  pkuba-dev-api:latest \
  pytest "$@"
