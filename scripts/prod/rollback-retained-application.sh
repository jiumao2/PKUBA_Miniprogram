#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() {
  echo "application rollback error: $*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 2 ]] \
  || die "usage: rollback-retained-application.sh TARGET_SLOT ROLLBACK_APPLICATION_ONLY"
target_slot=$1
[[ $target_slot == blue || $target_slot == green ]] || die "invalid target slot"
[[ $2 == ROLLBACK_APPLICATION_ONLY ]] \
  || die "type ROLLBACK_APPLICATION_ONLY as the second argument"

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # Root-owned deployment paths and switches only; never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/production/.env}
state_dir=$deploy_root/state
slot_state_dir=$state_dir/slots
release_root=$deploy_root/releases
log_root=$deploy_root/logs
current_state=$state_dir/current.env
target_state=$slot_state_dir/$target_slot.env
target_deadline_file=$target_state.retain-until
maintenance_file=$state_dir/maintenance.enabled
upstreams_file=$state_dir/upstreams.caddy
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-prod-runtime}
gateway_project=${PKUBA_GATEWAY_PROJECT:-pkuba-gateway}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-prod-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-prod-archives}
caddy_image=${PKUBA_CADDY_IMAGE:-ghcr.io/jiumao2/pkuba-caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d}
[[ $caddy_image == ghcr.io/jiumao2/pkuba-caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d ]] \
  || die "Caddy must use the approved mirrored digest"
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
retention_seconds=${PKUBA_OLD_SLOT_RETENTION_SECONDS:-86400}
stability_seconds=${PKUBA_SERVICE_STABILITY_SECONDS:-10}
gateway_probe_attempts=${PKUBA_GATEWAY_PROBE_ATTEMPTS:-30}
gateway_probe_delay_seconds=${PKUBA_GATEWAY_PROBE_DELAY_SECONDS:-2}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for command_name in awk chmod cmp cp curl date dirname docker find git \
  mktemp mv python3 realpath rm sed seq sha256sum sort sync tail tr; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
[[ -f $env_file ]] || die "missing server environment file"
[[ -d $state_dir ]] || die "missing pre-created deployment state directory"
lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-/usr/local/libexec/pkuba/acquire-deploy-lock.py}
if [[ ! -f $lock_helper ]]; then lock_helper=$script_dir/acquire-deploy-lock.py; fi
[[ -f $lock_helper ]] || die "missing secure deployment lock helper"
if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
  exec env \
    PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} \
    python3 "$lock_helper" --state-dir "$state_dir" --timeout 1800 -- \
    bash "$0" "$@"
fi
mkdir -p "$slot_state_dir" "$log_root"

recovery_command=${PKUBA_RELEASE_RECOVERY_COMMAND:-/usr/local/sbin/pkuba-recover-release-transaction}
if [[ ! -x $recovery_command ]]; then
  recovery_command=$script_dir/recover-release-transaction.sh
fi
[[ -f $recovery_command ]] || die "missing release transaction recovery command"
PKUBA_RECOVERY_LOCK_HELD=1 bash "$recovery_command"
[[ -f $current_state ]] || die "missing current release state"
[[ -f $target_state && -f $target_deadline_file ]] \
  || die "the requested retained application is unavailable"
[[ -f $upstreams_file ]] || die "missing gateway upstream state"

# Both identities and the persisted rollback contract are proven before
# maintenance, Compose, services or any mounted data volume is touched.
identity_validator=${PKUBA_RELEASE_IDENTITY_VALIDATOR:-/usr/local/libexec/pkuba/validate-release-identity.sh}
if [[ ! -x $identity_validator ]]; then
  identity_validator=$script_dir/validate-release-identity.sh
fi
parsed_current=$(bash "$identity_validator" "$current_state" "$release_root" "$repository_dir") \
  || die "current release identity is invalid"
IFS=$'\t' read -r \
  CURRENT_SLOT CURRENT_TAG CURRENT_COMMIT CURRENT_API_IMAGE CURRENT_WEB_IMAGE CURRENT_RELEASE_DIR CURRENT_APP_CAPABILITY CURRENT_ROLLBACK_ALLOWED_FROM \
  <<<"$parsed_current"
