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
  PKUBA_POSTGRES_IMAGE=ghcr.io/jiumao2/pkuba-postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
  PKUBA_CADDY_IMAGE=ghcr.io/jiumao2/pkuba-caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
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
  caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d \
  caddy validate --config /etc/caddy/Caddyfile

docker run --rm \
  -e PKUBA_SLOT_API_UPSTREAM=pkuba-blue-api:8000 \
  -e PKUBA_RELEASE_TAG=v1.0.0 \
  -e PKUBA_GIT_COMMIT=0000000000000000000000000000000000000000 \
  -v "$repo_root/infra/Caddyfile.slot:/etc/caddy/Caddyfile:ro" \
  caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d \
  caddy validate --config /etc/caddy/Caddyfile

[[ $(grep -Fc 'header_up X-Forwarded-Proto https' "$repo_root/infra/Caddyfile.slot") -eq 2 ]]
bash "$repo_root/scripts/prod/test-slot-forwarded-proto.sh"

bash -n "$repo_root"/scripts/prod/*.sh
bash "$repo_root/scripts/prod/test-deploy-ssh-gate.sh"
bash "$repo_root/scripts/prod/test-release-safety.sh"

grep -Fq '/usr/local/sbin/pkuba-sync-release-tools deploy' \
  "$repo_root/scripts/prod/deploy-gateway.sh"
! grep -Fq '/usr/local/sbin/pkuba-deploy-blue-green' \
  "$repo_root/scripts/prod/deploy-gateway.sh"
grep -Fq 'sync-release-tools.sh" \' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'activate-source "$release_commit" "$release_dir"' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'toolset_release_root=$toolset_root/releases' \
  "$repo_root/scripts/prod/sync-release-tools.sh"
grep -Fq 'PKUBA_DEPLOY_LOCK_HELD=1' \
  "$repo_root/scripts/prod/sync-release-tools.sh"
grep -Fq 'mv -Tf "$link_tmp" "$toolset_current"' \
  "$repo_root/scripts/prod/sync-release-tools.sh"
for installed_tool in \
  pkuba-deploy-gateway pkuba-record-deploy-ssh-verification \
  pkuba-finalize-deploy-ssh pkuba-deploy-blue-green \
  pkuba-rollback-retained-application pkuba-recover-release-transaction \
  pkuba-restore-paired-data pkuba-start-current-application \
  pkuba-backup-current acquire-deploy-lock.py fence-deploy-writers.sh \
  verify-paired-backup.py parse-release-state.sh parse-release-contract.sh \
  derive-release-capability.sh validate-release-identity.sh \
  check-app-capability.sh pkuba-sync-release-tools; do
  grep -Fq "$installed_tool" "$repo_root/scripts/prod/sync-release-tools.sh"
done
[[ $(grep -Fc "|sbin/" "$repo_root/scripts/prod/sync-release-tools.sh") -eq 10 ]]
[[ $(grep -Fc "|libexec/" "$repo_root/scripts/prod/sync-release-tools.sh") -eq 8 ]]
[[ $(grep -Fc 'ExecStart=/usr/local/libexec/pkuba/toolsets/current/sbin/' \
  "$repo_root/scripts/prod/bootstrap-server.sh") -eq 3 ]]
! grep -Eq '^ExecStart=/usr/local/sbin/pkuba-(recover-release-transaction|start-current-application|backup-current)' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'pkuba-sync-release-tools verify *' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'pkuba-sync-release-tools deploy *' \
  "$repo_root/scripts/prod/bootstrap-server.sh"

! grep -Fq 'COPY core/management ./core/management' \
  "$repo_root/apps/api/Dockerfile"
grep -Fq 'test ! -e /app/core/management/commands/sample_2026_schedule_v3.py' \
  "$repo_root/apps/api/Dockerfile"
! grep -Fq 'COPY apps/api/core/management' \
  "$repo_root/infra/admin-web.Dockerfile"
grep -Fq 'test ! -e /app/core/management' \
  "$repo_root/infra/admin-web.Dockerfile"

contract=$(sed -n 's/^PKUBA_PREVIOUS_APP_COMPATIBLE=//p' \
  "$repo_root/infra/release-contract.env")
[[ $contract == 1 ]]
grep -Fqx 'PKUBA_APP_CAPABILITY=reschedule-route-v1' \
  "$repo_root/infra/release-contract.env"
grep -Fqx 'PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1' \
  "$repo_root/infra/release-contract.env"

grep -Fq 'PHASE=PREPARED' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'write_journal_phase NEW_COMMITTED' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'RECOVERY_REQUIRED_' \
  "$repo_root/scripts/prod/recover-release-transaction.sh"
grep -Fq 'database_restored=0' \
  "$repo_root/scripts/prod/recover-release-transaction.sh"
grep -Fq 'archive_restored=0' \
  "$repo_root/scripts/prod/recover-release-transaction.sh"
grep -Fq 'previous-release.env MANIFEST.env' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'audit_season_integrity --json' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'PKUBA_OLD_SLOT_RETENTION_SECONDS:-86400' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'PKUBA_PRODUCTION_AUTOMATION_ARMED=0' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fxq '[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "release tag must be a stable vMAJOR.MINOR.PATCH"' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
stable_release_tag_regex='^v[0-9]+\.[0-9]+\.[0-9]+$'
for release_tag in v1.0.2; do
  [[ $release_tag =~ $stable_release_tag_regex ]] || {
    echo "stable release tag was unexpectedly rejected: $release_tag" >&2
    exit 1
  }
done
for release_tag in v1.0.2-rc.1 v1.0.2+build main v1.0; do
  if [[ $release_tag =~ $stable_release_tag_regex ]]; then
    echo "non-stable release tag was unexpectedly accepted: $release_tag" >&2
    exit 1
  fi
done
grep -Fq 'bootstrap_first_superadmin' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'bootstrap_admin_registration_policy' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'Usage: sudo /usr/bin/bash /root/pkuba-prod-tools/bootstrap-server.sh' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'sudo /usr/bin/bash /root/pkuba-prod-tools/bootstrap-server.sh' \
  "$repo_root/docs/DEPLOYMENT.md"
grep -Fq 'pkuba-backup-daily.timer' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'pkuba-backup-weekly.timer' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'available_bytes >= 16106127360' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'hard_floor_bytes=${PKUBA_DEPLOY_HARD_FLOOR_BYTES:-10737418240}' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'startup_headroom_bytes=${PKUBA_DEPLOY_STARTUP_HEADROOM_BYTES:-16106127360}' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'derive-release-capability.sh' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'pkuba-release-recovery.service' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'PRODUCTION_DEPLOYMENTS_ENABLED' \
  "$repo_root/.github/workflows/release.yml"
grep -Fq 'RESTORE_PAIRED_DATA' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'archive-staging.files.sha256' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'validate-release-identity.sh' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
! grep -Fq 'source "$backup_dir/previous-release.env"' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
! grep -Fq 'source "$current_state"' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
! grep -Fq 'source "$candidate_state"' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'audit_season_integrity --json' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'verify_backup "$backup_argument"' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'verify_backup "$backup_dir"' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'fence_all_writers || die' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'verify-paired-backup.py' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'pkuba-start-current-application' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
! grep -Fq 'PasswordAuthentication no' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
! grep -Fq 'systemctl reload ssh' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'chown root:root "/home/$deploy_user/.ssh/authorized_keys"' \
  "$repo_root/scripts/prod/bootstrap-server.sh"
grep -Fq 'PKUBA_DEPLOY_GATEWAY_VERIFIED=' \
  "$repo_root/scripts/prod/record-deploy-ssh-verification.sh"
grep -Fq 'verification proof must originate from a root sshd session' \
  "$repo_root/scripts/prod/record-deploy-ssh-verification.sh"
grep -Fq 'AUTHORIZED_KEYS_SHA256=' \
  "$repo_root/scripts/prod/record-deploy-ssh-verification.sh"
grep -Fq 'tty_options+=( -tt )' \
  "$repo_root/scripts/prod/verify-deploy-ssh.sh"
grep -Fq 'ExitOnForwardFailure=yes' \
  "$repo_root/scripts/prod/verify-deploy-ssh.sh"
grep -Fq 'GlobalKnownHostsFile=/dev/null' \
  "$repo_root/scripts/prod/verify-deploy-ssh.sh"
grep -Fq -- '--confirm-console-recovery' \
  "$repo_root/scripts/prod/verify-deploy-ssh.sh"
grep -Fq -- '--root-key-fingerprint' \
  "$repo_root/scripts/prod/verify-deploy-ssh.sh"
grep -Fq 'PasswordAuthentication no' \
  "$repo_root/scripts/prod/finalize-deploy-ssh.sh"
grep -Fq 'PubkeyAuthentication yes' \
  "$repo_root/scripts/prod/finalize-deploy-ssh.sh"
grep -Fq 'ROOT_KEY_FINGERPRINT=' \
  "$repo_root/scripts/prod/finalize-deploy-ssh.sh"
grep -Fq 'The prior sshd configuration was restored' \
  "$repo_root/scripts/prod/finalize-deploy-ssh.sh"
[[ $(grep -c 'restart: "no"' "$repo_root/infra/compose.prod.slot.yml") -ge 5 ]]
grep -Fq 'MANIFEST.env' \
  "$repo_root/scripts/prod/backup-current-server.sh"
grep -Fq 'keep_count=5' \
  "$repo_root/scripts/prod/backup-current-server.sh"
grep -Fq '[[ $backup_kind == weekly ]] && keep_count=4' \
  "$repo_root/scripts/prod/backup-current-server.sh"
grep -Fq 'PKUBA_START_UNDER_MAINTENANCE=1' \
  "$repo_root/scripts/prod/backup-current-server.sh"
grep -Fq 'postgres_source_digest=sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'caddy_source_digest=sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d' \
  "$repo_root/scripts/prod/deploy-blue-green.sh"
grep -Fq 'DATABASE_RESTORED=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'MEDIA_RESTORED=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'ARCHIVE_RESTORED=1' \
  "$repo_root/scripts/prod/restore-paired-data.sh"
grep -Fq 'ROLLBACK_APPLICATION_ONLY' \
  "$repo_root/scripts/prod/rollback-retained-application.sh"
grep -Fq 'database_restored=0' \
  "$repo_root/scripts/prod/rollback-retained-application.sh"
grep -Fq 'media_restored=0' \
  "$repo_root/scripts/prod/rollback-retained-application.sh"
grep -Fq 'archive_restored=0' \
  "$repo_root/scripts/prod/rollback-retained-application.sh"

state_fixture=$state_dir/release-state-fixture
mkdir -p "$state_fixture"
cat >"$state_fixture/previous-release.env" <<'EOF'
ACTIVE_SLOT=blue
CURRENT_TAG=v1.2.3
CURRENT_COMMIT=0123456789abcdef0123456789abcdef01234567
CURRENT_API_IMAGE=ghcr.io/jiumao2/pkuba-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
CURRENT_WEB_IMAGE=ghcr.io/jiumao2/pkuba-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
CURRENT_RELEASE_DIR=/opt/pkuba/production/deploy/releases/v1.2.3
CURRENT_APP_CAPABILITY=reschedule-route-v1
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

cat >"$state_fixture/compatible-contract.env" <<'EOF'
PKUBA_PREVIOUS_APP_COMPATIBLE=1
PKUBA_APP_CAPABILITY=reschedule-route-v1
PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1
EOF
[[ $(bash "$repo_root/scripts/prod/check-app-capability.sh" \
  reschedule-route-v1 "$state_fixture/compatible-contract.env") == reschedule-route-v1 ]]
if bash "$repo_root/scripts/prod/check-app-capability.sh" \
  legacy-request-type-v0 "$state_fixture/compatible-contract.env" >/dev/null 2>&1; then
  echo "an incompatible rollback application unexpectedly passed the capability gate" >&2
  exit 1
fi

cat >"$state_fixture/unarmed-contract.env" <<'EOF'
PKUBA_PREVIOUS_APP_COMPATIBLE=0
PKUBA_APP_CAPABILITY=reschedule-route-v1
PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1
EOF
if bash "$repo_root/scripts/prod/check-app-capability.sh" \
  reschedule-route-v1 "$state_fixture/unarmed-contract.env" >/dev/null 2>&1; then
  echo "an unarmed release contract unexpectedly passed the capability gate" >&2
  exit 1
fi

cat >"$state_fixture/hostile-contract.env" <<EOF
PKUBA_PREVIOUS_APP_COMPATIBLE=1
PKUBA_APP_CAPABILITY=reschedule-route-v1
PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1
UNEXPECTED=\$(touch $injection_marker)
EOF
if bash "$repo_root/scripts/prod/check-app-capability.sh" \
  reschedule-route-v1 "$state_fixture/hostile-contract.env" >/dev/null 2>&1; then
  echo "a hostile release contract unexpectedly passed fixed-key parsing" >&2
  exit 1
fi
[[ ! -e $injection_marker ]]

echo "Blue/green deployment files are structurally valid."
