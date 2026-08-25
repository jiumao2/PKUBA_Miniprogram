#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() {
  echo "paired restore error: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  sudo restore-paired-data.sh BACKUP_DIR RESTORE_PAIRED_DATA

This command is only for a confirmed data-loss incident. It restores the
database, private media and archive staging from one deployment manifest.
Normal application rollback must never call it.

For an isolated rehearsal, set PKUBA_RESTORE_ISOLATED=1 together with explicit
PKUBA_RESTORE_DB_CONTAINER, PKUBA_POSTGRES_VOLUME, PKUBA_MEDIA_VOLUME,
PKUBA_ARCHIVE_VOLUME and PKUBA_DEPLOY_STATE_DIR values.
EOF
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 2 ]] || { usage >&2; exit 2; }
backup_dir=$(realpath "$1")
[[ $2 == RESTORE_PAIRED_DATA ]] || die "type RESTORE_PAIRED_DATA as the second argument"
[[ -d $backup_dir ]] || die "backup directory does not exist"

for command_name in curl docker flock grep head realpath seq sha256sum tar tee; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
docker compose version >/dev/null 2>&1 || die "docker compose is unavailable"

for required in \
  MANIFEST.env \
  SHA256SUMS \
  database.dump \
  private-media.tar.gz \
  private-media.files.sha256 \
  archive-staging.tar.gz \
  archive-staging.files.sha256 \
  previous-release.env; do
  [[ -f $backup_dir/$required ]] || die "backup is missing $required"
done
(grep -Eq '^[0-9a-f]{64}  previous-release\.env$' "$backup_dir/SHA256SUMS") \
  || die "backup checksum manifest does not protect previous-release.env"
(cd "$backup_dir" && sha256sum --check SHA256SUMS)

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # shellcheck disable=SC1090
  source "$config_file"
fi

isolated=${PKUBA_RESTORE_ISOLATED:-0}
deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/deploy}
release_root=${PKUBA_RELEASE_ROOT:-$deploy_root/releases}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
log_root=$deploy_root/logs
lock_file=${PKUBA_DEPLOY_LOCK_FILE:-/var/lock/pkuba-deploy.lock}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/ip-test/.env}
data_project=${PKUBA_DATA_PROJECT:-pkuba-data}
gateway_project=${PKUBA_GATEWAY_PROJECT:-pkuba-gateway}
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-production}
postgres_volume=${PKUBA_POSTGRES_VOLUME:-pkuba-ip-test_postgres-data}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-ip-test_private-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-ip-test_archive-staging}
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
current_state=$state_dir/current.env
maintenance_file=$state_dir/maintenance.enabled

# A paired deployment backup belongs to the application state captured before
# migration. Parse and validate that state before stopping services or touching
# the database/media/archive volumes. Backup contents are data, never shell.
state_parser=${PKUBA_RELEASE_STATE_PARSER:-/usr/local/libexec/pkuba-parse-release-state}
if [[ ! -x $state_parser ]]; then
  script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
  state_parser=$script_dir/parse-release-state.sh
fi
[[ -f $state_parser ]] || die "release state parser is unavailable"
parsed_state=$(bash "$state_parser" "$backup_dir/previous-release.env") \
  || die "previous release state is invalid"
IFS=$'\t' read -r \
  ACTIVE_SLOT CURRENT_TAG CURRENT_COMMIT CURRENT_API_IMAGE CURRENT_WEB_IMAGE CURRENT_RELEASE_DIR \
  <<<"$parsed_state"
expected_release_dir=$(realpath -m "$release_root/$CURRENT_TAG")
CURRENT_RELEASE_DIR=$(realpath -m "$CURRENT_RELEASE_DIR")
[[ $CURRENT_RELEASE_DIR == "$expected_release_dir" ]] \
  || die "previous release directory does not match its tag"
[[ -f $CURRENT_RELEASE_DIR/infra/compose.prod.slot.yml ]] \
  || die "matching application release is unavailable"

mkdir -p "$state_dir" "$log_root"
touch "$lock_file"
exec 9>"$lock_file"
flock -w 1800 9 || die "another deployment or restore owns the server lock"

for volume in "$postgres_volume" "$media_volume" "$archive_volume"; do
  docker volume inspect "$volume" >/dev/null || die "missing volume: $volume"
done