parsed_target=$(bash "$identity_validator" "$target_state" "$release_root" "$repository_dir") \
  || die "retained release identity is invalid"
IFS=$'\t' read -r \
  TARGET_SLOT TARGET_TAG TARGET_COMMIT TARGET_API_IMAGE TARGET_WEB_IMAGE TARGET_RELEASE_DIR TARGET_APP_CAPABILITY TARGET_ROLLBACK_ALLOWED_FROM \
  <<<"$parsed_target"
[[ $TARGET_SLOT == "$target_slot" && $CURRENT_SLOT != "$target_slot" ]] \
  || die "retained slot does not represent the inactive application"
[[ $TARGET_ROLLBACK_ALLOWED_FROM != - \
  && $TARGET_ROLLBACK_ALLOWED_FROM == "$CURRENT_APP_CAPABILITY" ]] \
  || die "retained application has no rollback contract for the active capability"

retain_until=$(<"$target_deadline_file")
[[ $retain_until =~ ^[0-9]+$ ]] || die "retention deadline is invalid"
(( $(date +%s) < retain_until )) || die "the 24-hour application rollback window expired"
docker compose version >/dev/null 2>&1 || die "docker compose is unavailable"

slot_api_port() {
  if [[ $1 == blue ]]; then printf '%s\n' "$blue_api_port"; else printf '%s\n' "$green_api_port"; fi
}

slot_web_port() {
  if [[ $1 == blue ]]; then printf '%s\n' "$blue_web_port"; else printf '%s\n' "$green_web_port"; fi
}

compose_slot() {
  local slot=$1 directory=$2 api_image=$3 web_image=$4 tag=$5 commit=$6
  shift 6
  local profiles=()
  [[ $email_profile == 1 ]] && profiles=(--profile email)
  env \
    PKUBA_SLOT_NAME="pkuba-$slot" \
    PKUBA_SLOT_API_PORT="$(slot_api_port "$slot")" \
    PKUBA_SLOT_WEB_PORT="$(slot_web_port "$slot")" \
    PKUBA_API_IMAGE="$api_image" \
    PKUBA_WEB_IMAGE="$web_image" \
    PKUBA_RELEASE_TAG="$tag" \
    PKUBA_GIT_COMMIT="$commit" \
    PKUBA_ENV_FILE="$env_file" \
    PKUBA_MEDIA_VOLUME="$media_volume" \
    PKUBA_ARCHIVE_VOLUME="$archive_volume" \
    PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose \
      --project-name "pkuba-$slot" \
      --project-directory "$directory" \
      --env-file "$env_file" \
      -f "$directory/infra/compose.prod.slot.yml" \
      "${profiles[@]}" "$@"
}

compose_current() {
  compose_slot "$CURRENT_SLOT" "$CURRENT_RELEASE_DIR" "$CURRENT_API_IMAGE" \
    "$CURRENT_WEB_IMAGE" "$CURRENT_TAG" "$CURRENT_COMMIT" "$@"
}

compose_target() {
  compose_slot "$TARGET_SLOT" "$TARGET_RELEASE_DIR" "$TARGET_API_IMAGE" \
    "$TARGET_WEB_IMAGE" "$TARGET_TAG" "$TARGET_COMMIT" "$@"
}

compose_gateway() {
  env \
    PKUBA_DEPLOY_STATE_DIR="$state_dir" \
    PKUBA_CADDY_IMAGE="$caddy_image" \
    PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose \
      --project-name "$gateway_project" \
      --project-directory "$TARGET_RELEASE_DIR" \
      --env-file "$env_file" \
      -f "$TARGET_RELEASE_DIR/infra/compose.prod.gateway.yml" \
      "$@"
}

write_upstream_document() {
  local slot=$1 destination=$2
  cat >"$destination" <<EOF
(active_api) {
\treverse_proxy pkuba-$slot-api:8000
}

(active_web) {
\treverse_proxy pkuba-$slot-web:8080
}
EOF
  chmod 644 "$destination"
}

install_upstreams() {
  atomic_install "$1" "$upstreams_file" 644
}

