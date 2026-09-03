#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() {
  echo "deployment error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 4 ]] || die "usage: deploy-blue-green.sh TAG COMMIT API_IMAGE WEB_IMAGE"

release_tag=$1
release_commit=$2
api_image=$3
web_image=$4
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid release tag"
[[ $release_commit =~ ^[0-9a-f]{40}$ ]] || die "invalid release commit"
[[ $api_image =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] \
  || die "API image must use the approved immutable digest"
[[ $web_image =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] \
  || die "web image must use the approved immutable digest"

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # This root-owned file contains paths and feature switches, never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/production/.env}
state_dir=$deploy_root/state
slot_state_dir=$state_dir/slots
release_root=$deploy_root/releases
backup_root=$deploy_root/backups
log_root=$deploy_root/logs
current_state=$state_dir/current.env
upstreams_file=$state_dir/upstreams.caddy
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-prod-runtime}
data_project=${PKUBA_DATA_PROJECT:-pkuba-data}
gateway_project=${PKUBA_GATEWAY_PROJECT:-pkuba-gateway}
postgres_volume=${PKUBA_POSTGRES_VOLUME:-pkuba-prod-postgres}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-prod-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-prod-archives}
postgres_source_digest=sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
caddy_source_digest=sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
postgres_image=${PKUBA_POSTGRES_IMAGE:-ghcr.io/jiumao2/pkuba-postgres@$postgres_source_digest}
caddy_image=${PKUBA_CADDY_IMAGE:-ghcr.io/jiumao2/pkuba-caddy@$caddy_source_digest}
[[ $postgres_image == "ghcr.io/jiumao2/pkuba-postgres@$postgres_source_digest" ]] \
  || die "PostgreSQL must use the approved mirrored digest"
[[ $caddy_image == "ghcr.io/jiumao2/pkuba-caddy@$caddy_source_digest" ]] \
  || die "Caddy must use the approved mirrored digest"
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
preflight_wait_seconds=${PKUBA_DEPLOY_PREFLIGHT_WAIT_SECONDS:-900}
retention_seconds=${PKUBA_OLD_SLOT_RETENTION_SECONDS:-7200}
stability_seconds=${PKUBA_SERVICE_STABILITY_SECONDS:-10}
gateway_probe_attempts=${PKUBA_GATEWAY_PROBE_ATTEMPTS:-30}
gateway_probe_delay_seconds=${PKUBA_GATEWAY_PROBE_DELAY_SECONDS:-2}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
automation_armed=${PKUBA_PRODUCTION_AUTOMATION_ARMED:-0}

[[ $automation_armed == 1 ]] \
  || die "production automation is not armed; complete the isolated blue/green rehearsal first"

for command_name in awk chmod cmp cp curl df dirname docker find git head \
  mktemp mv python3 realpath rm sed seq sha256sum sort sync tar; do
  require_command "$command_name"
done

[[ -f $env_file ]] || die "missing server environment file: $env_file"
[[ -d $repository_dir/.git ]] || die "missing read-only repository: $repository_dir"
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
mkdir -p "$slot_state_dir" "$release_root" "$backup_root" "$log_root"

recovery_command=${PKUBA_RELEASE_RECOVERY_COMMAND:-/usr/local/sbin/pkuba-recover-release-transaction}
if [[ ! -x $recovery_command ]]; then
  recovery_command=$script_dir/recover-release-transaction.sh
fi
[[ -f $recovery_command ]] || die "missing release transaction recovery command"
PKUBA_RECOVERY_LOCK_HELD=1 bash "$recovery_command"
[[ -f $current_state ]] || die "missing blue/green state: $current_state"

# State is data, never shell. Verify its fixed keys, release worktree/tag,
# contract-derived capability and immutable images before Compose or data access.
identity_validator=${PKUBA_RELEASE_IDENTITY_VALIDATOR:-/usr/local/libexec/pkuba/validate-release-identity.sh}
if [[ ! -x $identity_validator ]]; then
  identity_validator=$script_dir/validate-release-identity.sh
fi
parsed_current=$(bash "$identity_validator" "$current_state" "$release_root" "$repository_dir") \
  || die "current release state or identity is invalid"
IFS=$'\t' read -r \
  ACTIVE_SLOT CURRENT_TAG CURRENT_COMMIT CURRENT_API_IMAGE CURRENT_WEB_IMAGE CURRENT_RELEASE_DIR CURRENT_APP_CAPABILITY CURRENT_ROLLBACK_ALLOWED_FROM \
  <<<"$parsed_current"
