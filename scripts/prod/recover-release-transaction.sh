#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() {
  echo "release recovery error: $*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 0 ]] || die "usage: recover-release-transaction.sh"

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # Root-owned paths and switches only; never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/production/.env}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
slot_state_dir=$state_dir/slots
release_root=$deploy_root/releases
log_root=$deploy_root/logs
transaction_dir=$state_dir/release-transaction
completed_transaction_dir=$state_dir/release-transaction-completed
journal_archive_root=$log_root/release-transactions
journal_file=$transaction_dir/journal.env
paired_restore_marker=$state_dir/paired-restore-incomplete.env
paired_transaction_dir=$state_dir/paired-restore-transaction
paired_completed_dir=$state_dir/paired-restore-completed
current_state=$state_dir/current.env
upstreams_file=$state_dir/upstreams.caddy
maintenance_file=$state_dir/maintenance.enabled
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
stability_seconds=${PKUBA_SERVICE_STABILITY_SECONDS:-10}
probe_attempts=${PKUBA_GATEWAY_PROBE_ATTEMPTS:-30}
probe_delay_seconds=${PKUBA_GATEWAY_PROBE_DELAY_SECONDS:-2}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for command_name in awk chmod cmp cp curl date dirname docker grep mkdir mv \
  python3 realpath rm sed seq sha256sum sleep sync tail tr; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done

[[ -d $state_dir ]] || die "missing pre-created deployment state directory"
lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-/usr/local/libexec/pkuba/acquire-deploy-lock.py}
if [[ ! -f $lock_helper ]]; then lock_helper=$script_dir/acquire-deploy-lock.py; fi
[[ -f $lock_helper ]] || die "missing secure deployment lock helper"
if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 && ${PKUBA_RECOVERY_LOCK_HELD:-0} != 1 ]]; then
  exec env \
    PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} \
    python3 "$lock_helper" --state-dir "$state_dir" --timeout 1800 -- \
    bash "$0" "$@"
fi
mkdir -p "$slot_state_dir" "$log_root" "$journal_archive_root"

writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
if [[ ! -x $writer_fence ]]; then writer_fence=$script_dir/fence-deploy-writers.sh; fi
prevalidation_required_file=$state_dir/release-recovery-required.env

fence_all_writers() { bash "$writer_fence"; }

record_prevalidation_failure() {
  local temporary=$prevalidation_required_file.tmp.$$
  cat >"$temporary" <<EOF
result=recovery_required
stage=pre_validation
failed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
database_restored=0
media_restored=0
archive_restored=0
EOF
  chmod 600 "$temporary" 2>/dev/null || return 1
  sync -f "$temporary" 2>/dev/null || return 1
  mv -f "$temporary" "$prevalidation_required_file" 2>/dev/null || return 1
  sync -f "$state_dir" 2>/dev/null || return 1
}

on_prevalidation_recovery_failure() {
  local status=$?
  trap - EXIT
  set +e
  touch "$maintenance_file"
  sync -f "$state_dir"
  fence_all_writers >/dev/null 2>&1
  record_prevalidation_failure
  echo "RELEASE RECOVERY PRE-VALIDATION FAILED. Maintenance, journals and the recovery requirement remain in place." >&2
  (( status != 0 )) || status=1
  exit "$status"
}

# Journal directory contents and identities are untrusted.  Their existence is
# the only fact used before the durable maintenance fence and the two-slot
# writer fence have both been established.  This ordering also covers corrupt,
# overlapping and partially committed journals after a host restart.
recovery_artifacts_present=0
for recovery_path in "$transaction_dir" "$completed_transaction_dir" \
  "$paired_transaction_dir" "$paired_completed_dir" "$paired_restore_marker" \
  "$prevalidation_required_file"; do
  if [[ -e $recovery_path || -L $recovery_path ]]; then
    recovery_artifacts_present=1
    break
  fi
done
if [[ $recovery_artifacts_present == 1 ]]; then
  trap on_prevalidation_recovery_failure EXIT
  touch "$maintenance_file"
  sync -f "$state_dir"
  [[ -f $writer_fence ]] || die "missing two-slot writer fence helper"
  fence_all_writers || die "could not establish the pre-validation two-slot writer fence"
fi