reload_gateway() {
  compose_gateway exec -T gateway caddy reload --config /etc/caddy/Caddyfile
}

public_domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r"')
[[ $public_domain =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid PKUBA_DOMAIN"

wait_gateway_identity() {
  local tag=$1 commit=$2 api_body= web_body=
  for _ in $(seq 1 "$gateway_probe_attempts"); do
    api_body=$(curl --fail --silent --show-error \
      "https://api.$public_domain/api/v1/health/live" || true)
    web_body=$(curl --fail --silent --show-error \
      "https://admin.$public_domain/_deployment/ready" || true)
    if [[ $api_body == *"$tag"* && $api_body == *"$commit"* \
      && $web_body == *"$tag"* && $web_body == *"$commit"* ]]; then
      return 0
    fi
    sleep "$gateway_probe_delay_seconds"
  done
  return 1
}

wait_stack_ready() {
  local slot=$1 tag=$2 commit=$3 api_body= web_body= attempt
  for attempt in $(seq 1 60); do
    api_body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$(slot_api_port "$slot")/api/v1/health/ready" || true)
    web_body=$(curl --silent --show-error \
      "http://127.0.0.1:$(slot_web_port "$slot")/_deployment/ready" || true)
    if [[ $api_body == *"$tag"* && $api_body == *"$commit"* \
      && $web_body == *"$tag"* && $web_body == *"$commit"* ]] \
      && [[ $api_body == *'"status":"ok"'* || $api_body == *'"status": "ok"'* ]] \
      && [[ $web_body == *'"status":"ok"'* || $web_body == *'"status": "ok"'* ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

worker_services=(expiry scoresheet-worker archive-worker)
[[ $email_profile == 1 ]] && worker_services+=(outbox)

assert_target_services_stable() {
  local service container before after
  local services=(api web "${worker_services[@]}")
  declare -A restart_counts=()
  for service in "${services[@]}"; do
    container=$(compose_target ps -q "$service")
    [[ -n $container ]] || die "retained service has no container: $service"
    [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] \
      || die "retained service is not running: $service"
    before=$(docker inspect --format '{{.RestartCount}}' "$container")
    restart_counts[$service]=$before
  done
  sleep "$stability_seconds"
  for service in "${services[@]}"; do
    container=$(compose_target ps -q "$service")
    [[ -n $container ]] || die "retained service disappeared: $service"
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${restart_counts[$service]} ]] \
      || die "retained service restarted during stability check: $service"
  done
}

# Prepare and validate every next authoritative file in a hidden staging
# directory. Only the complete, fsynced journal is atomically published.
transaction_dir=$state_dir/release-transaction
[[ ! -e $transaction_dir ]] || die "unresolved release transaction remains after recovery"
staging_dir=$(mktemp -d "$state_dir/.release-transaction.XXXXXX")
snapshot_dir=$staging_dir/original
next_dir=$staging_dir/next
mkdir -p "$snapshot_dir" "$next_dir"

snapshot_file() {
  local source_file=$1 key=$2
  if [[ -e $source_file ]]; then
    cp -p "$source_file" "$snapshot_dir/$key"
    : >"$snapshot_dir/$key.present"
  fi
}

old_slot_state=$slot_state_dir/$CURRENT_SLOT.env
old_slot_deadline=$old_slot_state.retain-until
created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
timestamp=$(date -u -d "$created_at" +%Y%m%dT%H%M%SZ)
transaction_id=rollback-$timestamp-$CURRENT_TAG-to-$TARGET_TAG
audit_file=$log_root/$transaction_id-application-rollback.env
snapshot_file "$current_state" current
snapshot_file "$target_state" target
snapshot_file "$target_deadline_file" target-deadline
snapshot_file "$old_slot_state" old-slot
snapshot_file "$old_slot_deadline" old-deadline
snapshot_file "$upstreams_file" upstreams
snapshot_file "$audit_file" audit
maintenance_was_present=0
[[ -e $maintenance_file ]] && maintenance_was_present=1

switched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
new_deadline=$(( $(date +%s) + retention_seconds ))
cat >"$next_dir/current.env" <<EOF
ACTIVE_SLOT=$TARGET_SLOT
CURRENT_TAG=$TARGET_TAG
CURRENT_COMMIT=$TARGET_COMMIT
CURRENT_API_IMAGE=$TARGET_API_IMAGE
CURRENT_WEB_IMAGE=$TARGET_WEB_IMAGE
CURRENT_RELEASE_DIR=$TARGET_RELEASE_DIR
CURRENT_APP_CAPABILITY=$TARGET_APP_CAPABILITY
SWITCHED_AT=$switched_at
EOF
cat >"$next_dir/retained.env" <<EOF
ACTIVE_SLOT=$CURRENT_SLOT
CURRENT_TAG=$CURRENT_TAG
CURRENT_COMMIT=$CURRENT_COMMIT
CURRENT_API_IMAGE=$CURRENT_API_IMAGE
CURRENT_WEB_IMAGE=$CURRENT_WEB_IMAGE
CURRENT_RELEASE_DIR=$CURRENT_RELEASE_DIR
CURRENT_APP_CAPABILITY=$CURRENT_APP_CAPABILITY
ROLLBACK_ALLOWED_FROM_CAPABILITY=$TARGET_APP_CAPABILITY
SWITCHED_AT=$switched_at
EOF
printf '%s\n' "$new_deadline" >"$next_dir/retained.retain-until"
write_upstream_document "$TARGET_SLOT" "$next_dir/upstreams.caddy"
cat >"$next_dir/audit.env" <<EOF
rollback_type=application_only
from_tag=$CURRENT_TAG
to_tag=$TARGET_TAG
database_restored=0
media_restored=0
archive_restored=0
completed_at=$switched_at
EOF
chmod 600 "$next_dir/current.env" "$next_dir/retained.env" \
  "$next_dir/retained.retain-until" "$next_dir/audit.env"
bash "$identity_validator" "$next_dir/current.env" "$release_root" "$repository_dir" >/dev/null
bash "$identity_validator" "$next_dir/retained.env" "$release_root" "$repository_dir" >/dev/null

cat >"$staging_dir/journal.env" <<EOF
JOURNAL_VERSION=1
TRANSACTION_ID=$transaction_id
TRANSACTION_KIND=ROLLBACK
PHASE=PREPARED
ORIGINAL_MAINTENANCE=$maintenance_was_present
OLD_SLOT=$CURRENT_SLOT
NEW_SLOT=$TARGET_SLOT
OLD_TAG=$CURRENT_TAG
OLD_COMMIT=$CURRENT_COMMIT
NEW_TAG=$TARGET_TAG
NEW_COMMIT=$TARGET_COMMIT
BACKUP_DIR=-
AUDIT_FILE=$audit_file
CREATED_AT=$created_at
EOF
chmod 600 "$staging_dir/journal.env"
grep -v '^PHASE=' "$staging_dir/journal.env" >"$staging_dir/immutable.env"
chmod 600 "$staging_dir/immutable.env"
(
  cd "$staging_dir"
  sha256sum immutable.env >immutable.sha256
  find original -type f -exec sha256sum '{}' + | sort -k2 >original.sha256
  find next -type f -exec sha256sum '{}' + | sort -k2 >prepared.sha256
  sha256sum --check immutable.sha256 >/dev/null
  sha256sum --check original.sha256 >/dev/null
  sha256sum --check prepared.sha256 >/dev/null
)
while IFS= read -r transaction_file; do sync -f "$transaction_file"; done \
  < <(find "$staging_dir" -type f -print)
sync -f "$staging_dir"
mv "$staging_dir" "$transaction_dir"
sync -f "$state_dir"
snapshot_dir=$transaction_dir/original
next_dir=$transaction_dir/next
journal_file=$transaction_dir/journal.env
transaction_prepared=1
recovery_invoked=0

write_journal_phase() {
  local phase=$1 temporary=$journal_file.tmp.$$
  awk -F= -v phase="$phase" \
    'BEGIN {OFS="="} $1 == "PHASE" {$2=phase} {print $1,$2}' \
    "$journal_file" >"$temporary"
  chmod 600 "$temporary"
  sync -f "$temporary"
  mv -f "$temporary" "$journal_file"
  sync -f "$transaction_dir"
}

atomic_install() {
  local source_file=$1 destination=$2 mode=${3:-600}
  local temporary=$destination.commit.$$
  cp "$source_file" "$temporary"
  chmod "$mode" "$temporary"
  sync -f "$temporary"
  mv -f "$temporary" "$destination"
  sync -f "$(dirname "$destination")"
}

remove_persisted() {
  local destination
  for destination in "$@"; do
    rm -f "$destination"
    sync -f "$(dirname "$destination")"
  done
}

crash_point() {
  if [[ ${PKUBA_TEST_CRASH_POINT:-} == "$1" ]]; then
    kill -KILL $$
  fi
}

verify_committed_target_state() {
  cmp -s "$next_dir/retained.env" "$old_slot_state" \
    || die "retained state commit verification failed"
  cmp -s "$next_dir/retained.retain-until" "$old_slot_deadline" \
    || die "retained deadline commit verification failed"
  cmp -s "$next_dir/current.env" "$current_state" \
    || die "current state commit verification failed"
  cmp -s "$next_dir/upstreams.caddy" "$upstreams_file" \
    || die "gateway upstream commit verification failed"
  [[ ! -e $target_state && ! -e $target_deadline_file ]] \
    || die "new active slot still has retained-state files"
  cmp -s "$next_dir/audit.env" "$audit_file" \
    || die "application rollback audit commit verification failed"
  bash "$identity_validator" "$current_state" "$release_root" "$repository_dir" >/dev/null
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ $status -ne 0 && $transaction_prepared == 1 \
    && -d $transaction_dir && $recovery_invoked == 0 ]]; then
    recovery_invoked=1
    if ! PKUBA_RECOVERY_LOCK_HELD=1 bash "$recovery_command"; then
      touch "$maintenance_file"
      echo "Automatic recovery failed; maintenance and the durable journal remain." >&2
    fi
  fi
  exit "$status"
}
trap on_exit EXIT

touch "$maintenance_file"
sync -f "$state_dir"
crash_point prepared
writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
[[ -x $writer_fence ]] || writer_fence=$script_dir/fence-deploy-writers.sh
[[ -f $writer_fence ]] || die "missing two-slot writer fence helper"
bash "$writer_fence" || die "could not establish the two-slot writer fence"
compose_target up -d --no-deps web "${worker_services[@]}"
compose_target up -d --no-deps api
wait_stack_ready "$TARGET_SLOT" "$TARGET_TAG" "$TARGET_COMMIT" \
  || die "retained application did not become ready"
assert_target_services_stable
install_upstreams "$next_dir/upstreams.caddy"
reload_gateway
write_journal_phase RUNTIME_SWITCHED
crash_point runtime-switched
wait_gateway_identity "$TARGET_TAG" "$TARGET_COMMIT" \
  || die "stable gateway did not expose the retained application"

# Until NEW_COMMITTED is durable, any error or crash restores the original
# application and its complete state snapshot under maintenance.
write_journal_phase STATE_COMMITTING
crash_point state-committing
atomic_install "$next_dir/retained.env" "$old_slot_state"
crash_point after-retained-state
atomic_install "$next_dir/retained.retain-until" "$old_slot_deadline"
crash_point after-retained-deadline
atomic_install "$next_dir/current.env" "$current_state"
crash_point after-current-state
remove_persisted "$target_state" "$target_deadline_file"
crash_point after-target-state-removal
atomic_install "$next_dir/audit.env" "$audit_file"
crash_point after-audit
verify_committed_target_state
write_journal_phase NEW_COMMITTED
crash_point new-committed

recovery_invoked=1
PKUBA_RECOVERY_LOCK_HELD=1 bash "$recovery_command"
transaction_prepared=0
trap - EXIT

echo "PKUBA_APPLICATION_ROLLBACK_RESULT=success"
echo "PKUBA_ACTIVE_TAG=$TARGET_TAG"
echo "PKUBA_DATABASE_RESTORED=0"
echo "PKUBA_MEDIA_RESTORED=0"
echo "PKUBA_ARCHIVE_RESTORED=0"