docker compose version >/dev/null 2>&1 || die "docker compose is unavailable"
docker pull "$postgres_image"
docker pull "$caddy_image"
docker image inspect "$postgres_image" >/dev/null
docker image inspect "$caddy_image" >/dev/null

# Build the cleanup input while the deployment still has no candidate
# worktree, maintenance state, backup payload or slot-state side effect.  A
# process substitution would hide a producer failure from mapfile; capture the
# pipefail status explicitly and abort before any cleanup or state commit.
if ! existing_backup_output=$(find "$backup_root" -mindepth 2 -maxdepth 2 \
  -type f -name SUCCESS -printf '%T@ %h\n' \
  | sort -nr \
  | awk '{$1=""; sub(/^ /, ""); print}'); then
  die "could not enumerate existing paired backups"
fi
declare -a existing_successful_backup_candidates=()
if [[ -n $existing_backup_output ]]; then
  mapfile -t existing_successful_backup_candidates <<<"$existing_backup_output"
fi

if [[ $ACTIVE_SLOT == blue ]]; then
  candidate_slot=green
else
  candidate_slot=blue
fi

slot_project() {
  printf 'pkuba-%s\n' "$1"
}

slot_api_port() {
  if [[ $1 == blue ]]; then printf '%s\n' "$blue_api_port"; else printf '%s\n' "$green_api_port"; fi
}

slot_web_port() {
  if [[ $1 == blue ]]; then printf '%s\n' "$blue_web_port"; else printf '%s\n' "$green_web_port"; fi
}

compose_data() {
  local directory=$1
  shift
  env \
    PKUBA_ENV_FILE="$env_file" \
    PKUBA_POSTGRES_IMAGE="$postgres_image" \
    PKUBA_POSTGRES_VOLUME="$postgres_volume" \
    PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose \
      --project-name "$data_project" \
      --project-directory "$directory" \
      --env-file "$env_file" \
      -f "$directory/infra/compose.prod.data.yml" \
      "$@"
}

compose_gateway() {
  local directory=$1
  shift
  env \
    PKUBA_DEPLOY_STATE_DIR="$state_dir" \
    PKUBA_CADDY_IMAGE="$caddy_image" \
    PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose \
      --project-name "$gateway_project" \
      --project-directory "$directory" \
      --env-file "$env_file" \
      -f "$directory/infra/compose.prod.gateway.yml" \
      "$@"
}

compose_slot() {
  local slot=$1
  local directory=$2
  local target_api_image=$3
  local target_web_image=$4
  local target_tag=$5
  local target_commit=$6
  shift 6
  local profile_args=()
  if [[ $email_profile == 1 ]]; then
    profile_args=(--profile email)
  fi
  env \
    PKUBA_SLOT_NAME="pkuba-$slot" \
    PKUBA_SLOT_API_PORT="$(slot_api_port "$slot")" \
    PKUBA_SLOT_WEB_PORT="$(slot_web_port "$slot")" \
    PKUBA_API_IMAGE="$target_api_image" \
    PKUBA_WEB_IMAGE="$target_web_image" \
    PKUBA_RELEASE_TAG="$target_tag" \
    PKUBA_GIT_COMMIT="$target_commit" \
    PKUBA_ENV_FILE="$env_file" \
    PKUBA_MEDIA_VOLUME="$media_volume" \
    PKUBA_ARCHIVE_VOLUME="$archive_volume" \
    PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose \
      --project-name "$(slot_project "$slot")" \
      --project-directory "$directory" \
      --env-file "$env_file" \
      -f "$directory/infra/compose.prod.slot.yml" \
      "${profile_args[@]}" \
      "$@"
}

compose_active() {
  compose_slot "$ACTIVE_SLOT" "$CURRENT_RELEASE_DIR" "$CURRENT_API_IMAGE" \
    "$CURRENT_WEB_IMAGE" "$CURRENT_TAG" "$CURRENT_COMMIT" "$@"
}

compose_candidate() {
  compose_slot "$candidate_slot" "$release_dir" "$api_image" "$web_image" \
    "$release_tag" "$release_commit" "$@"
}

echo "Fetching $release_tag from the read-only deployment checkout."
git -C "$repository_dir" fetch --force origin main \
  "+refs/tags/$release_tag:refs/tags/$release_tag"
resolved_commit=$(git -C "$repository_dir" rev-parse "$release_tag^{commit}")
[[ $resolved_commit == "$release_commit" ]] || die "tag does not resolve to requested commit"
git -C "$repository_dir" merge-base --is-ancestor "$release_commit" origin/main \
  || die "release commit is not reachable from origin/main"

