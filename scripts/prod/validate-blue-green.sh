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
grep -Fq 'PKUBA_OLD_SLOT_RETENTION_SECONDS:-86400' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'PKUBA_PRODUCTION_AUTOMATION_ARMED=0' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'ACTIVE_SLOT=uninitialized' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'PRODUCTION_DEPLOYMENTS_ENABLED' \
  "$repo_root/.github/workflows/release.yml"

echo "Blue/green deployment files are structurally valid."
