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

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/repository}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/ip-test/.env}
state_dir=$deploy_root/state
slot_state_dir=$state_dir/slots
release_root=$deploy_root/releases
backup_root=$deploy_root/backups
log_root=$deploy_root/logs
current_state=$state_dir/current.env
lock_file=${PKUBA_DEPLOY_LOCK_FILE:-/var/lock/pkuba-deploy.lock}
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-production}
data_project=${PKUBA_DATA_PROJECT:-pkuba-data}
gateway_project=${PKUBA_GATEWAY_PROJECT:-pkuba-gateway}
postgres_volume=${PKUBA_POSTGRES_VOLUME:-pkuba-ip-test_postgres-data}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-ip-test_private-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-ip-test_archive-staging}
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
preflight_wait_seconds=${PKUBA_DEPLOY_PREFLIGHT_WAIT_SECONDS:-900}
retention_seconds=${PKUBA_OLD_SLOT_RETENTION_SECONDS:-86400}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
automation_armed=${PKUBA_PRODUCTION_AUTOMATION_ARMED:-0}

[[ $automation_armed == 1 ]] \
  || die "production automation is not armed; complete the isolated blue/green rehearsal first"

for command_name in awk curl df docker find flock git head realpath sed seq sha256sum sort tar; do
  require_command "$command_name"
done
docker compose version >/dev/null 2>&1 || die "docker compose is unavailable"

[[ -f $env_file ]] || die "missing server environment file: $env_file"
[[ -d $repository_dir/.git ]] || die "missing read-only repository: $repository_dir"
[[ -f $current_state ]] || die "missing blue/green state: $current_state"

mkdir -p "$state_dir" "$slot_state_dir" "$release_root" "$backup_root" "$log_root"
touch "$lock_file"
exec 9>"$lock_file"
flock -w 1800 9 || die "another deployment still owns the server lock"