if [[ -n ${PKUBA_RESTORE_DB_CONTAINER:-} ]]; then
  db_container=$PKUBA_RESTORE_DB_CONTAINER
else
  db_container=$(docker ps -aq \
    --filter "label=com.docker.compose.project=$data_project" \
    --filter 'label=com.docker.compose.service=db' | head -n 1)
fi
[[ -n $db_container ]] || die "could not locate the PostgreSQL container"
[[ $(docker inspect --format '{{.State.Running}}' "$db_container") == true ]] \
  || die "PostgreSQL container is not running"

if [[ $isolated != 1 ]]; then
  [[ -f $current_state ]] || die "missing current deployment state"
  [[ -f $backup_dir/previous-release.env ]] \
    || die "backup cannot select its matching application release"
fi

restore_log=$log_root/$(date -u +%Y%m%dT%H%M%SZ)-paired-data-restore.log
exec > >(tee -a "$restore_log") 2>&1
touch "$maintenance_file"

restart_allowed=0
restore_complete=0
on_exit() {
  local status=$?
  if [[ $status -ne 0 || $restore_complete != 1 ]]; then
    touch "$maintenance_file"
    echo "Restore did not complete. Maintenance remains enabled." >&2
  fi
  if [[ $isolated == 1 && $restart_allowed == 1 ]]; then
    rm -f "$maintenance_file"
  fi
  exit "$status"
}
trap on_exit EXIT

writer_services=(api expiry scoresheet-worker archive-worker outbox)
if [[ $isolated != 1 ]]; then
  echo "Stopping both application slots before touching paired data."
  for slot in blue green; do
    project=pkuba-$slot
    for service in "${writer_services[@]}"; do
      container=$(docker ps -aq \
        --filter "label=com.docker.compose.project=$project" \
        --filter "label=com.docker.compose.service=$service" | head -n 1)
      if [[ -n $container && $(docker inspect --format '{{.State.Running}}' "$container") == true ]]; then
        docker stop --time 60 "$container" >/dev/null
      fi
    done
  done
fi

incident_dir=$deploy_root/incident-snapshots/$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$incident_dir"
echo "Preserving the pre-restore incident state at $incident_dir."
docker exec "$db_container" sh -ec \
  'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$incident_dir/database.dump"
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$incident_dir:/incident" postgres:17-alpine \
  -ec 'tar -C /source -czf /incident/private-media.tar.gz .'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$incident_dir:/incident" postgres:17-alpine \
  -ec 'tar -C /source -czf /incident/archive-staging.tar.gz .'
(
  cd "$incident_dir"
  sha256sum database.dump private-media.tar.gz archive-staging.tar.gz >SHA256SUMS
  sha256sum --check SHA256SUMS
)

echo "Restoring PostgreSQL from the verified deployment snapshot."
docker exec "$db_container" sh -ec \
  'dropdb --if-exists --force -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker exec -i "$db_container" sh -ec \
  'pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  <"$backup_dir/database.dump"
docker exec "$db_container" sh -ec \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT 1"' \
  | grep -qx 1

restore_volume() {
  local volume=$1 archive=$2 file_manifest=$3
  docker run --rm --entrypoint sh \
    -v "$volume:/target" -v "$backup_dir:/backup:ro" postgres:17-alpine \
    -ec "find /target -mindepth 1 -delete; tar -C /target -xzf /backup/$archive; cd /target; if [ -s /backup/$file_manifest ]; then sha256sum -c /backup/$file_manifest; else [ -z \"\$(find . -type f -print -quit)\" ]; fi"
}

echo "Restoring and verifying the private media volume."
restore_volume "$media_volume" private-media.tar.gz private-media.files.sha256
echo "Restoring and verifying the archive staging volume."
restore_volume "$archive_volume" archive-staging.tar.gz archive-staging.files.sha256

echo "Auditing restored season-scoped relationships before any service starts."
docker run --rm \
  --network "container:$db_container" \
  --env-file "$env_file" \
  -e PKUBA_RELEASE_TAG="$CURRENT_TAG" \
  -e PKUBA_GIT_COMMIT="$CURRENT_COMMIT" \
  "$CURRENT_API_IMAGE" \
  python manage.py audit_season_integrity --json \
  >"$incident_dir/season-integrity.json"

if [[ $isolated == 1 ]]; then
  restore_complete=1
  restart_allowed=1
  echo "PKUBA_PAIRED_RESTORE_RESULT=isolated-success"
  echo "PKUBA_INCIDENT_SNAPSHOT=$incident_dir"
  trap - EXIT
  rm -f "$maintenance_file"
  exit 0