if [[ -e $paired_transaction_dir || -e $paired_completed_dir ]]; then
  [[ ! -e $transaction_dir && ! -e $completed_transaction_dir ]] \
    || { touch "$maintenance_file"; sync -f "$state_dir"; die "application and paired restore transactions overlap"; }
  touch "$maintenance_file"
  sync -f "$state_dir"
  paired_restore_command=${PKUBA_PAIRED_RESTORE_COMMAND:-/usr/local/sbin/pkuba-restore-paired-data}
  [[ -x $paired_restore_command ]] || paired_restore_command=$script_dir/restore-paired-data.sh
  [[ -f $paired_restore_command ]] || die "missing paired restore recovery command"
  PKUBA_DEPLOY_LOCK_HELD=1 PKUBA_RECOVERY_LOCK_HELD=1 PKUBA_PAIRED_RECOVERY=1 \
    bash "$paired_restore_command" --resume
  paired_status=$?
  rm -f "$prevalidation_required_file"
  sync -f "$state_dir"
  trap - EXIT
  exit "$paired_status"
fi

if [[ -e $transaction_dir && -e $completed_transaction_dir ]]; then
  touch "$maintenance_file"
  die "active and completed release transaction journals both exist"
fi
resuming_completed=0
if [[ -e $completed_transaction_dir ]]; then
  [[ -d $completed_transaction_dir && -f $completed_transaction_dir/journal.env \
    && -f $completed_transaction_dir/completion.env \
    && -f $completed_transaction_dir/completion.sha256 ]] \
    || { touch "$maintenance_file"; die "completed release transaction is invalid"; }
  mv "$completed_transaction_dir" "$transaction_dir" \
    || { touch "$maintenance_file"; die "could not reopen the completed release transaction"; }
  sync -f "$state_dir" \
    || { touch "$maintenance_file"; die "could not durably reopen the completed release transaction"; }
  resuming_completed=1
fi

if [[ -f $paired_restore_marker ]]; then
  touch "$maintenance_file"
  sync -f "$state_dir"
  die "legacy paired restore marker requires manual diagnosis"
fi
[[ -e $transaction_dir ]] || exit 0
[[ -d $transaction_dir && -f $journal_file ]] \
  || { touch "$maintenance_file"; die "incomplete release transaction has no valid journal"; }
touch "$maintenance_file"
sync -f "$state_dir"

declare -A journal=()
while IFS='=' read -r key value; do
  [[ -n $key && $key =~ ^[A-Z0-9_]+$ ]] || die "invalid transaction journal line"
  [[ ! -v "journal[$key]" ]] || die "duplicate transaction journal key: $key"
  case "$key" in
    JOURNAL_VERSION|TRANSACTION_ID|TRANSACTION_KIND|PHASE|ORIGINAL_MAINTENANCE|OLD_SLOT|NEW_SLOT|OLD_TAG|OLD_COMMIT|NEW_TAG|NEW_COMMIT|BACKUP_DIR|AUDIT_FILE|CREATED_AT)
      journal[$key]=$value
      ;;
    *) die "unexpected transaction journal key: $key" ;;
  esac
done <"$journal_file"
for key in JOURNAL_VERSION TRANSACTION_ID TRANSACTION_KIND PHASE ORIGINAL_MAINTENANCE OLD_SLOT \
  NEW_SLOT OLD_TAG OLD_COMMIT NEW_TAG NEW_COMMIT BACKUP_DIR AUDIT_FILE CREATED_AT; do
  [[ -v "journal[$key]" ]] || die "transaction journal is missing $key"
done
[[ ${journal[JOURNAL_VERSION]} == 1 ]] || die "unsupported transaction journal version"
[[ ${journal[TRANSACTION_KIND]} == DEPLOY || ${journal[TRANSACTION_KIND]} == ROLLBACK ]] \
  || die "invalid transaction kind"
[[ ${journal[PHASE]} =~ ^(PREPARED|RUNTIME_SWITCHED|STATE_COMMITTING|RECOVERING_OLD|RECOVERING_NEW|RECOVERY_REQUIRED_OLD|RECOVERY_REQUIRED_NEW|OLD_COMMITTED|NEW_COMMITTED)$ ]] \
  || die "invalid transaction phase"
[[ ${journal[ORIGINAL_MAINTENANCE]} == 0 || ${journal[ORIGINAL_MAINTENANCE]} == 1 ]] \
  || die "invalid original maintenance flag"
[[ ${journal[OLD_SLOT]} =~ ^(blue|green)$ && ${journal[NEW_SLOT]} =~ ^(blue|green)$ \
  && ${journal[OLD_SLOT]} != "${journal[NEW_SLOT]}" ]] || die "invalid transaction slots"