# The state file is written atomically by this script or the one-time bootstrap.
# shellcheck disable=SC1090
source "$current_state"
: "${ACTIVE_SLOT:?missing ACTIVE_SLOT in $current_state}"
: "${CURRENT_TAG:?missing CURRENT_TAG in $current_state}"
: "${CURRENT_COMMIT:?missing CURRENT_COMMIT in $current_state}"
: "${CURRENT_API_IMAGE:?missing CURRENT_API_IMAGE in $current_state}"
: "${CURRENT_WEB_IMAGE:?missing CURRENT_WEB_IMAGE in $current_state}"
: "${CURRENT_RELEASE_DIR:?missing CURRENT_RELEASE_DIR in $current_state}"
[[ $ACTIVE_SLOT == blue || $ACTIVE_SLOT == green ]] || die "invalid ACTIVE_SLOT"
[[ $CURRENT_RELEASE_DIR == "$release_root"/* ]] \
  || die "current release directory is outside $release_root"
[[ -f $CURRENT_RELEASE_DIR/infra/compose.prod.slot.yml ]] \
  || die "current release has no blue/green slot Compose file"

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
  infra/release-contract.env; do
  [[ -f $release_dir/$required_file ]] || die "release is missing $required_file"
done

# shellcheck disable=SC1090
source "$release_dir/infra/release-contract.env"
[[ ${PKUBA_PREVIOUS_APP_COMPATIBLE:-0} == 1 ]] \
  || die "release is not approved for old/new application coexistence; establish a tested baseline first"

candidate_state=$slot_state_dir/$candidate_slot.env
if [[ -f $candidate_state ]]; then
  # shellcheck disable=SC1090
  source "$candidate_state"
  if [[ ${RETAIN_UNTIL_EPOCH:-0} =~ ^[0-9]+$ ]] \
    && (( $(date +%s) < RETAIN_UNTIL_EPOCH )); then
    die "$candidate_slot still contains the 24-hour rollback stack"
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
  docker run --rm --entrypoint sh -v "$1:/source:ro" postgres:17-alpine \
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
required_bytes=$((payload_bytes * 115 / 100 + reserve_bytes))
(( available_bytes >= required_bytes )) \
  || die "insufficient disk space: available=$available_bytes required=$required_bytes"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$backup_root/$timestamp-pre-$release_tag
mkdir -p "$backup_dir"
cp "$current_state" "$backup_dir/previous-release.env"

maintenance_file=$state_dir/maintenance.enabled
cutover_complete=0
candidate_started=0
active_writers_stopped=0

worker_services=(expiry scoresheet-worker archive-worker)
if [[ $email_profile == 1 ]]; then worker_services+=(outbox); fi

write_upstreams() {
  local slot=$1
  local temporary=$state_dir/upstreams.caddy.tmp
  cat >"$temporary" <<EOF
(active_api) {
	reverse_proxy pkuba-$slot-api:8000
}

(active_web) {
	reverse_proxy pkuba-$slot-web:8080
}
EOF
  chmod 644 "$temporary"
  mv -f "$temporary" "$state_dir/upstreams.caddy"
}

reload_gateway() {
  compose_gateway "$release_dir" exec -T gateway \
    caddy reload --config /etc/caddy/Caddyfile
}

wait_for_candidate() {
  local api_port web_port attempt body
  api_port=$(slot_api_port "$candidate_slot")
  web_port=$(slot_web_port "$candidate_slot")
  for attempt in $(seq 1 60); do
    body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$api_port/api/v1/health/ready" || true)
    if [[ $body == *'"status": "ok"'* || $body == *'"status":"ok"'* ]]; then
      [[ $body == *"$release_tag"* && $body == *"$release_commit"* ]] \
        || die "candidate readiness belongs to another release"
      curl --fail --silent --show-error \
        "http://127.0.0.1:$web_port/_deployment/ready" >/dev/null
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
  sleep 10
  for service in "${services[@]}"; do
    container=$(compose_candidate ps -q "$service")
    [[ -n $container ]] || die "candidate service disappeared: $service"
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${restart_counts[$service]} ]] \
      || die "candidate service restarted during stability check: $service"
  done
}

rollback_application_only() {
  local original_status=$1
  trap - EXIT
  set +e
  echo "Candidate failed; switching application traffic back without restoring data." >&2
  touch "$maintenance_file"
  if [[ $cutover_complete == 1 ]]; then
    write_upstreams "$ACTIVE_SLOT"
    reload_gateway
  fi
  if [[ $candidate_started == 1 ]]; then
    compose_candidate stop --timeout 60 api "${worker_services[@]}" >/dev/null
  fi
  compose_active up -d --no-deps web
  compose_active up -d --no-deps "${worker_services[@]}"
  compose_active up -d --no-deps api
  old_api_port=$(slot_api_port "$ACTIVE_SLOT")
  rollback_body=
  for _ in $(seq 1 60); do
    rollback_body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$old_api_port/api/v1/health/ready" || true)
    [[ $rollback_body == *"$CURRENT_TAG"* && $rollback_body == *'"status":"ok"'* ]] && break
    [[ $rollback_body == *"$CURRENT_TAG"* && $rollback_body == *'"status": "ok"'* ]] && break
    sleep 3
  done
  if [[ $rollback_body == *"$CURRENT_TAG"* ]] \
    && [[ $rollback_body == *'"status":"ok"'* || $rollback_body == *'"status": "ok"'* ]]; then
    rm -f "$maintenance_file"
    printf '%s\n' \
      "rollback_type=application_only" \
      "active_release=$CURRENT_TAG" \
      "failed_release=$release_tag" \
      "database_restored=0" \
      "media_restored=0" \
      >"$backup_dir/APPLICATION_ROLLBACK_COMPLETED"
    echo "Application rollback completed; DB/media/archive were not restored." >&2
  else
    touch "$maintenance_file"
    printf '%s\n' \
      "rollback_type=application_only" \
      "failed_release=$release_tag" \
      >"$backup_dir/APPLICATION_ROLLBACK_FAILED"
    echo "APPLICATION ROLLBACK FAILED. Maintenance remains enabled." >&2
  fi
  exit "$original_status"
}

on_exit() {
  local status=$?
  if [[ $status -ne 0 && $active_writers_stopped == 1 ]]; then
    rollback_application_only "$status"
  fi
  exit "$status"
}
trap on_exit EXIT

echo "Entering the bounded writer fence."
touch "$maintenance_file"
active_writers_stopped=1
compose_active stop --timeout 60 api "${worker_services[@]}" >/dev/null

echo "Creating one paired DB/media/archive rollback point."
compose_data "$release_dir" exec -T db sh -ec \
  'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$backup_dir/database.dump"
[[ -s $backup_dir/database.dump ]] || die "database dump is empty"
compose_data "$release_dir" exec -T db sh -ec 'pg_restore --list >/dev/null' \
  <"$backup_dir/database.dump"
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$backup_dir:/backup" postgres:17-alpine \
  -ec 'tar -C /source -czf /backup/private-media.tar.gz .'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$backup_dir:/backup" postgres:17-alpine \
  -ec 'tar -C /source -czf /backup/archive-staging.tar.gz .'
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$backup_dir:/backup" postgres:17-alpine \
  -ec 'cd /source && find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/private-media.files.sha256'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$backup_dir:/backup" postgres:17-alpine \
  -ec 'cd /source && find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/archive-staging.files.sha256'
tar -tzf "$backup_dir/private-media.tar.gz" >/dev/null
tar -tzf "$backup_dir/archive-staging.tar.gz" >/dev/null
cat >"$backup_dir/MANIFEST.env" <<EOF
CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
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
    private-media.files.sha256 archive-staging.files.sha256 MANIFEST.env \
    >SHA256SUMS
  sha256sum --check SHA256SUMS
)

echo "Applying compatible migrations from the candidate image."
compose_candidate run --rm --no-deps api python manage.py migrate --noinput
compose_candidate run --rm --no-deps api python manage.py check --deploy
compose_candidate run --rm --no-deps api python manage.py showmigrations core --plan \
  >"$backup_dir/core-migrations.txt"

echo "Starting the isolated candidate application and candidate workers."
compose_candidate up -d --no-deps web "${worker_services[@]}"
compose_candidate up -d --no-deps api
candidate_started=1
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

echo "Atomically switching the stable gateway to $candidate_slot."
write_upstreams "$candidate_slot"
reload_gateway
cutover_complete=1
rm -f "$maintenance_file"

public_domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r"')
[[ $public_domain =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid PKUBA_DOMAIN"
api_probe=$(curl --fail --silent --show-error --retry 6 --retry-delay 2 \
  --retry-all-errors "https://api.$public_domain/api/v1/health/ready")
[[ $api_probe == *"$release_tag"* && $api_probe == *"$release_commit"* ]] \
  || die "public API probe returned another release"
curl --fail --silent --show-error --retry 6 --retry-delay 2 --retry-all-errors \
  "https://admin.$public_domain/" >/dev/null

switched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
retain_until=$(( $(date +%s) + retention_seconds ))
temporary_state=$current_state.tmp
cat >"$temporary_state" <<EOF
ACTIVE_SLOT=$candidate_slot
CURRENT_TAG=$release_tag
CURRENT_COMMIT=$release_commit
CURRENT_API_IMAGE=$api_image
CURRENT_WEB_IMAGE=$web_image
CURRENT_RELEASE_DIR=$release_dir
SWITCHED_AT=$switched_at
EOF
chmod 600 "$temporary_state"
mv -f "$temporary_state" "$current_state"
cat >"$slot_state_dir/$ACTIVE_SLOT.env" <<EOF
SLOT=$ACTIVE_SLOT
TAG=$CURRENT_TAG
COMMIT=$CURRENT_COMMIT
RETAIN_UNTIL_EPOCH=$retain_until
RETAIN_REASON=application_rollback
EOF
chmod 600 "$slot_state_dir/$ACTIVE_SLOT.env"

cat >"$backup_dir/release.json" <<EOF
{"tag":"$release_tag","commit":"$release_commit","slot":"$candidate_slot","previous_slot":"$ACTIVE_SLOT","switched_at":"$switched_at"}
EOF
sha256sum "$backup_dir/release.json" >>"$backup_dir/SHA256SUMS"
touch "$backup_dir/SUCCESS"

active_writers_stopped=0
trap - EXIT

echo "PKUBA_DEPLOYMENT_RESULT=success"
echo "PKUBA_RELEASE_TAG=$release_tag"
echo "PKUBA_RELEASE_COMMIT=$release_commit"
echo "PKUBA_ACTIVE_SLOT=$candidate_slot"
echo "PKUBA_PREVIOUS_SLOT=$ACTIVE_SLOT"
echo "PKUBA_PREVIOUS_SLOT_RETAIN_UNTIL=$retain_until"
echo "PKUBA_BACKUP_DIR=$backup_dir"
echo "The previous application stack is retained for 24 hours; its workers remain stopped."
echo "A normal rollback switches applications only. Paired data restoration requires a separate confirmed incident procedure."

# Keep exactly the three newest successful paired rollback points. Failed
# deployments are diagnostic evidence and are never removed automatically.
# CURRENT_TAG is the just-retained previous application, so both live slots
# remain protected even when their release is older than the newest three.
set +e
mapfile -t newest_successful_backups < <(
  find "$backup_root" -mindepth 2 -maxdepth 2 -type f -name SUCCESS \
    -printf '%T@ %h\n' | sort -nr | awk 'NR <= 3 {$1=""; sub(/^ /, ""); print}'
)
mapfile -t old_successful_backups < <(
  find "$backup_root" -mindepth 2 -maxdepth 2 -type f -name SUCCESS \
    -printf '%T@ %h\n' | sort -nr | awk 'NR > 3 {$1=""; sub(/^ /, ""); print}'
)
for old_backup in "${old_successful_backups[@]}"; do
  resolved_backup=$(realpath "$old_backup")
  if [[ $resolved_backup != "$backup_root"/* ]]; then
    echo "Cleanup warning: refusing unsafe backup path: $resolved_backup" >&2
    continue
  fi
  rm -rf -- "$resolved_backup" \
    || echo "Cleanup warning: could not remove $resolved_backup" >&2
done

declare -A retained_release_tags=()
retained_release_tags[$release_tag]=1
retained_release_tags[$CURRENT_TAG]=1
for retained_backup in "${newest_successful_backups[@]}"; do
  [[ -f $retained_backup/release.json ]] || continue
  retained_tag=$(sed -n \
    's/^[[:space:]]*{"tag":"\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*$/\1/p' \
    "$retained_backup/release.json" | head -n 1)
  [[ -n $retained_tag ]] && retained_release_tags[$retained_tag]=1
done
for old_release in "$release_root"/v*.*.*; do
  [[ -d $old_release ]] || continue
  old_tag=${old_release##*/}
  [[ -n ${retained_release_tags[$old_tag]:-} ]] && continue
  resolved_release=$(realpath "$old_release")
  if [[ $resolved_release != "$release_root"/* ]]; then
    echo "Cleanup warning: refusing unsafe release path: $resolved_release" >&2
    continue
  fi
  git -C "$repository_dir" worktree remove --force "$resolved_release" \
    || echo "Cleanup warning: could not remove release $old_tag" >&2
done
git -C "$repository_dir" worktree prune \
  || echo "Cleanup warning: git worktree prune failed" >&2
docker image prune --force \
  --filter "label=org.opencontainers.image.source=https://github.com/jiumao2/PKUBA_Miniprogram" \
  >/dev/null || echo "Cleanup warning: PKUBA image prune failed" >&2
set -e