fi

slot_api_port=$blue_api_port
[[ $ACTIVE_SLOT == green ]] && slot_api_port=$green_api_port
slot_web_port=$blue_web_port
[[ $ACTIVE_SLOT == green ]] && slot_web_port=$green_web_port
profile_args=()
[[ $email_profile == 1 ]] && profile_args=(--profile email)
compose_slot=(
  env
  PKUBA_SLOT_NAME="pkuba-$ACTIVE_SLOT"
  PKUBA_SLOT_API_PORT="$slot_api_port"
  PKUBA_SLOT_WEB_PORT="$slot_web_port"
  PKUBA_API_IMAGE="$CURRENT_API_IMAGE"
  PKUBA_WEB_IMAGE="$CURRENT_WEB_IMAGE"
  PKUBA_RELEASE_TAG="$CURRENT_TAG"
  PKUBA_GIT_COMMIT="$CURRENT_COMMIT"
  PKUBA_ENV_FILE="$env_file"
  PKUBA_MEDIA_VOLUME="$media_volume"
  PKUBA_ARCHIVE_VOLUME="$archive_volume"
  PKUBA_RUNTIME_NETWORK="$runtime_network"
  docker compose
  --project-name "pkuba-$ACTIVE_SLOT"
  --project-directory "$CURRENT_RELEASE_DIR"
  --env-file "$env_file"
  -f "$CURRENT_RELEASE_DIR/infra/compose.prod.slot.yml"
  "${profile_args[@]}"
)

services=(web expiry scoresheet-worker archive-worker)
[[ $email_profile == 1 ]] && services+=(outbox)
"${compose_slot[@]}" up -d --no-deps "${services[@]}"
"${compose_slot[@]}" up -d --no-deps api

ready_body=
for _ in $(seq 1 60); do
  ready_body=$(curl --silent --show-error \
    -H 'Host: api' -H 'X-Forwarded-Proto: https' \
    "http://127.0.0.1:$slot_api_port/api/v1/health/ready" || true)
  if [[ $ready_body == *"$CURRENT_TAG"* ]] \
    && [[ $ready_body == *'"status":"ok"'* || $ready_body == *'"status": "ok"'* ]]; then
    break
  fi
  sleep 3
done
[[ $ready_body == *"$CURRENT_TAG"* ]] || die "restored application did not become ready"

cat >"$state_dir/upstreams.caddy.tmp" <<EOF
(active_api) {
	reverse_proxy pkuba-$ACTIVE_SLOT-api:8000
}

(active_web) {
	reverse_proxy pkuba-$ACTIVE_SLOT-web:8080
}
EOF
chmod 644 "$state_dir/upstreams.caddy.tmp"
mv -f "$state_dir/upstreams.caddy.tmp" "$state_dir/upstreams.caddy"
env \
  PKUBA_DEPLOY_STATE_DIR="$state_dir" \
  PKUBA_RUNTIME_NETWORK="$runtime_network" \
  docker compose \
    --project-name "$gateway_project" \
    --project-directory "$CURRENT_RELEASE_DIR" \
    --env-file "$env_file" \
    -f "$CURRENT_RELEASE_DIR/infra/compose.prod.gateway.yml" \
    exec -T gateway caddy reload --config /etc/caddy/Caddyfile

cp "$backup_dir/previous-release.env" "$current_state.tmp"
printf 'DATA_RESTORED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$current_state.tmp"
chmod 600 "$current_state.tmp"
mv -f "$current_state.tmp" "$current_state"

restore_complete=1
restart_allowed=1
rm -f "$maintenance_file"
cat >"$incident_dir/RESTORE_COMPLETED" <<EOF
rollback_type=paired_data_and_matching_application
backup_dir=$backup_dir
active_slot=$ACTIVE_SLOT
active_tag=$CURRENT_TAG
database_restored=1
media_restored=1
archive_restored=1
EOF
echo "PKUBA_PAIRED_RESTORE_RESULT=success"
echo "PKUBA_ACTIVE_SLOT=$ACTIVE_SLOT"
echo "PKUBA_ACTIVE_TAG=$CURRENT_TAG"
echo "PKUBA_INCIDENT_SNAPSHOT=$incident_dir"
trap - EXIT