[[ ${journal[OLD_TAG]} =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ \
  && ${journal[NEW_TAG]} =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid transaction tags"
[[ ${journal[OLD_COMMIT]} =~ ^[0-9a-f]{40}$ \
  && ${journal[NEW_COMMIT]} =~ ^[0-9a-f]{40}$ ]] || die "invalid transaction commits"
[[ ${journal[CREATED_AT]} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
  || die "invalid transaction creation time"
created_compact=$(date -u -d "${journal[CREATED_AT]}" +%Y%m%dT%H%M%SZ) \
  || die "invalid transaction creation time"
(cd "$transaction_dir" && sha256sum --check immutable.sha256 >/dev/null) \
  || die "transaction immutable-field checksum failed"
cmp -s <(grep -v '^PHASE=' "$journal_file") "$transaction_dir/immutable.env" \
  || die "transaction immutable fields differ from their signed snapshot"

canonical_log_root=$(realpath -e -- "$log_root") \
  || die "release log root is unavailable"
case ${journal[TRANSACTION_KIND]} in
  DEPLOY)
    expected_transaction_id=deploy-$created_compact-${journal[OLD_TAG]}-to-${journal[NEW_TAG]}
    [[ ${journal[TRANSACTION_ID]} == "$expected_transaction_id" ]] \
      || die "deployment transaction identity is invalid"
    [[ ${journal[AUDIT_FILE]} == - ]] \
      || die "deployment journal has an unexpected audit path"
    backup_root=$deploy_root/backups
    backup_dir=${journal[BACKUP_DIR]}
    backup_name=${backup_dir##*/}
    [[ $backup_name == "$created_compact-pre-${journal[NEW_TAG]}" \
      && $backup_dir == "$backup_root/$backup_name" \
      && -d $backup_dir && ! -L $backup_dir ]] \
      || die "deployment journal backup path is invalid"
    canonical_backup_root=$(realpath -e -- "$backup_root") \
      || die "deployment backup root is unavailable"
    canonical_backup_dir=$(realpath -e -- "$backup_dir") \
      || die "deployment journal backup path is unavailable"
    [[ $(dirname "$canonical_backup_dir") == "$canonical_backup_root" \
      && $canonical_backup_dir == "$canonical_backup_root/$backup_name" ]] \
      || die "deployment journal backup path escapes the backup root"
    ;;
  ROLLBACK)
    expected_transaction_id=rollback-$created_compact-${journal[OLD_TAG]}-to-${journal[NEW_TAG]}
    [[ ${journal[TRANSACTION_ID]} == "$expected_transaction_id" ]] \
      || die "application rollback transaction identity is invalid"
    [[ ${journal[BACKUP_DIR]} == - ]] \
      || die "application rollback journal has an unexpected backup path"
    audit_file=${journal[AUDIT_FILE]}
    audit_name=${audit_file##*/}
    [[ $audit_name == "$expected_transaction_id-application-rollback.env" \
      && $audit_file == "$log_root/$audit_name" \
      && ! -L $audit_file ]] \
      || die "application rollback journal audit path is invalid"
    canonical_audit_parent=$(realpath -e -- "$(dirname "$audit_file")") \
      || die "application rollback audit parent is unavailable"
    [[ $canonical_audit_parent == "$canonical_log_root" ]] \
      || die "application rollback journal audit path escapes the log root"
    if [[ -e $audit_file ]]; then
      [[ -f $audit_file ]] || die "application rollback audit path is not a file"
      canonical_audit_file=$(realpath -e -- "$audit_file") \
        || die "application rollback audit path is unavailable"
      [[ $canonical_audit_file == "$canonical_log_root/$audit_name" ]] \
        || die "application rollback journal audit file escapes the log root"
    fi
    ;;
esac

(cd "$transaction_dir" && sha256sum --check original.sha256 >/dev/null) \
  || die "transaction original-state checksum failed"

identity_validator=${PKUBA_RELEASE_IDENTITY_VALIDATOR:-/usr/local/libexec/pkuba/validate-release-identity.sh}
if [[ ! -x $identity_validator ]]; then
  identity_validator=$script_dir/validate-release-identity.sh
fi
parsed_old=$(bash "$identity_validator" \
  "$transaction_dir/original/current" "$release_root" "$repository_dir") \
  || die "transaction original release identity is invalid"
IFS=$'\t' read -r \
  OLD_SLOT OLD_TAG OLD_COMMIT OLD_API_IMAGE OLD_WEB_IMAGE OLD_RELEASE_DIR OLD_CAPABILITY _ \
  <<<"$parsed_old"
[[ $OLD_SLOT == "${journal[OLD_SLOT]}" && $OLD_TAG == "${journal[OLD_TAG]}" \
  && $OLD_COMMIT == "${journal[OLD_COMMIT]}" ]] || die "journal/original identity mismatch"

parsed_new=$(bash "$identity_validator" \
  "$transaction_dir/next/current.env" "$release_root" "$repository_dir") \
  || die "transaction candidate release identity is invalid"
IFS=$'\t' read -r \
  NEW_SLOT NEW_TAG NEW_COMMIT NEW_API_IMAGE NEW_WEB_IMAGE NEW_RELEASE_DIR NEW_CAPABILITY _ \
  <<<"$parsed_new"
[[ $NEW_SLOT == "${journal[NEW_SLOT]}" && $NEW_TAG == "${journal[NEW_TAG]}" \
  && $NEW_COMMIT == "${journal[NEW_COMMIT]}" ]] || die "journal/candidate identity mismatch"

recovery_direction=
recovery_archive_dir=

write_journal_phase() {
  local phase=$1 temporary=$journal_file.tmp.$$
  awk -F= -v phase="$phase" \
    'BEGIN {OFS="="} $1 == "PHASE" {$2=phase} {print $1,$2}' \
    "$journal_file" >"$temporary"
  chmod 600 "$temporary"
  sync -f "$temporary"
  mv -f "$temporary" "$journal_file"
  sync -f "$transaction_dir"
  journal[PHASE]=$phase
}

crash_point() {
  if [[ ${PKUBA_TEST_RECOVERY_CRASH_POINT:-} == "$1" ]]; then
    kill -KILL $$
  fi
}

on_recovery_failure() {
  local status=$?
  trap - EXIT
  set +e
  touch "$maintenance_file"
  sync -f "$state_dir"
  fence_all_writers >/dev/null 2>&1
  if [[ ! -d $transaction_dir && -d $completed_transaction_dir ]]; then
    mv "$completed_transaction_dir" "$transaction_dir" 2>/dev/null
    sync -f "$state_dir" 2>/dev/null
  fi
  if [[ ! -d $transaction_dir && -n $recovery_archive_dir \
    && -d $recovery_archive_dir ]]; then
    mv "$recovery_archive_dir" "$transaction_dir" 2>/dev/null
    sync -f "$state_dir" 2>/dev/null
    sync -f "$journal_archive_root" 2>/dev/null
  fi
  if [[ ! -d $transaction_dir ]]; then
    echo "RELEASE RECOVERY FAILED. Maintenance remains in place, but the durable journal could not be restored to its canonical path." >&2
    (( status != 0 )) || status=1
    exit "$status"
  fi
  if [[ $recovery_direction == OLD || $recovery_direction == NEW ]]; then
    write_journal_phase "RECOVERY_REQUIRED_$recovery_direction"
  fi
  cat >"$transaction_dir/recovery-required.env.tmp" <<EOF
result=recovery_required
direction=${recovery_direction:-UNKNOWN}
failed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
database_restored=0
media_restored=0
archive_restored=0
EOF
  chmod 600 "$transaction_dir/recovery-required.env.tmp" 2>/dev/null
  sync -f "$transaction_dir/recovery-required.env.tmp" 2>/dev/null
  mv -f "$transaction_dir/recovery-required.env.tmp" \
    "$transaction_dir/recovery-required.env" 2>/dev/null
  sync -f "$transaction_dir" 2>/dev/null
  echo "RELEASE RECOVERY FAILED. Maintenance and the durable journal remain in place." >&2
  (( status != 0 )) || status=1
  exit "$status"
}
trap on_recovery_failure EXIT
fence_all_writers || die "could not establish the two-slot writer fence"
rm -f "$prevalidation_required_file"
sync -f "$state_dir"

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

worker_services=(expiry scoresheet-worker archive-worker)
[[ $email_profile == 1 ]] && worker_services+=(outbox)

atomic_install() {
  local source_file=$1 destination=$2 mode=${3:-600}
  local temporary=$destination.recovery.$$
  cp "$source_file" "$temporary"
  chmod "$mode" "$temporary"
  sync -f "$temporary"
  mv -f "$temporary" "$destination" \
    || die "could not atomically install recovered state: $destination"
  sync -f "$(dirname "$destination")"
}

remove_persisted() {
  local destination
  for destination in "$@"; do
    rm -f "$destination"
    sync -f "$(dirname "$destination")"
  done
}

restore_snapshot_file() {
  local destination=$1 key=$2 mode=${3:-600}
  if [[ -f $transaction_dir/original/$key.present ]]; then
    atomic_install "$transaction_dir/original/$key" "$destination" "$mode"
  else
    remove_persisted "$destination"
  fi
}

verify_snapshot_file() {
  local destination=$1 key=$2
  if [[ -f $transaction_dir/original/$key.present ]]; then
    cmp -s "$transaction_dir/original/$key" "$destination" \
      || die "restored state differs from snapshot: $key"
  else
    [[ ! -e $destination ]] || die "state should not exist after restore: $destination"
  fi
}

wait_ready() {
  local slot=$1 tag=$2 commit=$3 api_body= web_body=
  for _ in $(seq 1 60); do
    api_body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$(slot_api_port "$slot")/api/v1/health/ready" || true)
    web_body=$(curl --silent --show-error \
      "http://127.0.0.1:$(slot_web_port "$slot")/_deployment/ready" || true)
    if [[ $api_body == *"$tag"* && $api_body == *"$commit"* \
      && $web_body == *"$tag"* && $web_body == *"$commit"* \
      && ( $api_body == *'"status":"ok"'* || $api_body == *'"status": "ok"'* ) \
      && ( $web_body == *'"status":"ok"'* || $web_body == *'"status": "ok"'* ) ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

assert_services_stable() {
  local slot=$1 directory=$2 api_image=$3 web_image=$4 tag=$5 commit=$6
  local service container before after
  local services=(api web "${worker_services[@]}")
  declare -A restart_counts=()
  for service in "${services[@]}"; do
    container=$(compose_slot "$slot" "$directory" "$api_image" "$web_image" \
      "$tag" "$commit" ps -q "$service")
    [[ -n $container ]] || die "recovered service has no container: $service"
    [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] \
      || die "recovered service is not running: $service"
    before=$(docker inspect --format '{{.RestartCount}}' "$container")
    restart_counts[$service]=$before
  done
  sleep "$stability_seconds"
  for service in "${services[@]}"; do
    container=$(compose_slot "$slot" "$directory" "$api_image" "$web_image" \
      "$tag" "$commit" ps -q "$service")
    [[ -n $container ]] || die "recovered service disappeared: $service"
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${restart_counts[$service]} ]] \
      || die "recovered service restarted during stability check: $service"
  done
}

install_upstreams() {
  atomic_install "$1" "$upstreams_file" 644
}

reload_gateway() {
  compose_gateway "$1" exec -T gateway caddy reload --config /etc/caddy/Caddyfile
}

public_domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r"')
[[ $public_domain =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid PKUBA_DOMAIN"

wait_gateway_identity() {
  local tag=$1 commit=$2 api_body= web_body=
  for _ in $(seq 1 "$probe_attempts"); do
    api_body=$(curl --fail --silent --show-error \
      "https://api.$public_domain/api/v1/health/live" || true)
    web_body=$(curl --fail --silent --show-error \
      "https://admin.$public_domain/_deployment/ready" || true)
    if [[ $api_body == *"$tag"* && $api_body == *"$commit"* \
      && $web_body == *"$tag"* && $web_body == *"$commit"* ]]; then
      return 0
    fi
    sleep "$probe_delay_seconds"
  done
  return 1
}

wait_public_ready() {
  local tag=$1 commit=$2 api_body= web_body=
  for _ in $(seq 1 "$probe_attempts"); do
    api_body=$(curl --fail --silent --show-error \
      "https://api.$public_domain/api/v1/health/ready" || true)
    web_body=$(curl --fail --silent --show-error \
      "https://admin.$public_domain/_deployment/ready" || true)
    if [[ $api_body == *"$tag"* && $api_body == *"$commit"* \
      && $web_body == *"$tag"* && $web_body == *"$commit"* \
      && ( $api_body == *'"status":"ok"'* || $api_body == *'"status": "ok"'* ) \
      && ( $web_body == *'"status":"ok"'* || $web_body == *'"status": "ok"'* ) ]]; then
      return 0
    fi
    sleep "$probe_delay_seconds"
  done
  return 1
}

verify_original_state() {
  local old_state=$slot_state_dir/$OLD_SLOT.env
  local old_deadline=$old_state.retain-until
  local new_state=$slot_state_dir/$NEW_SLOT.env
  local new_deadline=$new_state.retain-until
  verify_snapshot_file "$current_state" current
  verify_snapshot_file "$upstreams_file" upstreams
  if [[ ${journal[TRANSACTION_KIND]} == DEPLOY ]]; then
    verify_snapshot_file "$new_state" candidate
    verify_snapshot_file "$new_deadline" candidate-deadline
    verify_snapshot_file "$old_state" active-slot
    verify_snapshot_file "$old_deadline" active-deadline
    local backup_dir=${journal[BACKUP_DIR]}
    [[ $backup_dir == "$deploy_root/backups/"* && -d $backup_dir ]] \
      || die "journal backup directory is invalid"
    if [[ -f $transaction_dir/backup-base.sha256 ]]; then
      (cd "$transaction_dir" && sha256sum --check backup-base.sha256 >/dev/null) \
        || die "base deployment backup checksum changed"
      (cd "$backup_dir" && sha256sum --check \
        "$transaction_dir/backup-base.SHA256SUMS" >/dev/null) \
        || die "base deployment backup payload changed"
    else
      verify_snapshot_file "$backup_dir/SHA256SUMS" backup-sha
    fi
    verify_snapshot_file "$backup_dir/release.json" release-json
    verify_snapshot_file "$backup_dir/SUCCESS" success
  else
    verify_snapshot_file "$new_state" target
    verify_snapshot_file "$new_deadline" target-deadline
    verify_snapshot_file "$old_state" old-slot
    verify_snapshot_file "$old_deadline" old-deadline
    local audit_file=${journal[AUDIT_FILE]}
    [[ $audit_file == "$log_root/"* ]] || die "journal audit path is invalid"
    verify_snapshot_file "$audit_file" audit
  fi
  bash "$identity_validator" "$current_state" "$release_root" "$repository_dir" >/dev/null
}

restore_original_state() {
  local old_state=$slot_state_dir/$OLD_SLOT.env
  local old_deadline=$old_state.retain-until
  local new_state=$slot_state_dir/$NEW_SLOT.env
  local new_deadline=$new_state.retain-until
  restore_snapshot_file "$current_state" current
  restore_snapshot_file "$upstreams_file" upstreams 644
  if [[ ${journal[TRANSACTION_KIND]} == DEPLOY ]]; then
    restore_snapshot_file "$new_state" candidate
    restore_snapshot_file "$new_deadline" candidate-deadline
    restore_snapshot_file "$old_state" active-slot
    restore_snapshot_file "$old_deadline" active-deadline
    local backup_dir=${journal[BACKUP_DIR]}
    [[ $backup_dir == "$deploy_root/backups/"* && -d $backup_dir ]] \
      || die "journal backup directory is invalid"
    if [[ -f $transaction_dir/backup-base.sha256 ]]; then
      (cd "$transaction_dir" && sha256sum --check backup-base.sha256 >/dev/null) \
        || die "base deployment backup checksum changed"
      (cd "$backup_dir" && sha256sum --check \
        "$transaction_dir/backup-base.SHA256SUMS" >/dev/null) \
        || die "base deployment backup payload changed"
      restore_snapshot_file "$backup_dir/SHA256SUMS" backup-sha
    else
      restore_snapshot_file "$backup_dir/SHA256SUMS" backup-sha
    fi
    restore_snapshot_file "$backup_dir/release.json" release-json
    restore_snapshot_file "$backup_dir/SUCCESS" success
  else
    restore_snapshot_file "$new_state" target
    restore_snapshot_file "$new_deadline" target-deadline
    restore_snapshot_file "$old_state" old-slot
    restore_snapshot_file "$old_deadline" old-deadline
    local audit_file=${journal[AUDIT_FILE]}
    [[ $audit_file == "$log_root/"* ]] || die "journal audit path is invalid"
    restore_snapshot_file "$audit_file" audit
  fi
  verify_original_state
}

verify_candidate_state() {
  local old_state=$slot_state_dir/$OLD_SLOT.env
  local old_deadline=$old_state.retain-until
  local new_state=$slot_state_dir/$NEW_SLOT.env
  local new_deadline=$new_state.retain-until
  cmp -s "$transaction_dir/next/current.env" "$current_state" \
    || die "current state differs from committed transaction"
  cmp -s "$transaction_dir/next/retained.env" "$old_state" \
    || die "retained state differs from committed transaction"
  cmp -s "$transaction_dir/next/retained.retain-until" "$old_deadline" \
    || die "retained deadline differs from committed transaction"
  cmp -s "$transaction_dir/next/upstreams.caddy" "$upstreams_file" \
    || die "gateway upstream differs from committed transaction"
  [[ ! -e $new_state && ! -e $new_deadline ]] \
    || die "newly active slot still has retained-state files"
  if [[ ${journal[TRANSACTION_KIND]} == DEPLOY ]]; then
    local backup_dir=${journal[BACKUP_DIR]}
    cmp -s "$transaction_dir/next/release.json" "$backup_dir/release.json" \
      || die "release audit differs from committed transaction"
    cmp -s "$transaction_dir/next/SHA256SUMS" "$backup_dir/SHA256SUMS" \
      || die "backup checksum manifest differs from committed transaction"
    cmp -s "$transaction_dir/next/SUCCESS" "$backup_dir/SUCCESS" \
      || die "deployment success marker differs from committed transaction"
    (cd "$backup_dir" && sha256sum --check SHA256SUMS >/dev/null) \
      || die "committed deployment audit checksum failed"
  else
    local audit_file=${journal[AUDIT_FILE]}
    cmp -s "$transaction_dir/next/audit.env" "$audit_file" \
      || die "application rollback audit differs from committed transaction"
  fi
  bash "$identity_validator" "$current_state" "$release_root" "$repository_dir" >/dev/null
}

parse_completion_payload() {
  local completion_file=$1
  declare -gA completion=()
  local key value
  while IFS='=' read -r key value; do
    [[ $key =~ ^[A-Z0-9_]+$ && ! -v "completion[$key]" ]] \
      || die "invalid or duplicate completion payload key"
    case "$key" in
      TRANSACTION_ID|RESULT|AUDIT_FILE|OLD_TAG|NEW_TAG|DATABASE_RESTORED|MEDIA_RESTORED|ARCHIVE_RESTORED|COMPLETED_AT)
        completion[$key]=$value ;;
      *) die "unexpected completion payload key: $key" ;;
    esac
  done <"$completion_file"
  for key in TRANSACTION_ID RESULT AUDIT_FILE OLD_TAG NEW_TAG DATABASE_RESTORED \
    MEDIA_RESTORED ARCHIVE_RESTORED COMPLETED_AT; do
    [[ -v "completion[$key]" ]] || die "completion payload is missing $key"
  done
  [[ ${completion[TRANSACTION_ID]} == "${journal[TRANSACTION_ID]}" ]] \
    || die "completion payload belongs to another transaction"
  [[ ${completion[RESULT]} == completed_candidate \
    || ${completion[RESULT]} == restored_original ]] \
    || die "invalid completion result"
  [[ ${completion[OLD_TAG]} == "$OLD_TAG" && ${completion[NEW_TAG]} == "$NEW_TAG" ]] \
    || die "completion payload release identity is invalid"
  [[ ${completion[DATABASE_RESTORED]} == 0 \
    && ${completion[MEDIA_RESTORED]} == 0 \
    && ${completion[ARCHIVE_RESTORED]} == 0 ]] \
    || die "application recovery must not claim a data restore"
  [[ ${completion[COMPLETED_AT]} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
    || die "invalid completion timestamp"
  local expected_audit=$log_root/${journal[TRANSACTION_ID]}-release-recovery.env
  [[ ${completion[AUDIT_FILE]} == "$expected_audit" ]] \
    || die "completion audit path is not bound to the transaction"
}

finalize_completed_transaction() {
  local completed_dir=$1
  (cd "$completed_dir" && sha256sum --check completion.sha256 >/dev/null) \
    || die "completion payload checksum failed"
  parse_completion_payload "$completed_dir/completion.env"
  local audit_file=${completion[AUDIT_FILE]}
  if [[ -e $audit_file ]]; then
    [[ -f $audit_file && ! -L $audit_file ]] \
      || die "completion audit target is unsafe"
    cmp -s "$completed_dir/completion.env" "$audit_file" \
      || die "existing completion audit belongs to different content"
  else
    atomic_install "$completed_dir/completion.env" "$audit_file"
  fi
  sync -f "$log_root"
  crash_point after-completion-audit
}

archive_completed_transaction() {
  local completed_dir=$1
  recovery_archive_dir=$journal_archive_root/${journal[TRANSACTION_ID]}
  [[ ! -e $recovery_archive_dir ]] || die "release transaction archive already exists"
  mv "$completed_dir" "$recovery_archive_dir"
  sync -f "$state_dir"
  sync -f "$journal_archive_root"
  crash_point after-completed-archive
}

finish_recovery() {
  local tag=$1 commit=$2 result=$3 phase=$4
  write_journal_phase "$phase"
  crash_point after-recovery-committed
  # Both health endpoints bypass the maintenance response, while every business
  # route remains fenced.  Therefore public identity/readiness can be proven
  # before the single final maintenance-removal commit point.
  wait_public_ready "$tag" "$commit" \
    || die "stable public entry did not expose the recovered release"
  sleep "$stability_seconds"
  wait_public_ready "$tag" "$commit" \
    || die "stable public entry was not stable for the recovered release"
  local completion_file=$transaction_dir/completion.env
  local completion_audit=$log_root/${journal[TRANSACTION_ID]}-release-recovery.env
  if [[ -e $completion_file || -e $transaction_dir/completion.sha256 ]]; then
    [[ -f $completion_file && -f $transaction_dir/completion.sha256 ]] \
      || die "partial completion payload exists"
    (cd "$transaction_dir" && sha256sum --check completion.sha256 >/dev/null) \
      || die "completion payload checksum failed"
    parse_completion_payload "$completion_file"
    [[ ${completion[RESULT]} == "$result" ]] \
      || die "completion payload result differs from the recovery direction"
  else
    cat >"$completion_file.tmp" <<EOF
TRANSACTION_ID=${journal[TRANSACTION_ID]}
RESULT=$result
AUDIT_FILE=$completion_audit
OLD_TAG=$OLD_TAG
NEW_TAG=$NEW_TAG
DATABASE_RESTORED=0
MEDIA_RESTORED=0
ARCHIVE_RESTORED=0
COMPLETED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 600 "$completion_file.tmp"
    sync -f "$completion_file.tmp"
    mv -f "$completion_file.tmp" "$completion_file"
    (cd "$transaction_dir" && sha256sum completion.env >completion.sha256.tmp)
    chmod 600 "$transaction_dir/completion.sha256.tmp"
    sync -f "$transaction_dir/completion.sha256.tmp"
    mv -f "$transaction_dir/completion.sha256.tmp" "$transaction_dir/completion.sha256"
    sync -f "$transaction_dir"
    (cd "$transaction_dir" && sha256sum --check completion.sha256 >/dev/null)
  fi
  crash_point after-completion-payload
  [[ ! -e $completed_transaction_dir ]] \
    || die "a completed release transaction already exists"
  mv "$transaction_dir" "$completed_transaction_dir"
  sync -f "$state_dir" \
    || die "could not durably commit the completed release transaction"
  crash_point after-transaction-rename
  finalize_completed_transaction "$completed_transaction_dir"
  if [[ ${journal[ORIGINAL_MAINTENANCE]} == 0 ]]; then
    remove_persisted "$maintenance_file"
    crash_point after-maintenance-removal
  fi
  archive_completed_transaction "$completed_transaction_dir"
  trap - EXIT
  echo "PKUBA_RELEASE_RECOVERY_RESULT=$result"
}

case ${journal[PHASE]} in
  PREPARED|RUNTIME_SWITCHED|STATE_COMMITTING|RECOVERING_OLD|RECOVERY_REQUIRED_OLD|OLD_COMMITTED)
    recovery_direction=OLD
    write_journal_phase RECOVERING_OLD
    restore_original_state
    crash_point after-state-recovery
    compose_slot "$NEW_SLOT" "$NEW_RELEASE_DIR" "$NEW_API_IMAGE" "$NEW_WEB_IMAGE" \
      "$NEW_TAG" "$NEW_COMMIT" stop --timeout 60 api "${worker_services[@]}" \
      >/dev/null 2>&1 || true
    compose_slot "$OLD_SLOT" "$OLD_RELEASE_DIR" "$OLD_API_IMAGE" "$OLD_WEB_IMAGE" \
      "$OLD_TAG" "$OLD_COMMIT" up -d --no-deps web "${worker_services[@]}" api
    wait_ready "$OLD_SLOT" "$OLD_TAG" "$OLD_COMMIT" \
      || die "original application did not recover"
    assert_services_stable "$OLD_SLOT" "$OLD_RELEASE_DIR" "$OLD_API_IMAGE" \
      "$OLD_WEB_IMAGE" "$OLD_TAG" "$OLD_COMMIT"
    install_upstreams "$transaction_dir/original/upstreams"
    reload_gateway "$OLD_RELEASE_DIR" \
      || die "gateway reload failed while recovering the original release"
    crash_point after-gateway-reload
    wait_gateway_identity "$OLD_TAG" "$OLD_COMMIT" \
      || die "stable gateway did not expose the original release"
    verify_original_state
    finish_recovery "$OLD_TAG" "$OLD_COMMIT" restored_original OLD_COMMITTED
    ;;
  NEW_COMMITTED|RECOVERING_NEW|RECOVERY_REQUIRED_NEW)
    recovery_direction=NEW
    (cd "$transaction_dir" && sha256sum --check prepared.sha256 >/dev/null) \
      || die "transaction prepared-state checksum failed"
    write_journal_phase RECOVERING_NEW
    verify_candidate_state
    crash_point after-state-recovery
    compose_slot "$NEW_SLOT" "$NEW_RELEASE_DIR" "$NEW_API_IMAGE" "$NEW_WEB_IMAGE" \
      "$NEW_TAG" "$NEW_COMMIT" up -d --no-deps web "${worker_services[@]}" api
    wait_ready "$NEW_SLOT" "$NEW_TAG" "$NEW_COMMIT" \
      || die "candidate application did not recover"
    assert_services_stable "$NEW_SLOT" "$NEW_RELEASE_DIR" "$NEW_API_IMAGE" \
      "$NEW_WEB_IMAGE" "$NEW_TAG" "$NEW_COMMIT"
    install_upstreams "$transaction_dir/next/upstreams.caddy"
    reload_gateway "$NEW_RELEASE_DIR" \
      || die "gateway reload failed while recovering the candidate release"
    crash_point after-gateway-reload
    wait_gateway_identity "$NEW_TAG" "$NEW_COMMIT" \
      || die "stable gateway did not expose the candidate release"
    verify_candidate_state
    finish_recovery "$NEW_TAG" "$NEW_COMMIT" completed_candidate NEW_COMMITTED
    ;;
esac
