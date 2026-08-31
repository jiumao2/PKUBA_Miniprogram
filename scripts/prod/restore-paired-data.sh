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
  sudo restore-paired-data.sh --resume

The first form starts a confirmed DB/media/archive restore. The second form is
reserved for boot-time recovery of an already durable restore transaction.
Normal application rollback must never call this command.
EOF
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
resume=0
backup_argument=
if [[ $# -eq 1 && $1 == --resume ]]; then
  resume=1
elif [[ $# -eq 2 ]]; then
  backup_argument=$1
  [[ $2 == RESTORE_PAIRED_DATA ]] \
    || die "type RESTORE_PAIRED_DATA as the second argument"
else
  usage >&2
  exit 2
fi

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # Root-owned paths and switches only; never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

isolated=${PKUBA_RESTORE_ISOLATED:-0}
deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
release_root=${PKUBA_RELEASE_ROOT:-$deploy_root/releases}
backup_root=${PKUBA_BACKUP_ROOT:-$deploy_root/backups}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
slot_state_dir=$state_dir/slots
log_root=$deploy_root/logs
incident_root=$deploy_root/incident-snapshots
transaction_dir=$state_dir/paired-restore-transaction
completed_transaction_dir=$state_dir/paired-restore-completed
journal_archive_root=$log_root/paired-restore-transactions
legacy_marker=$state_dir/paired-restore-incomplete.env
current_state=$state_dir/current.env
upstreams_file=$state_dir/upstreams.caddy
maintenance_file=$state_dir/maintenance.enabled
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/production/.env}
data_project=${PKUBA_DATA_PROJECT:-pkuba-data}
gateway_project=${PKUBA_GATEWAY_PROJECT:-pkuba-gateway}
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-prod-runtime}
postgres_volume=${PKUBA_POSTGRES_VOLUME:-pkuba-prod-postgres}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-prod-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-prod-archives}
postgres_image=${PKUBA_POSTGRES_IMAGE:-ghcr.io/jiumao2/pkuba-postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73}
caddy_image=${PKUBA_CADDY_IMAGE:-ghcr.io/jiumao2/pkuba-caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d}
[[ $postgres_image == ghcr.io/jiumao2/pkuba-postgres@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73 ]] \
  || die "PostgreSQL must use the approved mirrored digest"
[[ $caddy_image == ghcr.io/jiumao2/pkuba-caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d ]] \
  || die "Caddy must use the approved mirrored digest"
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
stability_seconds=${PKUBA_SERVICE_STABILITY_SECONDS:-10}
probe_attempts=${PKUBA_GATEWAY_PROBE_ATTEMPTS:-30}
probe_delay_seconds=${PKUBA_GATEWAY_PROBE_DELAY_SECONDS:-2}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for command_name in awk chmod cmp cp curl date dirname docker find grep head \
  mkdir mktemp mv python3 realpath rm sed seq sha256sum sleep sync tar; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
[[ -d $state_dir ]] || die "missing pre-created deployment state directory"
lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-/usr/local/libexec/pkuba/acquire-deploy-lock.py}
[[ -f $lock_helper ]] || lock_helper=$script_dir/acquire-deploy-lock.py
[[ -f $lock_helper ]] || die "missing secure deployment lock helper"
if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 && ${PKUBA_RECOVERY_LOCK_HELD:-0} != 1 ]]; then
  exec env \
    PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} \
    python3 "$lock_helper" --state-dir "$state_dir" --timeout 1800 -- \
    bash "$0" "$@"
fi

mkdir -p "$slot_state_dir" "$log_root" "$incident_root" "$journal_archive_root"
if [[ $resume == 0 && ( -e $state_dir/release-transaction \
  || -e $state_dir/release-transaction-completed ) ]]; then
  recovery_command=${PKUBA_RELEASE_RECOVERY_COMMAND:-/usr/local/sbin/pkuba-recover-release-transaction}
  [[ -x $recovery_command ]] || recovery_command=$script_dir/recover-release-transaction.sh
  [[ -f $recovery_command ]] || die "missing application release recovery command"
  PKUBA_RECOVERY_LOCK_HELD=1 PKUBA_DEPLOY_LOCK_HELD=1 bash "$recovery_command"
fi
[[ ! -e $legacy_marker ]] \
  || { touch "$maintenance_file"; sync -f "$state_dir"; die "legacy paired restore marker requires manual diagnosis"; }
[[ ! -e $state_dir/release-transaction && ! -e $state_dir/release-transaction-completed ]] \
  || { touch "$maintenance_file"; sync -f "$state_dir"; die "application and paired restore transactions cannot overlap"; }
if [[ -e $transaction_dir && -e $completed_transaction_dir ]]; then
  touch "$maintenance_file"
  sync -f "$state_dir"
  die "active and completed paired restore transactions both exist"
