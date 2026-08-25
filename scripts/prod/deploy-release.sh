#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

echo "deployment error: the in-place deploy path is retired; use pkuba-deploy-blue-green" >&2
exit 64

die() {
  echo "deployment error: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 4 ]] || die "usage: deploy-release.sh TAG COMMIT API_IMAGE WEB_IMAGE"

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
  # The file is created by bootstrap-server.sh, owned by root and never sourced from Git.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/repository}
runtime_dir=${PKUBA_RUNTIME_DIR:-/opt/pkuba/ip-test}
compose_project=${PKUBA_COMPOSE_PROJECT:-pkuba-ip-test}
env_file=${PKUBA_ENV_FILE:-$runtime_dir/.env}
state_dir=$deploy_root/state
release_root=$deploy_root/releases
backup_root=$deploy_root/backups
log_root=$deploy_root/logs
current_state=$state_dir/current.env
lock_file=${PKUBA_DEPLOY_LOCK_FILE:-/var/lock/pkuba-deploy.lock}
minimum_headroom_bytes=${PKUBA_DEPLOY_MIN_HEADROOM_BYTES:-2147483648}
preflight_wait_seconds=${PKUBA_DEPLOY_PREFLIGHT_WAIT_SECONDS:-900}
enforce_data_gate=${PKUBA_ENFORCE_DATA_GATE:-1}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}

for command_name in curl df docker flock git realpath sha256sum; do
  require_command "$command_name"
done
docker compose version >/dev/null 2>&1 || die "docker compose is unavailable"

[[ -f $env_file ]] || die "missing server environment file: $env_file"
[[ -d $repository_dir/.git ]] || die "missing read-only repository: $repository_dir"
[[ -f $current_state ]] || die "missing deployment state: $current_state"

mkdir -p "$state_dir" "$release_root" "$backup_root" "$log_root"
touch "$lock_file"
exec 9>"$lock_file"
flock -w 1800 9 || die "another deployment still owns the server lock"

# The state file is generated only by this root-owned script or bootstrap-server.sh.
# shellcheck disable=SC1090
source "$current_state"
: "${CURRENT_TAG:?missing CURRENT_TAG in $current_state}"
: "${CURRENT_COMMIT:?missing CURRENT_COMMIT in $current_state}"
: "${CURRENT_API_IMAGE:?missing CURRENT_API_IMAGE in $current_state}"
: "${CURRENT_WEB_IMAGE:?missing CURRENT_WEB_IMAGE in $current_state}"
: "${CURRENT_RELEASE_DIR:?missing CURRENT_RELEASE_DIR in $current_state}"