release_dir=$release_root/$release_tag
if [[ -e $release_dir ]]; then
  [[ -d $release_dir/.git || -f $release_dir/.git ]] || die "invalid release directory"
  [[ $(git -C "$release_dir" rev-parse HEAD) == "$release_commit" ]] \
    || die "release directory points to another commit"
else
  git -C "$repository_dir" worktree add --detach "$release_dir" "$release_commit"
fi

for required_file in \
  infra/compose.prod.data.yml \
  infra/compose.prod.gateway.yml \
  infra/compose.prod.slot.yml \
  infra/Caddyfile.gateway \
  infra/Caddyfile.slot \
  infra/release-contract.env \
  scripts/prod/check-app-capability.sh \
  scripts/prod/parse-release-contract.sh \
  scripts/prod/derive-release-capability.sh \
  scripts/prod/parse-release-state.sh \
  scripts/prod/validate-release-identity.sh \
  scripts/prod/recover-release-transaction.sh \
  scripts/prod/acquire-deploy-lock.py \
  scripts/prod/fence-deploy-writers.sh \
  scripts/prod/verify-paired-backup.py; do
  [[ -f $release_dir/$required_file ]] || die "release is missing $required_file"
done

candidate_app_capability=$(bash "$release_dir/scripts/prod/check-app-capability.sh" \
  "$CURRENT_APP_CAPABILITY" "$release_dir/infra/release-contract.env") \
  || die "release cannot safely coexist with or roll back to the active application"

candidate_state=$slot_state_dir/$candidate_slot.env
if [[ -f $candidate_state ]]; then
  parsed_retained=$(bash "$identity_validator" \
    "$candidate_state" "$release_root" "$repository_dir") \
    || die "retained slot state or identity is invalid"
  IFS=$'\t' read -r retained_slot _ _ _ _ _ _ _ <<<"$parsed_retained"
  [[ $retained_slot == "$candidate_slot" ]] || die "retained slot identity is inconsistent"
  retain_file=$candidate_state.retain-until
  [[ -f $retain_file ]] || die "retained slot deadline is missing"
  RETAIN_UNTIL_EPOCH=$(<"$retain_file")
  [[ $RETAIN_UNTIL_EPOCH =~ ^[0-9]+$ ]] \
    || die "retained slot deadline is invalid"
  if (( $(date +%s) < RETAIN_UNTIL_EPOCH )); then
    die "$candidate_slot still contains the configured application rollback stack"
  fi
fi

for name in "$runtime_network"; do
  docker network inspect "$name" >/dev/null || die "missing external network: $name"
done
for name in "$postgres_volume" "$media_volume" "$archive_volume"; do
  docker volume inspect "$name" >/dev/null || die "missing external volume: $name"
done

compose_data "$release_dir" config --quiet
compose_gateway "$release_dir" config --quiet
compose_candidate config --quiet
data_container=$(compose_data "$release_dir" ps -q db)
gateway_container=$(compose_gateway "$release_dir" ps -q gateway)
[[ -n $data_container && $(docker inspect --format '{{.State.Running}}' "$data_container") == true ]] \
  || die "stable PostgreSQL service is not running"
[[ -n $gateway_container && $(docker inspect --format '{{.State.Running}}' "$gateway_container") == true ]] \
  || die "stable gateway service is not running"

deployment_log=$log_root/$(date -u +%Y%m%dT%H%M%SZ)-$release_tag.log
exec > >(tee -a "$deployment_log") 2>&1
echo "Preparing inactive $candidate_slot for $release_tag ($release_commit)."
echo "Active release remains $CURRENT_TAG ($CURRENT_COMMIT) on $ACTIVE_SLOT."

