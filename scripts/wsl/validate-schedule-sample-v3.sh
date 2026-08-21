#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_file="$repo_root/.env.wsl.local"
sample_path="${1:-$repo_root/docs/examples/PKUBA_2026北大杯_赛程导入示例_v3.xlsx}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing $env_file. Run scripts/deploy-wsl.ps1 first." >&2
  exit 1
fi
if [[ ! -f "$sample_path" ]]; then
  echo "Missing sample workbook: $sample_path" >&2
  exit 1
fi
if ! docker network inspect pkuba-wsl_default >/dev/null 2>&1; then
  echo "The WSL deployment network is unavailable. Run scripts/deploy-wsl.ps1 first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

docker run --rm \
  --network pkuba-wsl_default \
  -v "$repo_root/apps/api:/app" \
  -v "$sample_path:/workspace/sample.xlsx:ro" \
  -e "DATABASE_URL=postgresql://pkuba:${PKUBA_DB_PASSWORD}@db:5432/pkuba" \
  -e "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}" \
  pkuba-dev-api:latest \
  python manage.py sample_2026_schedule_v3 --validate /workspace/sample.xlsx