fi
if [[ -e $completed_transaction_dir ]]; then
  [[ $resume == 1 ]] || die "a completed paired restore must be finalized first"
  touch "$maintenance_file"
  sync -f "$state_dir"
  mv "$completed_transaction_dir" "$transaction_dir" \
    || die "could not reopen completed paired restore transaction"
  sync -f "$state_dir" || die "could not durably reopen paired restore transaction"
fi

identity_validator=${PKUBA_RELEASE_IDENTITY_VALIDATOR:-/usr/local/libexec/pkuba/validate-release-identity.sh}
[[ -x $identity_validator ]] || identity_validator=$script_dir/validate-release-identity.sh
backup_verifier=${PKUBA_PAIRED_BACKUP_VERIFIER:-/usr/local/libexec/pkuba/verify-paired-backup.py}
[[ -f $backup_verifier ]] || backup_verifier=$script_dir/verify-paired-backup.py
writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
[[ -x $writer_fence ]] || writer_fence=$script_dir/fence-deploy-writers.sh
[[ -f $identity_validator && -f $backup_verifier && -f $writer_fence ]] \
  || die "paired restore helpers are incomplete"
if [[ $resume == 1 && -e $transaction_dir ]]; then
  # A restart may have allowed Docker to bring old containers back before the
  # recovery unit ran.  An existing transaction therefore fences first, even
  # when its journal later proves corrupt; a brand-new invalid restore request
  # still completes its full preflight without any service side effect.
  touch "$maintenance_file"
  sync -f "$state_dir"
  bash "$writer_fence" || die "could not establish the recovery writer fence"
fi

allow_test_root=()
[[ $isolated == 1 ]] && allow_test_root=(--allow-test-root)
scratch_root=$deploy_root/restore-preflight-scratch
mkdir -p "$scratch_root"