compose_candidate pull api web expiry scoresheet-worker archive-worker
if [[ $email_profile == 1 ]]; then compose_candidate pull outbox; fi
docker image inspect "$api_image" >/dev/null
docker image inspect "$web_image" >/dev/null
[[ $(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$api_image") == "$release_commit" ]] \
  || die "API image revision label does not match the requested commit"
[[ $(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$web_image") == "$release_commit" ]] \
  || die "web image revision label does not match the requested commit"

# Only the inactive application project is replaced. All three data volumes are
# external and therefore cannot be removed by this command.
compose_candidate down --remove-orphans

echo "Waiting for recognition, archive, purge, edit and expiry work to become idle."
compose_active exec -T api python manage.py deployment_preflight \
  "--wait-seconds=$preflight_wait_seconds" --poll-seconds=5 --json

database_bytes=$(compose_data "$release_dir" exec -T db sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT pg_database_size(current_database())"')
[[ $database_bytes =~ ^[0-9]+$ ]] || die "could not determine database size"
volume_bytes() {
  docker run --rm --entrypoint sh -v "$1:/source:ro" "$postgres_image" \
    -ec "du -sk /source | awk '{print \$1 * 1024}'"
}
media_bytes=$(volume_bytes "$media_volume")
archive_bytes=$(volume_bytes "$archive_volume")
[[ $media_bytes =~ ^[0-9]+$ && $archive_bytes =~ ^[0-9]+$ ]] \
  || die "could not determine media/archive size"
read -r filesystem_bytes available_bytes < <(df -PB1 "$deploy_root" | awk 'NR == 2 {print $2, $4}')
reserve_bytes=$((filesystem_bytes / 4))
(( reserve_bytes >= 10737418240 )) || reserve_bytes=10737418240
payload_bytes=$((database_bytes + media_bytes + archive_bytes))
startup_headroom_bytes=${PKUBA_DEPLOY_STARTUP_HEADROOM_BYTES:-16106127360}
hard_floor_bytes=${PKUBA_DEPLOY_HARD_FLOOR_BYTES:-10737418240}
(( available_bytes >= hard_floor_bytes )) \
  || die "deployment hard-blocked below 10 GiB free space: available=$available_bytes"
required_bytes=$((payload_bytes * 115 / 100 + reserve_bytes))
(( required_bytes >= startup_headroom_bytes )) || required_bytes=$startup_headroom_bytes
(( available_bytes >= required_bytes )) \
  || die "insufficient disk space: available=$available_bytes required=$required_bytes"

created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
timestamp=$(date -u -d "$created_at" +%Y%m%dT%H%M%SZ)
transaction_id=deploy-$timestamp-$CURRENT_TAG-to-$release_tag
backup_dir=$backup_root/$timestamp-pre-$release_tag
[[ ! -e $backup_dir ]] || die "backup directory already exists: $backup_dir"
mkdir -p "$backup_dir"
cp "$current_state" "$backup_dir/previous-release.env"

maintenance_file=$state_dir/maintenance.enabled
transaction_prepared=0
recovery_invoked=0
maintenance_was_present=0
[[ -e $maintenance_file ]] && maintenance_was_present=1
transaction_dir=$state_dir/release-transaction
[[ ! -e $transaction_dir ]] || die "unresolved release transaction remains after recovery"
staging_dir=$(mktemp -d "$state_dir/.release-transaction.XXXXXX")
snapshot_dir=$staging_dir/original
next_dir=$staging_dir/next
mkdir -p "$snapshot_dir" "$next_dir"
active_slot_state=$slot_state_dir/$ACTIVE_SLOT.env
active_slot_deadline=$active_slot_state.retain-until
candidate_deadline=$candidate_state.retain-until

snapshot_file() {
  local source_file=$1 key=$2
  if [[ -e $source_file ]]; then
    cp -p "$source_file" "$snapshot_dir/$key"
    : >"$snapshot_dir/$key.present"
  fi
}

snapshot_file "$current_state" current
snapshot_file "$candidate_state" candidate
snapshot_file "$candidate_deadline" candidate-deadline
snapshot_file "$active_slot_state" active-slot
snapshot_file "$active_slot_deadline" active-deadline
snapshot_file "$upstreams_file" upstreams
snapshot_file "$backup_dir/SHA256SUMS" backup-sha
snapshot_file "$backup_dir/release.json" release-json
snapshot_file "$backup_dir/SUCCESS" success

switched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
retain_until=$(( $(date +%s) + retention_seconds ))
cat >"$next_dir/current.env" <<EOF
ACTIVE_SLOT=$candidate_slot
CURRENT_TAG=$release_tag
CURRENT_COMMIT=$release_commit
CURRENT_API_IMAGE=$api_image
CURRENT_WEB_IMAGE=$web_image
CURRENT_RELEASE_DIR=$release_dir
CURRENT_APP_CAPABILITY=$candidate_app_capability
SWITCHED_AT=$switched_at
EOF
cat >"$next_dir/retained.env" <<EOF
ACTIVE_SLOT=$ACTIVE_SLOT
CURRENT_TAG=$CURRENT_TAG
CURRENT_COMMIT=$CURRENT_COMMIT
CURRENT_API_IMAGE=$CURRENT_API_IMAGE
CURRENT_WEB_IMAGE=$CURRENT_WEB_IMAGE
CURRENT_RELEASE_DIR=$CURRENT_RELEASE_DIR
CURRENT_APP_CAPABILITY=$CURRENT_APP_CAPABILITY
ROLLBACK_ALLOWED_FROM_CAPABILITY=$candidate_app_capability
SWITCHED_AT=$switched_at
EOF
printf '%s\n' "$retain_until" >"$next_dir/retained.retain-until"
cat >"$next_dir/upstreams.caddy" <<EOF
(active_api) {
    reverse_proxy pkuba-$candidate_slot-api:8000
}

(active_web) {
    reverse_proxy pkuba-$candidate_slot-web:8080
}
EOF
cat >"$next_dir/release.json" <<EOF
{"tag":"$release_tag","commit":"$release_commit","slot":"$candidate_slot","previous_slot":"$ACTIVE_SLOT","api_image":"$api_image","web_image":"$web_image","postgres_source_digest":"$postgres_source_digest","postgres_mirror_digest":"${postgres_image##*@}","caddy_source_digest":"$caddy_source_digest","caddy_mirror_digest":"${caddy_image##*@}","switched_at":"$switched_at"}
EOF
chmod 600 "$next_dir/current.env" "$next_dir/retained.env" \
  "$next_dir/retained.retain-until" "$next_dir/release.json"
chmod 644 "$next_dir/upstreams.caddy"
bash "$identity_validator" "$next_dir/current.env" "$release_root" "$repository_dir" >/dev/null
bash "$identity_validator" "$next_dir/retained.env" "$release_root" "$repository_dir" >/dev/null

cat >"$staging_dir/journal.env" <<EOF
JOURNAL_VERSION=1
TRANSACTION_ID=$transaction_id
TRANSACTION_KIND=DEPLOY
PHASE=PREPARED
ORIGINAL_MAINTENANCE=$maintenance_was_present
OLD_SLOT=$ACTIVE_SLOT
NEW_SLOT=$candidate_slot
OLD_TAG=$CURRENT_TAG
OLD_COMMIT=$CURRENT_COMMIT
NEW_TAG=$release_tag
NEW_COMMIT=$release_commit
BACKUP_DIR=$backup_dir
AUDIT_FILE=-
CREATED_AT=$created_at
EOF
chmod 600 "$staging_dir/journal.env"
grep -v '^PHASE=' "$staging_dir/journal.env" >"$staging_dir/immutable.env"
chmod 600 "$staging_dir/immutable.env"
(
  cd "$staging_dir"
  sha256sum immutable.env >immutable.sha256
  find original -type f -exec sha256sum '{}' + | sort -k2 >original.sha256
  sha256sum --check immutable.sha256 >/dev/null
  sha256sum --check original.sha256 >/dev/null
)
while IFS= read -r transaction_file; do sync -f "$transaction_file"; done \
  < <(find "$staging_dir" -type f -print)
sync -f "$staging_dir"
mv "$staging_dir" "$transaction_dir"
sync -f "$state_dir"
transaction_prepared=1
snapshot_dir=$transaction_dir/original
next_dir=$transaction_dir/next
journal_file=$transaction_dir/journal.env

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

verify_committed_candidate_state() {
  cmp -s "$next_dir/retained.env" "$active_slot_state" \
    || die "retained state commit verification failed"
  cmp -s "$next_dir/retained.retain-until" "$active_slot_deadline" \
    || die "retained deadline commit verification failed"
  cmp -s "$next_dir/current.env" "$current_state" \
    || die "current state commit verification failed"
  cmp -s "$next_dir/upstreams.caddy" "$upstreams_file" \
    || die "gateway upstream commit verification failed"
  [[ ! -e $candidate_state && ! -e $candidate_deadline ]] \
    || die "new active slot still has retained-state files"
  cmp -s "$next_dir/release.json" "$backup_dir/release.json" \
    || die "release audit commit verification failed"
  cmp -s "$next_dir/SHA256SUMS" "$backup_dir/SHA256SUMS" \
    || die "backup checksum commit verification failed"
  cmp -s "$next_dir/SUCCESS" "$backup_dir/SUCCESS" \
    || die "success marker commit verification failed"
  bash "$identity_validator" "$current_state" "$release_root" "$repository_dir" >/dev/null
  (cd "$backup_dir" && sha256sum --check SHA256SUMS >/dev/null)
}

worker_services=(expiry scoresheet-worker archive-worker)
if [[ $email_profile == 1 ]]; then worker_services+=(outbox); fi

install_upstreams() {
  atomic_install "$1" "$upstreams_file" 644
}

reload_gateway() {
  compose_gateway "$release_dir" exec -T gateway \
    caddy reload --config /etc/caddy/Caddyfile
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

wait_for_candidate() {
  local api_port web_port attempt api_body web_body
  api_port=$(slot_api_port "$candidate_slot")
  web_port=$(slot_web_port "$candidate_slot")
  for attempt in $(seq 1 60); do
    api_body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$api_port/api/v1/health/ready" || true)
    web_body=$(curl --silent --show-error \
      "http://127.0.0.1:$web_port/_deployment/ready" || true)
    if [[ $api_body == *"$release_tag"* && $api_body == *"$release_commit"* \
      && $web_body == *"$release_tag"* && $web_body == *"$release_commit"* ]] \
      && [[ $api_body == *'"status": "ok"'* || $api_body == *'"status":"ok"'* ]] \
      && [[ $web_body == *'"status": "ok"'* || $web_body == *'"status":"ok"'* ]]; then
      return 0
    fi
    sleep 3
  done
  die "candidate readiness did not become healthy"
}

assert_candidate_services_stable() {
  local service container before after
  local services=(api web "${worker_services[@]}")
  declare -A restart_counts=()
  for service in "${services[@]}"; do
    container=$(compose_candidate ps -q "$service")
    [[ -n $container ]] || die "candidate service has no container: $service"
    [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] \
      || die "candidate service is not running: $service"
    before=$(docker inspect --format '{{.RestartCount}}' "$container")
    restart_counts[$service]=$before
  done
  sleep "$stability_seconds"
  for service in "${services[@]}"; do
    container=$(compose_candidate ps -q "$service")
    [[ -n $container ]] || die "candidate service disappeared: $service"
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${restart_counts[$service]} ]] \
      || die "candidate service restarted during stability check: $service"
  done
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

echo "Entering the bounded writer fence."
touch "$maintenance_file"
sync -f "$state_dir"
crash_point prepared
writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
if [[ ! -x $writer_fence ]]; then writer_fence=$script_dir/fence-deploy-writers.sh; fi
[[ -f $writer_fence ]] || die "missing two-slot writer fence helper"
bash "$writer_fence" || die "could not establish the two-slot writer fence"

echo "Creating one paired DB/media/archive rollback point."
compose_data "$release_dir" exec -T db sh -ec \
  'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$backup_dir/database.dump"
[[ -s $backup_dir/database.dump ]] || die "database dump is empty"
compose_data "$release_dir" exec -T db sh -ec 'pg_restore --list >/dev/null' \
  <"$backup_dir/database.dump"
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'tar -C /source -czf /backup/private-media.tar.gz .'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'tar -C /source -czf /backup/archive-staging.tar.gz .'
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'cd /source && find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/private-media.files.sha256'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'cd /source && find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/archive-staging.files.sha256'
tar -tzf "$backup_dir/private-media.tar.gz" >/dev/null
tar -tzf "$backup_dir/archive-staging.tar.gz" >/dev/null
cat >"$backup_dir/MANIFEST.env" <<EOF
MANIFEST_VERSION=2
TRANSACTION_ID=$transaction_id
CREATED_AT=$created_at
FROM_TAG=$CURRENT_TAG
FROM_COMMIT=$CURRENT_COMMIT
TO_TAG=$release_tag
TO_COMMIT=$release_commit
DATABASE_BYTES=$database_bytes
MEDIA_BYTES=$media_bytes
ARCHIVE_BYTES=$archive_bytes
EOF
(
  cd "$backup_dir"
  sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
    private-media.files.sha256 archive-staging.files.sha256 \
    previous-release.env MANIFEST.env \
    >"$transaction_dir/backup-base.SHA256SUMS"
  sha256sum --check "$transaction_dir/backup-base.SHA256SUMS"
)
(
  cd "$transaction_dir"
  sha256sum backup-base.SHA256SUMS >backup-base.sha256.tmp
  sync -f backup-base.sha256.tmp
  mv -f backup-base.sha256.tmp backup-base.sha256
  sync -f "$transaction_dir"
)
echo "Applying compatible migrations from the candidate image."
compose_candidate run --rm --no-deps api python manage.py migrate --noinput
compose_candidate run --rm --no-deps api python manage.py audit_season_integrity --json \
  >"$backup_dir/season-integrity-after-migrate.json"
compose_candidate run --rm --no-deps api python manage.py check --deploy
compose_candidate run --rm --no-deps api python manage.py showmigrations core --plan \
  >"$backup_dir/core-migrations.txt"
[[ -s $backup_dir/season-integrity-after-migrate.json \
  && -s $backup_dir/core-migrations.txt ]] \
  || die "migration audit outputs are incomplete"

echo "Starting the isolated candidate application and candidate workers."
compose_candidate up -d --no-deps web "${worker_services[@]}"
compose_candidate up -d --no-deps api
wait_for_candidate
assert_candidate_services_stable

candidate_api_port=$(slot_api_port "$candidate_slot")
candidate_web_port=$(slot_web_port "$candidate_slot")
curl --fail --silent --show-error \
  -H 'Host: api' -H 'X-Forwarded-Proto: https' \
  "http://127.0.0.1:$candidate_api_port/api/v1/public/season" >/dev/null \
  || [[ $(curl --silent --output /dev/null --write-out '%{http_code}' \
    -H 'Host: api' -H 'X-Forwarded-Proto: https' \
    "http://127.0.0.1:$candidate_api_port/api/v1/public/season") == 404 ]]
curl --fail --silent --show-error "http://127.0.0.1:$candidate_web_port/" >/dev/null

(
  cd "$backup_dir"
  sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
    private-media.files.sha256 archive-staging.files.sha256 \
    previous-release.env MANIFEST.env season-integrity-after-migrate.json \
    core-migrations.txt >"$next_dir/SHA256SUMS"
)
release_json_hash=$(sha256sum "$next_dir/release.json" | awk '{print $1}')
printf '%s  release.json\n' "$release_json_hash" >>"$next_dir/SHA256SUMS"
manifest_hash=$(sha256sum "$next_dir/SHA256SUMS" | awk '{print $1}')
cat >"$next_dir/SUCCESS" <<EOF
TRANSACTION_ID=$transaction_id
MANIFEST_SHA256=$manifest_hash
COMMITTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
chmod 600 "$next_dir/SHA256SUMS" "$next_dir/SUCCESS"
while IFS= read -r backup_payload; do
  [[ -f $backup_payload && ! -L $backup_payload ]] \
    || die "backup payload is not a regular non-symlink file: $backup_payload"
  sync -f "$backup_payload"
done < <(find "$backup_dir" -mindepth 1 -maxdepth 1 -type f -print)
sync -f "$backup_dir"
sync -f "$backup_root"
[[ $(sha256sum "$next_dir/release.json" | awk '{print $1}') == "$release_json_hash" ]] \
  || die "release audit document changed during preparation"
(
  cd "$transaction_dir"
  find next -type f -exec sha256sum '{}' + | sort -k2 >prepared.sha256.tmp
  sha256sum --check prepared.sha256.tmp >/dev/null
  sync -f prepared.sha256.tmp
  mv -f prepared.sha256.tmp prepared.sha256
  sync -f "$transaction_dir"
)

# The paired rollback point is a transaction of its own. Every payload and
# audit file is durable before SHA256SUMS is installed, and SUCCESS is the last
# commit marker. Until the release journal reaches NEW_COMMITTED, recovery will
# remove these three files again if deployment does not finish.
atomic_install "$next_dir/release.json" "$backup_dir/release.json"
crash_point after-release-json
atomic_install "$next_dir/SHA256SUMS" "$backup_dir/SHA256SUMS"
crash_point after-backup-checksum
atomic_install "$next_dir/SUCCESS" "$backup_dir/SUCCESS"
sync -f "$backup_root"
crash_point after-success-marker

backup_verifier=$release_dir/scripts/prod/verify-paired-backup.py
[[ -f $backup_verifier ]] || die "release is missing paired backup verifier"
python3 "$backup_verifier" \
  --backup-dir "$backup_dir" \
  --backup-root "$backup_root" \
  --release-root "$release_root" \
  --repository-dir "$repository_dir" \
  --identity-validator "$identity_validator" \
  --scratch-root "$transaction_dir/backup-verify-scratch" \
  >/dev/null || die "paired rollback point failed semantic verification"
rm -rf -- "$transaction_dir/backup-verify-scratch"
sync -f "$transaction_dir"

echo "Switching the stable gateway to the verified candidate under maintenance."
install_upstreams "$next_dir/upstreams.caddy"
reload_gateway
write_journal_phase RUNTIME_SWITCHED
crash_point runtime-switched
wait_gateway_identity "$release_tag" "$release_commit" \
  || die "stable gateway did not expose the verified candidate"

# Before NEW_COMMITTED, every crash or ordinary failure deterministically
# restores the old application and every original state file.
write_journal_phase STATE_COMMITTING
crash_point state-committing
atomic_install "$next_dir/retained.env" "$active_slot_state"
crash_point after-retained-state
atomic_install "$next_dir/retained.retain-until" "$active_slot_deadline"
crash_point after-retained-deadline
atomic_install "$next_dir/current.env" "$current_state"
crash_point after-current-state
remove_persisted "$candidate_state" "$candidate_deadline"
crash_point after-candidate-state-removal
verify_committed_candidate_state
write_journal_phase NEW_COMMITTED
crash_point new-committed

# The shared recovery command re-verifies state, runtime, Caddy and the stable
# HTTPS entry before it removes maintenance and retires the durable journal.
recovery_invoked=1
PKUBA_RECOVERY_LOCK_HELD=1 bash "$recovery_command"
transaction_prepared=0
trap - EXIT

echo "PKUBA_DEPLOYMENT_RESULT=success"
echo "PKUBA_RELEASE_TAG=$release_tag"
echo "PKUBA_RELEASE_COMMIT=$release_commit"
echo "PKUBA_ACTIVE_SLOT=$candidate_slot"
echo "PKUBA_PREVIOUS_SLOT=$ACTIVE_SLOT"
echo "PKUBA_PREVIOUS_SLOT_RETAIN_UNTIL=$retain_until"
echo "PKUBA_BACKUP_DIR=$backup_dir"
echo "The previous application stack is retained until epoch $retain_until; its workers remain stopped."
echo "A normal rollback switches applications only. Paired data restoration requires a separate confirmed incident procedure."

# Keep the newest three *verified* paired rollback points. Both the FROM and TO
# worktrees are part of recoverability: the data snapshot selects FROM while
# release.json proves TO. An invalid SUCCESS directory is diagnostic evidence;
# it is retained and disables automatic worktree cleanup rather than being
# trusted or silently deleted.
set +e
declare -a retained_backups=() removable_backups=()
declare -A retained_release_tags=()
retained_release_tags[$release_tag]=1
retained_release_tags[$CURRENT_TAG]=1
backup_cleanup_safe=1
valid_backup_count=0
successful_backup_candidates=("$backup_dir" "${existing_successful_backup_candidates[@]}")
metadata_test_flag=()
[[ ${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} == 1 ]] \
  && metadata_test_flag=(--allow-test-root)
for backup_candidate in "${successful_backup_candidates[@]}"; do
  metadata=$(python3 "$backup_verifier" \
    --backup-dir "$backup_candidate" --backup-root "$backup_root" \
    --release-root "$release_root" --repository-dir "$repository_dir" \
    --identity-validator "$identity_validator" --metadata-only \
    "${metadata_test_flag[@]}" 2>/dev/null)
  if [[ $? -ne 0 ]]; then
    echo "Cleanup warning: retaining invalid paired backup: $backup_candidate" >&2
    backup_cleanup_safe=0
    continue
  fi
  IFS=$'\t' read -r verified_path from_tag _ to_tag _ _ <<<"$metadata"
  [[ $verified_path == "$backup_candidate" ]] || {
    echo "Cleanup warning: verifier returned a different backup path" >&2
    backup_cleanup_safe=0
    continue
  }
  if (( valid_backup_count < 3 )); then
    retained_backups+=("$backup_candidate")
    retained_release_tags[$from_tag]=1
    retained_release_tags[$to_tag]=1
    valid_backup_count=$((valid_backup_count + 1))
  else
    removable_backups+=("$backup_candidate")
  fi
done

for old_backup in "${removable_backups[@]}"; do
  resolved_backup=$(realpath -e -- "$old_backup")
  if [[ ! -d $resolved_backup || -L $old_backup \
    || $(dirname "$resolved_backup") != "$(realpath -e -- "$backup_root")" ]]; then
    echo "Cleanup warning: refusing unsafe backup path: $old_backup" >&2
    backup_cleanup_safe=0
    continue
  fi
  rm -rf -- "$resolved_backup" \
    || { echo "Cleanup warning: could not remove $resolved_backup" >&2; backup_cleanup_safe=0; }
done

if [[ $backup_cleanup_safe == 1 ]]; then
  for old_release in "$release_root"/v*.*.*; do
    [[ -d $old_release && ! -L $old_release ]] || continue
    old_tag=${old_release##*/}
    [[ -n ${retained_release_tags[$old_tag]:-} ]] && continue
    resolved_release=$(realpath -e -- "$old_release")
    [[ $(dirname "$resolved_release") == "$(realpath -e -- "$release_root")" ]] || {
      echo "Cleanup warning: refusing unsafe release path: $resolved_release" >&2
      continue
    }
    git -C "$repository_dir" worktree remove --force "$resolved_release" \
      || echo "Cleanup warning: could not remove release $old_tag" >&2
  done
  git -C "$repository_dir" worktree prune \
    || echo "Cleanup warning: git worktree prune failed" >&2
else
  echo "Cleanup warning: release worktrees were retained because backup validation was incomplete." >&2
fi
docker image prune --force \
  --filter "label=org.opencontainers.image.source=https://github.com/jiumao2/PKUBA_Miniprogram" \
  >/dev/null || echo "Cleanup warning: PKUBA image prune failed" >&2
set -e