[[ $CURRENT_RELEASE_DIR == "$release_root"/* ]] \
  || die "current release directory is outside $release_root"
[[ -f $CURRENT_RELEASE_DIR/infra/compose.prod.yml ]] \
  || die "current release has no production Compose file"

echo "Fetching $release_tag from the read-only production checkout."
git -C "$repository_dir" fetch --force origin main \
  "+refs/tags/$release_tag:refs/tags/$release_tag"
resolved_commit=$(git -C "$repository_dir" rev-parse "$release_tag^{commit}")
[[ $resolved_commit == "$release_commit" ]] || die "tag does not resolve to requested commit"
git -C "$repository_dir" merge-base --is-ancestor "$release_commit" origin/main \
  || die "release commit is not reachable from origin/main"

release_dir=$release_root/$release_tag
if [[ -e $release_dir ]]; then
  [[ -d $release_dir/.git || -f $release_dir/.git ]] || die "invalid existing release directory"
  [[ $(git -C "$release_dir" rev-parse HEAD) == "$release_commit" ]] \
    || die "existing release directory points to another commit"
else
  git -C "$repository_dir" worktree add --detach "$release_dir" "$release_commit"
fi

compose_release() {
  local directory=$1
  local target_api_image=$2
  local target_web_image=$3
  local target_tag=$4
  local target_commit=$5
  shift 5
  env \
    PKUBA_API_IMAGE="$target_api_image" \
    PKUBA_WEB_IMAGE="$target_web_image" \
    PKUBA_RELEASE_TAG="$target_tag" \
    PKUBA_GIT_COMMIT="$target_commit" \
    PKUBA_DEPLOY_STATE_DIR="$state_dir" \
    PKUBA_ENV_FILE="$env_file" \
    COMPOSE_PROFILES="$([[ $email_profile == 1 ]] && echo email || true)" \
    docker compose \
      --project-name "$compose_project" \
      --project-directory "$directory" \
      --env-file "$env_file" \
      -f "$directory/infra/compose.prod.yml" \
      "$@"
}

compose_new() {
  compose_release "$release_dir" "$api_image" "$web_image" "$release_tag" \
    "$release_commit" "$@"
}

compose_previous() {
  compose_release "$CURRENT_RELEASE_DIR" "$CURRENT_API_IMAGE" "$CURRENT_WEB_IMAGE" \
    "$CURRENT_TAG" "$CURRENT_COMMIT" "$@"
}

database_dump() {
  local destination=$1
  compose_new exec -T db sh -ec \
    'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    >"$destination"
  [[ -s $destination ]] || die "database dump is empty: $destination"
  compose_new exec -T db sh -ec 'pg_restore --list >/dev/null' <"$destination"
}

restore_database() {
  local source_dump=$1
  compose_new exec -T db sh -ec '
    dropdb --if-exists --force -U "$POSTGRES_USER" "$POSTGRES_DB"
    createdb -U "$POSTGRES_USER" "$POSTGRES_DB"
    pg_restore --exit-on-error --no-owner --no-privileges \
      -U "$POSTGRES_USER" -d "$POSTGRES_DB"
  ' <"$source_dump"
}

write_current_state() {
  local temporary=$current_state.tmp
  {
    printf 'CURRENT_TAG=%q\n' "$release_tag"
    printf 'CURRENT_COMMIT=%q\n' "$release_commit"
    printf 'CURRENT_API_IMAGE=%q\n' "$api_image"
    printf 'CURRENT_WEB_IMAGE=%q\n' "$web_image"
    printf 'CURRENT_RELEASE_DIR=%q\n' "$release_dir"
  } >"$temporary"
  chmod 600 "$temporary"
  mv -f "$temporary" "$current_state"
}

services=(api expiry scoresheet-worker archive-worker)
if [[ $email_profile == 1 ]]; then
  services+=(outbox)
fi

stop_writers() {
  compose_new stop --timeout 45 "${services[@]}" >/dev/null
}

wait_for_services() {
  declare -A restart_counts=()
  local service
  for service in "$@"; do
    local container_id
    container_id=$(compose_new ps -q "$service")
    [[ -n $container_id ]] || die "service has no container: $service"
    [[ $(docker inspect --format '{{.State.Running}}' "$container_id") == true ]] \
      || die "service is not running: $service"
    local health
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$container_id")
    [[ -z $health || $health == healthy ]] || die "service is not healthy: $service ($health)"
    restart_counts[$service]=$(docker inspect --format '{{.RestartCount}}' "$container_id")
  done
  sleep 10
  for service in "$@"; do
    local container_id
    container_id=$(compose_new ps -q "$service")
    [[ -n $container_id ]] || die "service disappeared during stability check: $service"
    local later_restart_count
    later_restart_count=$(docker inspect --format '{{.RestartCount}}' "$container_id")
    [[ $later_restart_count -eq ${restart_counts[$service]} ]] \
      || die "service restarted during deployment: $service"
  done
}

maintenance_file=$state_dir/maintenance.enabled
deployment_log=$log_root/$(date -u +%Y%m%dT%H%M%SZ)-$release_tag.log
exec > >(tee -a "$deployment_log") 2>&1

echo "Deploying $release_tag ($release_commit)."
echo "Previous release: $CURRENT_TAG ($CURRENT_COMMIT)."

compose_new config --quiet
compose_new up -d --no-deps db
compose_new pull api caddy expiry scoresheet-worker archive-worker
docker image inspect "$api_image" >/dev/null
docker image inspect "$web_image" >/dev/null

database_bytes=$(compose_new exec -T db sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT pg_database_size(current_database())"')
[[ $database_bytes =~ ^[0-9]+$ ]] || die "could not determine database size"
available_bytes=$(df -PB1 "$deploy_root" | awk 'NR == 2 {print $4}')
required_bytes=$((database_bytes * 3 + minimum_headroom_bytes))
(( available_bytes >= required_bytes )) \
  || die "insufficient disk space: available=$available_bytes required=$required_bytes"

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$backup_root/$timestamp-pre-$release_tag
mkdir -p "$backup_dir"
cp "$current_state" "$backup_dir/previous-release.env"

echo "Waiting for recognition, archive, purge, edit and expiry work to become idle."
compose_new run --rm --no-deps api python manage.py deployment_preflight \
  "--wait-seconds=$preflight_wait_seconds" --poll-seconds=5 --json \
  | tee "$backup_dir/preflight.json"
if [[ $enforce_data_gate == 1 ]]; then
  compose_new run --rm --no-deps api python manage.py check_no_synthetic_public_data
fi

echo "Creating online preliminary database dump."
database_dump "$backup_dir/database-online.dump"
sha256sum "$backup_dir/database-online.dump" >"$backup_dir/database-online.dump.sha256"

rollback_required=0
rollback_succeeded=0

rollback_release() {
  local original_status=$1
  trap - EXIT
  set +e
  echo "Deployment failed; restoring $CURRENT_TAG from the final rollback point." >&2
  touch "$maintenance_file"
  stop_writers
  local restore_status=$?
  if [[ $restore_status -eq 0 ]]; then
    restore_database "$backup_dir/database-final.dump"
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    compose_previous up -d --no-deps --wait --wait-timeout 180 api
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    compose_previous up -d --no-deps --wait --wait-timeout 180 \
      expiry scoresheet-worker archive-worker
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 && $email_profile == 1 ]]; then
    compose_previous up -d --no-deps --wait --wait-timeout 180 outbox
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    compose_previous up -d --no-deps caddy
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    compose_previous exec -T api python manage.py check
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    compose_previous exec -T api python -c \
      "import http.client,sys; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=5); c.request('GET','/api/v1/health',headers={'Host':'api','X-Forwarded-Proto':'https'}); r=c.getresponse(); r.read(); c.close(); sys.exit(0 if r.status == 200 else 1)"
    restore_status=$?
  fi
  if [[ $restore_status -eq 0 ]]; then
    cp "$backup_dir/previous-release.env" "$current_state"
    chmod 600 "$current_state"
    rm -f "$maintenance_file"
    printf '%s\n' "rollback=$CURRENT_TAG" "failed_release=$release_tag" \
      >"$backup_dir/ROLLBACK_COMPLETED"
    rollback_succeeded=1
    echo "Rollback completed. Production is again serving $CURRENT_TAG." >&2
  else
    touch "$maintenance_file"
    printf '%s\n' "rollback_failed=$CURRENT_TAG" "failed_release=$release_tag" \
      >"$backup_dir/ROLLBACK_FAILED"
    echo "ROLLBACK FAILED. Maintenance mode remains enabled." >&2
    echo "Recovery dump: $backup_dir/database-final.dump" >&2
  fi
  exit "$original_status"
}

on_exit() {
  local status=$?
  if [[ $status -ne 0 && $rollback_required -eq 1 && $rollback_succeeded -eq 0 ]]; then
    rollback_release "$status"
  fi
  exit "$status"
}
trap on_exit EXIT

echo "Enabling maintenance mode and stopping all writers."
touch "$maintenance_file"
stop_writers

echo "Creating authoritative final rollback dump."
database_dump "$backup_dir/database-final.dump"
sha256sum "$backup_dir/database-final.dump" >"$backup_dir/database-final.dump.sha256"
rollback_required=1

echo "Applying database migrations from $release_tag."
compose_new run --rm --no-deps api python manage.py migrate --noinput
compose_new run --rm --no-deps api python manage.py check --deploy
compose_new run --rm --no-deps api python manage.py showmigrations core --plan \
  >"$backup_dir/core-migrations.txt"

echo "Starting the new API behind maintenance mode."
compose_new up -d --no-deps --wait --wait-timeout 180 api
compose_new exec -T api python -c \
  "import http.client,sys; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=5); c.request('GET','/api/v1/health/ready',headers={'Host':'api','X-Forwarded-Proto':'https'}); r=c.getresponse(); body=r.read(); c.close(); sys.exit(0 if r.status == 200 and b'$release_tag' in body else 1)"
compose_new exec -T api python -c \
  "import http.client,sys; c=http.client.HTTPConnection('127.0.0.1',8000,timeout=5); c.request('GET','/api/v1/public/season',headers={'Host':'api','X-Forwarded-Proto':'https'}); r=c.getresponse(); r.read(); c.close(); sys.exit(0 if r.status in (200,404) else 1)"

echo "Starting the new web image and background workers."
compose_new up -d --no-deps caddy
compose_new up -d --no-deps --wait --wait-timeout 180 \
  expiry scoresheet-worker archive-worker
if [[ $email_profile == 1 ]]; then
  compose_new up -d --no-deps --wait --wait-timeout 180 outbox
fi
wait_for_services "${services[@]}" caddy

public_domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r"')
[[ $public_domain =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid PKUBA_DOMAIN in server env"

echo "Checking the new API and web image through production HTTPS while writes remain blocked."
api_probe=$(curl --fail --silent --show-error --retry 12 --retry-delay 2 \
  --retry-all-errors "https://api.$public_domain/api/v1/health/ready")
[[ $api_probe == *"$release_tag"* ]] || die "public API probe returned another release"
web_probe=$(curl --fail --silent --show-error --retry 12 --retry-delay 2 \
  --retry-all-errors "https://admin.$public_domain/_deployment/ready")
[[ $web_probe == *"$release_tag"* ]] || die "public web probe returned another release"

write_current_state
rm -f "$maintenance_file"

echo "Checking the public application after leaving maintenance mode."
post_ready=$(curl --fail --silent --show-error --retry 6 --retry-delay 2 \
  --retry-all-errors "https://api.$public_domain/api/v1/health/ready")
[[ $post_ready == *"$release_tag"* ]] || die "post-cutover API probe returned another release"
public_status=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  --retry 6 --retry-delay 2 --retry-all-errors \
  "https://api.$public_domain/api/v1/public/season")
[[ $public_status == 200 || $public_status == 404 ]] \
  || die "post-cutover public season probe failed: HTTP $public_status"
curl --fail --silent --show-error --retry 6 --retry-delay 2 --retry-all-errors \
  "https://admin.$public_domain/" >/dev/null

{
  printf '{\n'
  printf '  "tag": "%s",\n' "$release_tag"
  printf '  "commit": "%s",\n' "$release_commit"
  printf '  "api_image": "%s",\n' "$api_image"
  printf '  "web_image": "%s",\n' "$web_image"
  printf '  "deployed_at": "%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '}\n'
} >"$backup_dir/release.json"
sha256sum "$backup_dir"/*.dump "$backup_dir/release.json" >"$backup_dir/SHA256SUMS"
touch "$backup_dir/SUCCESS"

rollback_required=0
echo "PKUBA_DEPLOYMENT_RESULT=success"
echo "PKUBA_RELEASE_TAG=$release_tag"
echo "PKUBA_RELEASE_COMMIT=$release_commit"
echo "PKUBA_BACKUP_DIR=$backup_dir"

# Keep the three newest successful rollback points. Failed deployments are never
# removed automatically. The realpath guard prevents a malformed path from
# widening deletion beyond this deployment's backup directory.
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
    's/^[[:space:]]*"tag": "\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)",*$/\1/p' \
    "$retained_backup/release.json" | head -n 1)
  if [[ -n $retained_tag ]]; then
    retained_release_tags[$retained_tag]=1
  fi
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
