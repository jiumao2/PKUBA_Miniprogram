#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() { echo "production backup error: $*" >&2; exit 1; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -le 1 ]] || die "usage: backup-current-server.sh [daily|weekly]"
backup_kind=${1:-daily}
[[ $backup_kind == daily || $backup_kind == weekly ]] \
  || die "backup kind must be daily or weekly"

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # Root-owned paths and immutable image references only; never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
backup_root=${PKUBA_SCHEDULED_BACKUP_ROOT:-/opt/pkuba/production/backups}
data_project=${PKUBA_DATA_PROJECT:-pkuba-data}
postgres_image=${PKUBA_POSTGRES_IMAGE:-ghcr.io/jiumao2/pkuba-postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-prod-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-prod-archives}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for command_name in cut docker find head mkdir mv python3 realpath rm sha256sum sort sync tar; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose is unavailable"
[[ $postgres_image == ghcr.io/jiumao2/pkuba-postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73 ]] \
  || die "PostgreSQL must use the approved mirrored digest"
[[ -d $state_dir ]] || die "missing deployment state directory"

lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-/usr/local/libexec/pkuba/acquire-deploy-lock.py}
[[ -f $lock_helper ]] || lock_helper=$script_dir/acquire-deploy-lock.py
if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
  exec env PKUBA_DEPLOY_LOCK_HELD=1 \
    python3 "$lock_helper" --state-dir "$state_dir" --timeout 1800 -- \
    bash "$0" "$backup_kind"
fi

kind_root=$backup_root/$backup_kind
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$kind_root/$timestamp
mkdir -p "$kind_root"
[[ ! -e $backup_dir ]] || die "backup directory already exists"
mkdir "$backup_dir"

maintenance_file=$state_dir/maintenance.enabled
maintenance_was_present=0
[[ -e $maintenance_file ]] && maintenance_was_present=1
writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
[[ -f $writer_fence ]] || writer_fence=$script_dir/fence-deploy-writers.sh
application_start=${PKUBA_APPLICATION_START_COMMAND:-/usr/local/sbin/pkuba-start-current-application}
[[ -f $application_start ]] || application_start=$script_dir/start-current-application.sh

backup_complete=0
on_exit() {
  local status=$?
  trap - EXIT
  if [[ $status -ne 0 || $backup_complete != 1 ]]; then
    touch "$maintenance_file" 2>/dev/null || true
    sync -f "$state_dir" 2>/dev/null || true
    bash "$writer_fence" >/dev/null 2>&1 || true
    echo "Backup failed; maintenance remains enabled and both slots remain fenced." >&2
  fi
  exit "$status"
}
trap on_exit EXIT

touch "$maintenance_file"
sync -f "$state_dir"
bash "$writer_fence" || die "could not fence all application writers"

db_container=$(docker ps -q \
  --filter "label=com.docker.compose.project=$data_project" \
  --filter "label=com.docker.compose.service=db" | head -n 1)
[[ -n $db_container ]] || die "could not find the production PostgreSQL container"
[[ $(docker inspect --format '{{.State.Running}}' "$db_container") == true ]] \
  || die "production PostgreSQL is not running"
docker volume inspect "$media_volume" >/dev/null
docker volume inspect "$archive_volume" >/dev/null

docker exec "$db_container" sh -ec \
  'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$backup_dir/database.dump"
docker exec -i "$db_container" sh -ec 'pg_restore --list >/dev/null' \
  <"$backup_dir/database.dump"
docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'tar -C /source -czf /backup/private-media.tar.gz .; cd /source; find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/private-media.files.sha256'
docker run --rm --entrypoint sh \
  -v "$archive_volume:/source:ro" -v "$backup_dir:/backup" "$postgres_image" \
  -ec 'tar -C /source -czf /backup/archive-staging.tar.gz .; cd /source; find . -type f -exec sha256sum "{}" ";" | sort -k2 > /backup/archive-staging.files.sha256'
tar -tzf "$backup_dir/private-media.tar.gz" >/dev/null
tar -tzf "$backup_dir/archive-staging.tar.gz" >/dev/null

cat >"$backup_dir/MANIFEST.env" <<EOF
MANIFEST_VERSION=1
BACKUP_KIND=$backup_kind
CREATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
DATA_PROJECT=$data_project
POSTGRES_IMAGE=$postgres_image
MEDIA_VOLUME=$media_volume
ARCHIVE_VOLUME=$archive_volume
EOF
(
  cd "$backup_dir"
  sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
    private-media.files.sha256 archive-staging.files.sha256 MANIFEST.env \
    >SHA256SUMS.tmp
  sha256sum --check SHA256SUMS.tmp >/dev/null
  sync -f database.dump private-media.tar.gz archive-staging.tar.gz \
    private-media.files.sha256 archive-staging.files.sha256 MANIFEST.env \
    SHA256SUMS.tmp
  mv -f SHA256SUMS.tmp SHA256SUMS
  sync -f SHA256SUMS
  printf 'MANIFEST_SHA256=%s\nCOMMITTED_AT=%s\n' \
    "$(sha256sum SHA256SUMS | cut -d' ' -f1)" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >SUCCESS.tmp
  sync -f SUCCESS.tmp
  mv -f SUCCESS.tmp SUCCESS
  sync -f SUCCESS
)
sync -f "$backup_dir"
sync -f "$kind_root"

# These scheduled restore points are separate from the newest three release
# rollback points kept under the deployment transaction directory.
keep_count=5
[[ $backup_kind == weekly ]] && keep_count=4
if ! committed_output=$(find "$kind_root" -mindepth 2 -maxdepth 2 \
  -type f -name SUCCESS -printf '%T@ %h\n' | sort -nr | cut -d' ' -f2-); then
  die "could not enumerate committed $backup_kind backups"
fi
committed_backups=()
if [[ -n $committed_output ]]; then
  mapfile -t committed_backups <<<"$committed_output"
fi
for ((index=keep_count; index<${#committed_backups[@]}; index++)); do
  old_backup=${committed_backups[$index]}
  resolved=$(realpath -e -- "$old_backup")
  [[ -d $resolved && ! -L $old_backup \
    && $(dirname "$resolved") == "$(realpath -e -- "$kind_root")" ]] \
    || die "refusing unsafe retention target: $old_backup"
  (cd "$resolved" && sha256sum --check SHA256SUMS >/dev/null) \
    || die "refusing to remove an invalid committed backup: $resolved"
  rm -rf -- "$resolved"
done
sync -f "$kind_root"

if [[ $maintenance_was_present == 0 ]]; then
  PKUBA_DEPLOY_LOCK_HELD=1 PKUBA_START_UNDER_MAINTENANCE=1 \
    bash "$application_start" \
    || die "backup completed but the authoritative application did not restart"
  rm -f "$maintenance_file"
  sync -f "$state_dir"
fi
backup_complete=1
trap - EXIT
echo "Production $backup_kind backup completed: $backup_dir"
