#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../.." && pwd)
env_file=$repo_root/.env.production.example
state_dir=$(mktemp -d)
trap 'rm -rf -- "$state_dir"' EXIT

cp "$repo_root/infra/upstreams.example.caddy" "$state_dir/upstreams.caddy"

common_env=(
  PKUBA_ENV_FILE="$env_file"
  PKUBA_RUNTIME_NETWORK=pkuba-production-validation
  PKUBA_POSTGRES_VOLUME=pkuba-validation-postgres
  PKUBA_MEDIA_VOLUME=pkuba-validation-media
  PKUBA_ARCHIVE_VOLUME=pkuba-validation-archives
  PKUBA_DEPLOY_STATE_DIR="$state_dir"
)

env "${common_env[@]}" docker compose \
  --project-name pkuba-data-validation \
  --project-directory "$repo_root" \
  --env-file "$env_file" \
  -f "$repo_root/infra/compose.prod.data.yml" \
  config --quiet

env "${common_env[@]}" docker compose \
  --project-name pkuba-gateway-validation \
  --project-directory "$repo_root" \
  --env-file "$env_file" \
  -f "$repo_root/infra/compose.prod.gateway.yml" \
  config --quiet

for slot in blue green; do
  if [[ $slot == blue ]]; then
    api_port=18000
    web_port=18080
  else
    api_port=18001
    web_port=18081
  fi
  env \
    "${common_env[@]}" \
    PKUBA_SLOT_NAME="pkuba-$slot" \
    PKUBA_SLOT_API_PORT="$api_port" \
    PKUBA_SLOT_WEB_PORT="$web_port" \
    docker compose \
      --project-name "pkuba-$slot-validation" \
      --project-directory "$repo_root" \
      --env-file "$env_file" \
      -f "$repo_root/infra/compose.prod.slot.yml" \
      config --quiet
done

docker run --rm \
  -e PKUBA_DOMAIN=example.com \
  -e ACME_EMAIL=admin@example.com \
  -v "$repo_root/infra/Caddyfile.gateway:/etc/caddy/Caddyfile:ro" \
  -v "$state_dir:/srv/deployment-state:ro" \
  caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile

docker run --rm \
  -e PKUBA_SLOT_API_UPSTREAM=pkuba-blue-api:8000 \
  -e PKUBA_RELEASE_TAG=v0.0.0 \
  -e PKUBA_GIT_COMMIT=0000000000000000000000000000000000000000 \
  -v "$repo_root/infra/Caddyfile.slot:/etc/caddy/Caddyfile:ro" \
  caddy:2.10-alpine caddy validate --config /etc/caddy/Caddyfile

bash -n "$repo_root"/scripts/prod/*.sh

contract=$(sed -n 's/^PKUBA_PREVIOUS_APP_COMPATIBLE=//p' \
  "$repo_root/infra/release-contract.env")
[[ $contract == 0 ]]

grep -Fq 'rollback_type=application_only' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'database_restored=0' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'previous-release.env MANIFEST.env' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'audit_season_integrity --json' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'PKUBA_OLD_SLOT_RETENTION_SECONDS:-86400' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'PKUBA_PRODUCTION_AUTOMATION_ARMED=0' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'ACTIVE_SLOT=uninitialized' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'PRODUCTION_DEPLOYMENTS_ENABLED' \
  "$repo_root/.github/workflows/release.yml"
grep -Fq 'RESTORE_PAIRED_DATA' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'archive-staging.files.sha256' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'pkuba-parse-release-state' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
! grep -Fq 'source "$backup_dir/previous-release.env"' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'audit_season_integrity --json' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
parser_line=$(grep -n 'parsed_state=$(bash "$state_parser"' \
  "$repo_root/scripts/prod/restore-paired-data.sh" | head -n 1 | cut -d: -f1)
stop_line=$(grep -n 'Stopping both application slots before touching paired data' \
  "$repo_root/scripts/prod/restore-paired-data.sh" | head -n 1 | cut -d: -f1)
restore_line=$(grep -n 'Restoring PostgreSQL from the verified deployment snapshot' \
  "$repo_root/scripts/prod/restore-paired-data.sh" | head -n 1 | cut -d: -f1)
[[ -n $parser_line && -n $stop_line && -n $restore_line \
  && $parser_line -lt $stop_line && $parser_line -lt $restore_line ]] \
  || { echo "release state must be parsed before services or paired data are touched" >&2; exit 1; }
grep -Fq 'MANIFEST.env' \
  "$repo_root/scripts/prod/backup-current-server.sh"
grep -Fq 'database_restored=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'media_restored=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'archive_restored=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"

state_fixture=$state_dir/release-state-fixture
mkdir -p "$state_fixture"
cat >"$state_fixture/previous-release.env" <<'EOF'
ACTIVE_SLOT=blue
CURRENT_TAG=v1.2.3
CURRENT_COMMIT=0123456789abcdef0123456789abcdef01234567
CURRENT_API_IMAGE=ghcr.io/jiumao2/pkuba-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CURRENT_WEB_IMAGE=ghcr.io/jiumao2/pkuba-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CURRENT_RELEASE_DIR=/opt/pkuba/deploy/releases/v1.2.3
SWITCHED_AT=2026-08-25T00:00:00Z
EOF
bash "$repo_root/scripts/prod/parse-release-state.sh" \
  "$state_fixture/previous-release.env" >/dev/null
(
  cd "$state_fixture"
  sha256sum previous-release.env >SHA256SUMS
)
injection_marker=$state_fixture/injection-marker
printf 'UNEXPECTED=$(touch %s)\n' "$injection_marker" \
  >>"$state_fixture/previous-release.env"
if (cd "$state_fixture" && sha256sum --check SHA256SUMS >/dev/null 2>&1); then
  echo "tampered previous-release.env unexpectedly passed its checksum" >&2
  exit 1
fi
# A rewritten checksum manifest must not turn semantically hostile state into a
# valid restore input. Fixed-key parsing still fails before any data operation.
(
  cd "$state_fixture"
  sha256sum previous-release.env >SHA256SUMS
  sha256sum --check SHA256SUMS >/dev/null
)
if bash "$repo_root/scripts/prod/parse-release-state.sh" \
  "$state_fixture/previous-release.env" >/dev/null 2>&1; then
  echo "tampered previous-release.env unexpectedly passed fixed-key parsing" >&2
  exit 1
fi
[[ ! -e $injection_marker ]]

echo "Blue/green deployment files are structurally valid."
