#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_file="$repo_root/.env.wsl.local"

[[ ${PKUBA_CONFIRM_LOCAL_INITIALIZATION:-} == INITIALIZE_LOCAL_DATA ]] || {
  echo "Refusing to mutate local business data without explicit confirmation." >&2
  exit 64
}
[[ -f $env_file ]] || {
  echo "Missing $env_file. Run scripts/deploy-wsl.ps1 first." >&2
  exit 1
}

mode=${1:-}
legacy_source=${2:-}
[[ $mode == demo || $mode == legacy-2026 ]] || {
  echo "Usage: initialize-local.sh {demo|legacy-2026} [legacy-source]" >&2
  exit 64
}

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

"${compose[@]}" exec -T api python manage.py migrate --noinput

if [[ $mode == demo ]]; then
  season_count=$("${compose[@]}" exec -T api python manage.py shell -c \
    'from core.models import Season; print(Season.objects.count())' \
    | tr -d '\r' | tail -n 1)
  [[ $season_count == 0 ]] || {
    echo "Demo initialization requires an empty season table; found $season_count." >&2
    exit 1
  }
  "${compose[@]}" exec -T api python manage.py seed_demo
  exit 0
fi

[[ -d $legacy_source ]] || {
  echo "Legacy source is missing or not a directory: $legacy_source" >&2
  exit 1
}
"${compose[@]}" run --rm \
  -v "$legacy_source:/legacy:ro" \
  api python manage.py import_legacy_2026 --source /legacy --dry-run
"${compose[@]}" run --rm \
  -v "$legacy_source:/legacy:ro" \
  api python manage.py import_legacy_2026 --source /legacy