declare -A journal=()
journal_file=$transaction_dir/journal.env
parse_journal() {
  journal=()
  local key value
  while IFS='=' read -r key value; do
    [[ $key =~ ^[A-Z0-9_]+$ && ! -v "journal[$key]" ]] \
      || die "invalid or duplicate paired restore journal key"
    case "$key" in
      JOURNAL_VERSION|TRANSACTION_ID|TRANSACTION_KIND|PHASE|ORIGINAL_MAINTENANCE|BACKUP_DIR|BACKUP_MANIFEST_SHA256|BACKUP_TRANSACTION_ID|TARGET_SLOT|TARGET_TAG|TARGET_COMMIT|TARGET_API_IMAGE|TARGET_WEB_IMAGE|TARGET_RELEASE_DIR|TARGET_APP_CAPABILITY|TARGET_ROLLBACK_ALLOWED_FROM|INCIDENT_DIR|AUDIT_FILE|CREATED_AT)
        journal[$key]=$value ;;
      *) die "unexpected paired restore journal key: $key" ;;
    esac
  done <"$journal_file"
  for key in JOURNAL_VERSION TRANSACTION_ID TRANSACTION_KIND PHASE ORIGINAL_MAINTENANCE \
    BACKUP_DIR BACKUP_MANIFEST_SHA256 BACKUP_TRANSACTION_ID TARGET_SLOT TARGET_TAG \
    TARGET_COMMIT TARGET_API_IMAGE TARGET_WEB_IMAGE TARGET_RELEASE_DIR \
    TARGET_APP_CAPABILITY TARGET_ROLLBACK_ALLOWED_FROM INCIDENT_DIR AUDIT_FILE CREATED_AT; do
    [[ -v "journal[$key]" ]] || die "paired restore journal is missing $key"
  done
  [[ ${journal[JOURNAL_VERSION]} == 1 \
    && ${journal[TRANSACTION_KIND]} == PAIRED_RESTORE ]] \
    || die "unsupported paired restore journal"
  [[ ${journal[PHASE]} =~ ^(PREPARED|INCIDENT_CAPTURED|DATA_RESTORED|RUNTIME_RESTORED|COMMITTED)$ ]] \
    || die "invalid paired restore phase"
  [[ ${journal[ORIGINAL_MAINTENANCE]} == 0 || ${journal[ORIGINAL_MAINTENANCE]} == 1 ]] \
    || die "invalid original maintenance flag"
  [[ ${journal[TARGET_SLOT]} =~ ^(blue|green)$ \
    && ${journal[TARGET_TAG]} =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ \
    && ${journal[TARGET_COMMIT]} =~ ^[0-9a-f]{40}$ \
    && ${journal[BACKUP_MANIFEST_SHA256]} =~ ^[0-9a-f]{64}$ \
    && ${journal[CREATED_AT]} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
    || die "paired restore journal identity is invalid"
  (cd "$transaction_dir" && sha256sum --check immutable.sha256 >/dev/null) \
    || die "paired restore immutable checksum failed"
  cmp -s <(grep -v '^PHASE=' "$journal_file") "$transaction_dir/immutable.env" \
    || die "paired restore immutable fields changed"
  local compact expected_id expected_incident expected_audit
  compact=$(date -u -d "${journal[CREATED_AT]}" +%Y%m%dT%H%M%SZ) \
    || die "paired restore creation time is invalid"
  expected_id=paired-$compact-${journal[BACKUP_TRANSACTION_ID]}
  expected_incident=$incident_root/$expected_id
  expected_audit=$log_root/$expected_id-paired-restore.env
  [[ ${journal[TRANSACTION_ID]} == "$expected_id" \
    && ${journal[INCIDENT_DIR]} == "$expected_incident" \
    && ${journal[AUDIT_FILE]} == "$expected_audit" ]] \
    || die "paired restore journal objects are not transaction-bound"
  [[ $(realpath -m -- "${journal[BACKUP_DIR]}") == "${journal[BACKUP_DIR]}" \
    && $(dirname "${journal[BACKUP_DIR]}") == "$(realpath -e -- "$backup_root")" ]] \
    || die "paired restore backup path is not canonical"
  [[ $(realpath -m -- "${journal[INCIDENT_DIR]}") == "${journal[INCIDENT_DIR]}" \
    && $(dirname "${journal[INCIDENT_DIR]}") == "$(realpath -e -- "$incident_root")" ]] \
    || die "paired restore incident path is not canonical"
  [[ $(realpath -m -- "${journal[AUDIT_FILE]}") == "${journal[AUDIT_FILE]}" \
    && $(dirname "${journal[AUDIT_FILE]}") == "$(realpath -e -- "$log_root")" ]] \
    || die "paired restore audit path is not canonical"
}

verify_backup() {
  local requested=$1 output
  output=$(python3 "$backup_verifier" \
    --backup-dir "$requested" --backup-root "$backup_root" \
    --release-root "$release_root" --repository-dir "$repository_dir" \
    --identity-validator "$identity_validator" --scratch-root "$scratch_root" \
    "${allow_test_root[@]}") || die "paired backup preflight failed"
  IFS=$'\t' read -r \
    VERIFIED_BACKUP FROM_TAG FROM_COMMIT TO_TAG TO_COMMIT BACKUP_TRANSACTION_ID \
    <<<"$output"
  [[ -n $VERIFIED_BACKUP && -n $BACKUP_TRANSACTION_ID ]] \
    || die "paired backup verifier returned incomplete identity"
  docker run --rm --entrypoint pg_restore \
    -v "$VERIFIED_BACKUP:/backup:ro" "$postgres_image" \
    --list /backup/database.dump >/dev/null \
    || die "database dump cannot be listed before restore"
  parsed_target=$(bash "$identity_validator" \
    "$VERIFIED_BACKUP/previous-release.env" "$release_root" "$repository_dir") \
    || die "matching application identity is invalid"
  IFS=$'\t' read -r \
    TARGET_SLOT TARGET_TAG TARGET_COMMIT TARGET_API_IMAGE TARGET_WEB_IMAGE \
    TARGET_RELEASE_DIR TARGET_APP_CAPABILITY TARGET_ROLLBACK_ALLOWED_FROM \
    <<<"$parsed_target"
  [[ $TARGET_TAG == "$FROM_TAG" && $TARGET_COMMIT == "$FROM_COMMIT" ]] \
    || die "matching application does not agree with backup manifest"
  VERIFIED_MANIFEST_SHA=$(sha256sum "$VERIFIED_BACKUP/SHA256SUMS" | awk '{print $1}')
}

write_phase() {
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
  if [[ ${PKUBA_TEST_PAIRED_CRASH_POINT:-} == "$1" ]]; then kill -KILL $$; fi
}

if [[ $resume == 0 ]]; then
  [[ ! -e $transaction_dir ]] || die "a paired restore transaction already exists"
  verify_backup "$backup_argument"
  created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  compact=$(date -u -d "$created_at" +%Y%m%dT%H%M%SZ)
  transaction_id=paired-$compact-$BACKUP_TRANSACTION_ID
  incident_dir=$incident_root/$transaction_id
  audit_file=$log_root/$transaction_id-paired-restore.env
  [[ ! -e $incident_dir && ! -e $audit_file ]] \
    || die "paired restore transaction objects already exist"
  original_maintenance=0
  [[ -e $maintenance_file ]] && original_maintenance=1
  staging=$(mktemp -d "$state_dir/.paired-restore-transaction.XXXXXX")
  cat >"$staging/journal.env" <<EOF
JOURNAL_VERSION=1
TRANSACTION_ID=$transaction_id
TRANSACTION_KIND=PAIRED_RESTORE
PHASE=PREPARED
ORIGINAL_MAINTENANCE=$original_maintenance
BACKUP_DIR=$VERIFIED_BACKUP
BACKUP_MANIFEST_SHA256=$VERIFIED_MANIFEST_SHA
BACKUP_TRANSACTION_ID=$BACKUP_TRANSACTION_ID
TARGET_SLOT=$TARGET_SLOT
TARGET_TAG=$TARGET_TAG
TARGET_COMMIT=$TARGET_COMMIT
TARGET_API_IMAGE=$TARGET_API_IMAGE
TARGET_WEB_IMAGE=$TARGET_WEB_IMAGE
TARGET_RELEASE_DIR=$TARGET_RELEASE_DIR
TARGET_APP_CAPABILITY=$TARGET_APP_CAPABILITY
TARGET_ROLLBACK_ALLOWED_FROM=$TARGET_ROLLBACK_ALLOWED_FROM
INCIDENT_DIR=$incident_dir
AUDIT_FILE=$audit_file
CREATED_AT=$created_at
EOF
  grep -v '^PHASE=' "$staging/journal.env" >"$staging/immutable.env"
  (cd "$staging" && sha256sum immutable.env >immutable.sha256)
  chmod 600 "$staging/journal.env" "$staging/immutable.env" "$staging/immutable.sha256"
  for item in "$staging"/*; do sync -f "$item"; done
  sync -f "$staging"
  mv "$staging" "$transaction_dir"
  sync -f "$state_dir"
else
  [[ -d $transaction_dir && -f $journal_file ]] \
    || die "there is no paired restore transaction to resume"
fi

journal_file=$transaction_dir/journal.env
parse_journal
backup_dir=${journal[BACKUP_DIR]}
incident_dir=${journal[INCIDENT_DIR]}
audit_file=${journal[AUDIT_FILE]}
transaction_id=${journal[TRANSACTION_ID]}

persist_maintenance() {
  if [[ ! -e $maintenance_file ]]; then
    printf 'transaction=%s\n' "$transaction_id" >"$maintenance_file.tmp.$$"
    chmod 600 "$maintenance_file.tmp.$$"
    sync -f "$maintenance_file.tmp.$$"
    mv -f "$maintenance_file.tmp.$$" "$maintenance_file"
  fi
  sync -f "$state_dir"
}

fence_all_writers() { bash "$writer_fence"; }

recovery_archive_dir=
on_failure() {
  local status=$?
  trap - EXIT
  set +e
  persist_maintenance
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
  if [[ -d $transaction_dir ]]; then
    cat >"$transaction_dir/recovery-required.env.tmp" <<EOF
RESULT=RECOVERY_REQUIRED
TRANSACTION_ID=$transaction_id
FAILED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
    chmod 600 "$transaction_dir/recovery-required.env.tmp" 2>/dev/null
    sync -f "$transaction_dir/recovery-required.env.tmp" 2>/dev/null
    mv -f "$transaction_dir/recovery-required.env.tmp" \
      "$transaction_dir/recovery-required.env" 2>/dev/null
    sync -f "$transaction_dir" 2>/dev/null
  fi
  echo "PAIRED RESTORE FAILED. Maintenance, the writer fence and transaction evidence remain." >&2
  (( status != 0 )) || status=1
  exit "$status"
}
trap on_failure EXIT

# A valid transaction always establishes maintenance and stops both slots'
# writers before state recovery, Compose up, or shared DB/media/archive writes.
persist_maintenance
fence_all_writers || die "could not establish the two-slot writer fence"
crash_point after-writer-fence

# A new restore is fully preflighted before this transaction exists.  A resumed
# restore, however, may follow a host restart where Docker has brought writers
# back.  Re-establish the durable fence first, then repeat the expensive backup
# and release-identity checks while no application writer can mutate shared
# state.  The immutable journal binds the second check to the exact object that
# was approved before the first side effect.
verify_backup "$backup_dir"
[[ $VERIFIED_BACKUP == "$backup_dir" \
  && $VERIFIED_MANIFEST_SHA == "${journal[BACKUP_MANIFEST_SHA256]}" \
  && $BACKUP_TRANSACTION_ID == "${journal[BACKUP_TRANSACTION_ID]}" \
  && $TARGET_SLOT == "${journal[TARGET_SLOT]}" \
  && $TARGET_TAG == "${journal[TARGET_TAG]}" \
  && $TARGET_COMMIT == "${journal[TARGET_COMMIT]}" \
  && $TARGET_API_IMAGE == "${journal[TARGET_API_IMAGE]}" \
  && $TARGET_WEB_IMAGE == "${journal[TARGET_WEB_IMAGE]}" \
  && $TARGET_RELEASE_DIR == "${journal[TARGET_RELEASE_DIR]}" ]] \
  || die "paired backup or matching application changed after transaction creation"

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

verify_incident_snapshot() {
  [[ -d $incident_dir && ! -L $incident_dir \
    && -f $incident_dir/SUCCESS && ! -L $incident_dir/SUCCESS ]] || return 1
  local required=(database.dump private-media.tar.gz archive-staging.tar.gz \
    before-current.env before-upstreams.caddy SHA256SUMS SUCCESS)
  local item
  for item in "${required[@]}"; do
    [[ -f $incident_dir/$item && ! -L $incident_dir/$item ]] || return 1
  done
  (cd "$incident_dir" && sha256sum --check SHA256SUMS >/dev/null) || return 1
  local manifest_sha
  manifest_sha=$(sha256sum "$incident_dir/SHA256SUMS" | awk '{print $1}') || return 1
  local key value
  local -A success=()
  while IFS='=' read -r key value; do
    [[ $key =~ ^(TRANSACTION_ID|MANIFEST_SHA256|COMMITTED_AT)$ \
      && ! -v "success[$key]" && $value != *$'\n'* ]] || return 1
    success[$key]=$value
  done <"$incident_dir/SUCCESS"
  [[ ${#success[@]} -eq 3 \
    && ${success[TRANSACTION_ID]:-} == "$transaction_id" \
    && ${success[MANIFEST_SHA256]:-} == "$manifest_sha" \
    && ${success[COMMITTED_AT]:-} =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
    || return 1
  docker run --rm --entrypoint pg_restore \
    -v "$incident_dir:/incident:ro" "$postgres_image" \
    --list /incident/database.dump >/dev/null || return 1
  tar -tzf "$incident_dir/private-media.tar.gz" >/dev/null || return 1
  tar -tzf "$incident_dir/archive-staging.tar.gz" >/dev/null || return 1
}

capture_incident_snapshot() {
  if verify_incident_snapshot; then return 0; fi
  [[ ! -e $incident_dir || ( -d $incident_dir && ! -L $incident_dir ) ]] \
    || die "incident snapshot path is unsafe"
  rm -rf -- "$incident_dir"
  local staging=$incident_dir.preparing
  rm -rf -- "$staging"
  mkdir "$staging"
  docker exec "$db_container" sh -ec \
    'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    >"$staging/database.dump"
  docker run --rm --entrypoint sh \
    -v "$media_volume:/source:ro" -v "$staging:/incident" "$postgres_image" \
    -ec 'tar -C /source -czf /incident/private-media.tar.gz .'
  docker run --rm --entrypoint sh \
    -v "$archive_volume:/source:ro" -v "$staging:/incident" "$postgres_image" \
    -ec 'tar -C /source -czf /incident/archive-staging.tar.gz .'
  if [[ -f $current_state ]]; then cp "$current_state" "$staging/before-current.env";
  else cp "$backup_dir/previous-release.env" "$staging/before-current.env"; fi
  if [[ -f $upstreams_file ]]; then cp "$upstreams_file" "$staging/before-upstreams.caddy";
  else cp "$backup_dir/previous-release.env" "$staging/before-upstreams.caddy"; fi
  (
    cd "$staging"
    sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
      before-current.env before-upstreams.caddy >SHA256SUMS
    sha256sum --check SHA256SUMS >/dev/null
  )
  docker run --rm --entrypoint pg_restore \
    -v "$staging:/incident:ro" "$postgres_image" \
    --list /incident/database.dump >/dev/null
  tar -tzf "$staging/private-media.tar.gz" >/dev/null
  tar -tzf "$staging/archive-staging.tar.gz" >/dev/null
  for item in "$staging"/*; do sync -f "$item"; done
  sync -f "$staging"
  local manifest_sha
  manifest_sha=$(sha256sum "$staging/SHA256SUMS" | awk '{print $1}')
  cat >"$staging/SUCCESS.tmp" <<EOF
TRANSACTION_ID=$transaction_id
MANIFEST_SHA256=$manifest_sha
COMMITTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  chmod 600 "$staging/SUCCESS.tmp"
  sync -f "$staging/SUCCESS.tmp"
  mv "$staging/SUCCESS.tmp" "$staging/SUCCESS"
  sync -f "$staging"
  mv "$staging" "$incident_dir"
  sync -f "$incident_root"
  verify_incident_snapshot || die "incident snapshot did not commit durably"
}

if [[ ${journal[PHASE]} == PREPARED ]]; then
  capture_incident_snapshot
  crash_point after-incident-snapshot
  write_phase INCIDENT_CAPTURED
fi

restore_volume() {
  local volume=$1 archive=$2 file_manifest=$3
  docker run --rm --entrypoint sh \
    -v "$volume:/target" -v "$backup_dir:/backup:ro" "$postgres_image" \
    -ec "find /target -mindepth 1 -delete; tar -C /target -xzf /backup/$archive; cd /target; if [ -s /backup/$file_manifest ]; then sha256sum -c /backup/$file_manifest; else [ -z \"\$(find . -type f -print -quit)\" ]; fi"
}

if [[ ${journal[PHASE]} == INCIDENT_CAPTURED ]]; then
  docker exec "$db_container" sh -ec \
    'dropdb --if-exists --force -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
  docker exec -i "$db_container" sh -ec \
    'pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    <"$backup_dir/database.dump"
  docker exec "$db_container" sh -ec \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT 1"' \
    | grep -qx 1
  restore_volume "$media_volume" private-media.tar.gz private-media.files.sha256
  restore_volume "$archive_volume" archive-staging.tar.gz archive-staging.files.sha256
  docker run --rm --network "container:$db_container" --env-file "$env_file" \
    -e PKUBA_RELEASE_TAG="${journal[TARGET_TAG]}" \
    -e PKUBA_GIT_COMMIT="${journal[TARGET_COMMIT]}" \
    "${journal[TARGET_API_IMAGE]}" \
    python manage.py audit_season_integrity --json \
    >"$incident_dir/season-integrity-after-restore.json.tmp"
  [[ -s $incident_dir/season-integrity-after-restore.json.tmp ]] \
    || die "restored season integrity audit is empty"
  sync -f "$incident_dir/season-integrity-after-restore.json.tmp"
  mv -f "$incident_dir/season-integrity-after-restore.json.tmp" \
    "$incident_dir/season-integrity-after-restore.json"
  sync -f "$incident_dir"
  crash_point after-data-restore
  write_phase DATA_RESTORED
fi

slot_api_port() { if [[ $1 == blue ]]; then echo "$blue_api_port"; else echo "$green_api_port"; fi; }
slot_web_port() { if [[ $1 == blue ]]; then echo "$blue_web_port"; else echo "$green_web_port"; fi; }
worker_services=(expiry scoresheet-worker archive-worker)
[[ $email_profile == 1 ]] && worker_services+=(outbox)

compose_slot() {
  local profiles=()
  [[ $email_profile == 1 ]] && profiles=(--profile email)
  env \
    PKUBA_SLOT_NAME="pkuba-${journal[TARGET_SLOT]}" \
    PKUBA_SLOT_API_PORT="$(slot_api_port "${journal[TARGET_SLOT]}")" \
    PKUBA_SLOT_WEB_PORT="$(slot_web_port "${journal[TARGET_SLOT]}")" \
    PKUBA_API_IMAGE="${journal[TARGET_API_IMAGE]}" \
    PKUBA_WEB_IMAGE="${journal[TARGET_WEB_IMAGE]}" \
    PKUBA_RELEASE_TAG="${journal[TARGET_TAG]}" \
    PKUBA_GIT_COMMIT="${journal[TARGET_COMMIT]}" \
    PKUBA_ENV_FILE="$env_file" PKUBA_MEDIA_VOLUME="$media_volume" \
    PKUBA_ARCHIVE_VOLUME="$archive_volume" PKUBA_RUNTIME_NETWORK="$runtime_network" \
    docker compose --project-name "pkuba-${journal[TARGET_SLOT]}" \
      --project-directory "${journal[TARGET_RELEASE_DIR]}" --env-file "$env_file" \
      -f "${journal[TARGET_RELEASE_DIR]}/infra/compose.prod.slot.yml" \
      "${profiles[@]}" "$@"
}

compose_gateway() {
  env PKUBA_DEPLOY_STATE_DIR="$state_dir" PKUBA_RUNTIME_NETWORK="$runtime_network" \
    PKUBA_CADDY_IMAGE="$caddy_image" \
    docker compose --project-name "$gateway_project" \
      --project-directory "${journal[TARGET_RELEASE_DIR]}" --env-file "$env_file" \
      -f "${journal[TARGET_RELEASE_DIR]}/infra/compose.prod.gateway.yml" "$@"
}

wait_internal_ready() {
  local api_body web_body
  for _ in $(seq 1 60); do
    api_body=$(curl --silent --show-error -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$(slot_api_port "${journal[TARGET_SLOT]}")/api/v1/health/ready" || true)
    web_body=$(curl --silent --show-error \
      "http://127.0.0.1:$(slot_web_port "${journal[TARGET_SLOT]}")/_deployment/ready" || true)
    if [[ $api_body == *"${journal[TARGET_TAG]}"* \
      && $api_body == *"${journal[TARGET_COMMIT]}"* \
      && $web_body == *"${journal[TARGET_TAG]}"* \
      && $web_body == *"${journal[TARGET_COMMIT]}"* \
      && $api_body == *'"status"'*ok* && $web_body == *'"status"'*ok* ]]; then return 0; fi
    sleep 3
  done
  return 1
}

assert_services_stable() {
  local service container after
  local services=(api web "${worker_services[@]}")
  declare -A counts=()
  for service in "${services[@]}"; do
    container=$(compose_slot ps -q "$service")
    [[ -n $container && $(docker inspect --format '{{.State.Running}}' "$container") == true ]] \
      || die "restored service is not running: $service"
    counts[$service]=$(docker inspect --format '{{.RestartCount}}' "$container")
  done
  sleep "$stability_seconds"
  for service in "${services[@]}"; do
    container=$(compose_slot ps -q "$service")
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${counts[$service]} ]] || die "restored service restarted: $service"
  done
}

atomic_install() {
  local source=$1 destination=$2 mode=${3:-600}
  local temporary=$destination.paired.$$
  cp "$source" "$temporary"
  chmod "$mode" "$temporary"
  sync -f "$temporary"
  mv -f "$temporary" "$destination"
  sync -f "$(dirname "$destination")"
}

public_domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file" | tail -n 1 | tr -d '\r"')
[[ $isolated == 1 || $public_domain =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid PKUBA_DOMAIN"

wait_gateway() {
  local mode=$1 api_path=/api/v1/health/live api_body web_body
  [[ $mode == ready ]] && api_path=/api/v1/health/ready
  for _ in $(seq 1 "$probe_attempts"); do
    api_body=$(curl --fail --silent --show-error "https://api.$public_domain$api_path" || true)
    web_body=$(curl --fail --silent --show-error \
      "https://admin.$public_domain/_deployment/ready" || true)
    if [[ $api_body == *"${journal[TARGET_TAG]}"* \
      && $api_body == *"${journal[TARGET_COMMIT]}"* \
      && $web_body == *"${journal[TARGET_TAG]}"* \
      && $web_body == *"${journal[TARGET_COMMIT]}"* ]]; then
      [[ $mode == live || ( $api_body == *'"status"'*ok* && $web_body == *'"status"'*ok* ) ]] \
        && return 0
    fi
    sleep "$probe_delay_seconds"
  done
  return 1
}

if [[ ${journal[PHASE]} == DATA_RESTORED ]]; then
  if [[ $isolated == 1 ]]; then
    write_phase RUNTIME_RESTORED
  else
    compose_slot up -d --no-deps web "${worker_services[@]}" api
    wait_internal_ready || die "matching application did not become ready"
    assert_services_stable
    cat >"$transaction_dir/next-current.env" <<EOF
ACTIVE_SLOT=${journal[TARGET_SLOT]}
CURRENT_TAG=${journal[TARGET_TAG]}
CURRENT_COMMIT=${journal[TARGET_COMMIT]}
CURRENT_API_IMAGE=${journal[TARGET_API_IMAGE]}
CURRENT_WEB_IMAGE=${journal[TARGET_WEB_IMAGE]}
CURRENT_RELEASE_DIR=${journal[TARGET_RELEASE_DIR]}
CURRENT_APP_CAPABILITY=${journal[TARGET_APP_CAPABILITY]}
EOF
    if [[ ${journal[TARGET_ROLLBACK_ALLOWED_FROM]} != - ]]; then
      printf 'ROLLBACK_ALLOWED_FROM_CAPABILITY=%s\n' \
        "${journal[TARGET_ROLLBACK_ALLOWED_FROM]}" >>"$transaction_dir/next-current.env"
    fi
    printf 'DATA_RESTORED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      >>"$transaction_dir/next-current.env"
    cat >"$transaction_dir/next-upstreams.caddy" <<EOF
(active_api) {
\treverse_proxy pkuba-${journal[TARGET_SLOT]}-api:8000
}

(active_web) {
\treverse_proxy pkuba-${journal[TARGET_SLOT]}-web:8080
}
EOF
    chmod 600 "$transaction_dir/next-current.env"
    chmod 644 "$transaction_dir/next-upstreams.caddy"
    sync -f "$transaction_dir/next-current.env"
    sync -f "$transaction_dir/next-upstreams.caddy"
    atomic_install "$transaction_dir/next-current.env" "$current_state"
    atomic_install "$transaction_dir/next-upstreams.caddy" "$upstreams_file" 644
    compose_gateway exec -T gateway caddy reload --config /etc/caddy/Caddyfile
    wait_gateway live || die "stable gateway did not expose the matching application"
    crash_point after-runtime-restore
    write_phase RUNTIME_RESTORED
  fi
fi

declare -A completion=()
parse_completion() {
  completion=()
  local key value
  while IFS='=' read -r key value; do
    [[ $key =~ ^[A-Z0-9_]+$ && ! -v "completion[$key]" ]] \
      || die "invalid paired completion payload"
    case "$key" in
      TRANSACTION_ID|RESULT|AUDIT_FILE|BACKUP_DIR|BACKUP_MANIFEST_SHA256|TARGET_TAG|TARGET_COMMIT|INCIDENT_DIR|DATABASE_RESTORED|MEDIA_RESTORED|ARCHIVE_RESTORED|COMPLETED_AT)
        completion[$key]=$value ;;
      *) die "unexpected paired completion key: $key" ;;
    esac
  done <"$1"
  for key in TRANSACTION_ID RESULT AUDIT_FILE BACKUP_DIR BACKUP_MANIFEST_SHA256 \
    TARGET_TAG TARGET_COMMIT INCIDENT_DIR DATABASE_RESTORED MEDIA_RESTORED \
    ARCHIVE_RESTORED COMPLETED_AT; do
    [[ -v "completion[$key]" ]] || die "paired completion is missing $key"
  done
  [[ ${completion[TRANSACTION_ID]} == "$transaction_id" \
    && ${completion[RESULT]} == PAIRED_DATA_RESTORED \
    && ${completion[AUDIT_FILE]} == "$audit_file" \
    && ${completion[BACKUP_DIR]} == "$backup_dir" \
    && ${completion[BACKUP_MANIFEST_SHA256]} == "${journal[BACKUP_MANIFEST_SHA256]}" \
    && ${completion[TARGET_TAG]} == "${journal[TARGET_TAG]}" \
    && ${completion[TARGET_COMMIT]} == "${journal[TARGET_COMMIT]}" \
    && ${completion[INCIDENT_DIR]} == "$incident_dir" \
    && ${completion[DATABASE_RESTORED]} == 1 \
    && ${completion[MEDIA_RESTORED]} == 1 \
    && ${completion[ARCHIVE_RESTORED]} == 1 ]] \
    || die "paired completion does not match its transaction"
}

finalize_completed() {
  local completed=$1
  (cd "$completed" && sha256sum --check completion.sha256 >/dev/null) \
    || die "paired completion checksum failed"
  parse_completion "$completed/completion.env"
  if [[ -e $audit_file ]]; then
    [[ -f $audit_file && ! -L $audit_file ]] || die "paired restore audit target is unsafe"
    cmp -s "$completed/completion.env" "$audit_file" \
      || die "paired restore audit contains different content"
  else
    atomic_install "$completed/completion.env" "$audit_file"
  fi
  atomic_install "$completed/completion.env" "$incident_dir/RESTORE_COMPLETED"
  sync -f "$log_root"
  sync -f "$incident_dir"
  crash_point after-completion-audit
}

archive_completed() {
  local completed=$1
  recovery_archive_dir=$journal_archive_root/$transaction_id
  [[ ! -e $recovery_archive_dir ]] || die "paired restore transaction archive exists"
  mv "$completed" "$recovery_archive_dir"
  sync -f "$state_dir"
  sync -f "$journal_archive_root"
  crash_point after-completed-archive
}

if [[ ${journal[PHASE]} == RUNTIME_RESTORED ]]; then
  # The gateway exposes only the versioned API/Web health probes during
  # maintenance.  Prove the restored release twice before creating any success
  # payload; normal business routes remain fenced until every authoritative
  # state and audit file below is durable.
  if [[ $isolated != 1 ]]; then
    wait_gateway ready || die "public readiness failed during paired restore"
    sleep "$stability_seconds"
    wait_gateway ready || die "public readiness was not stable during paired restore"
  fi
  completion_file=$transaction_dir/completion.env
  if [[ ! -e $completion_file ]]; then
    cat >"$completion_file.tmp" <<EOF
TRANSACTION_ID=$transaction_id
RESULT=PAIRED_DATA_RESTORED
AUDIT_FILE=$audit_file
BACKUP_DIR=$backup_dir
BACKUP_MANIFEST_SHA256=${journal[BACKUP_MANIFEST_SHA256]}
TARGET_TAG=${journal[TARGET_TAG]}
TARGET_COMMIT=${journal[TARGET_COMMIT]}
INCIDENT_DIR=$incident_dir
DATABASE_RESTORED=1
MEDIA_RESTORED=1
ARCHIVE_RESTORED=1
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
  fi
  (cd "$transaction_dir" && sha256sum --check completion.sha256 >/dev/null)
  parse_completion "$completion_file"
  crash_point after-completion-payload
  write_phase COMMITTED
  crash_point after-paired-committed
fi

if [[ ${journal[PHASE]} == COMMITTED ]]; then
  [[ -f $transaction_dir/completion.env && -f $transaction_dir/completion.sha256 ]] \
    || die "committed paired restore has no completion payload"
  [[ ! -e $completed_transaction_dir ]] || die "completed paired restore path already exists"
  mv "$transaction_dir" "$completed_transaction_dir"
  sync -f "$state_dir"
  crash_point after-transaction-rename
  finalize_completed "$completed_transaction_dir"
  if [[ ${journal[ORIGINAL_MAINTENANCE]} == 0 ]]; then
    rm -f "$maintenance_file"
    sync -f "$state_dir"
    crash_point after-maintenance-removal
  fi
  archive_completed "$completed_transaction_dir"
fi

trap - EXIT
echo "PKUBA_PAIRED_RESTORE_RESULT=success"
echo "PKUBA_ACTIVE_TAG=${journal[TARGET_TAG]}"
echo "PKUBA_INCIDENT_SNAPSHOT=$incident_dir"
