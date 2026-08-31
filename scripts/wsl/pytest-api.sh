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

exec docker run --rm \
  --network pkuba-wsl_default \
  -v "$repo_root/apps/api:/app" \
  -e "DATABASE_URL=postgresql://pkuba:${PKUBA_DB_PASSWORD}@db:5432/pkuba" \
  -e "DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY}" \
  -e "QWEN_API_KEY=${QWEN_API_KEY:-}" \
  -e "QWEN_BASE_URL=${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}" \
  -e "QWEN_MODEL=${QWEN_MODEL:-qwen3.8-max}" \
  -e "QWEN_REASONING_EFFORT=${QWEN_REASONING_EFFORT:-xhigh}" \
  -e "SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS=${SCORESHEET_RECOGNITION_UPSCALE_TARGET_PIXELS:-8000000}" \
  -e "SCORESHEET_RECOGNITION_TIMEOUT_SECONDS=${SCORESHEET_RECOGNITION_TIMEOUT_SECONDS:-180}" \
  pkuba-dev-api:latest \
  pytest "$@"
