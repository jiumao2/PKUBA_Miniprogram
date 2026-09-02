#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() { echo "production toolset error: $*" >&2; exit 1; }

test_mode=${PKUBA_TEST_ALLOW_NON_ROOT_TOOLSET:-${PKUBA_TEST_ALLOW_NON_ROOT:-0}}
[[ ${EUID:-$(id -u)} -eq 0 || $test_mode == 1 ]] \
  || die "this command must run as root"

config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # Root-owned paths and feature switches only; never credentials.
  # shellcheck disable=SC1090
  source "$config_file"
fi

deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
release_root=$deploy_root/releases
toolset_root=${PKUBA_RELEASE_TOOLSET_ROOT:-${PKUBA_PRODUCTION_TOOLSET_ROOT:-/usr/local/libexec/pkuba/toolsets}}
toolset_release_root=$toolset_root/releases
toolset_legacy_root=$toolset_root/legacy
toolset_current=${PKUBA_PRODUCTION_TOOLSET_CURRENT:-$toolset_root/current}
local_sbin_dir=${PKUBA_RELEASE_TOOLS_SBIN_ROOT:-${PKUBA_LOCAL_SBIN_DIR:-/usr/local/sbin}}
local_libexec_dir=${PKUBA_RELEASE_TOOLS_LIBEXEC_ROOT:-${PKUBA_LOCAL_LIBEXEC_DIR:-/usr/local/libexec/pkuba}}
sudoers_file=${PKUBA_DEPLOY_SUDOERS_FILE:-/etc/sudoers.d/pkuba-deploy}
systemd_dir=${PKUBA_SYSTEMD_DIR:-/etc/systemd/system}
deploy_user=${PKUBA_DEPLOY_USER:-pkuba-deploy}
lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-$toolset_current/libexec/acquire-deploy-lock.py}

# Complete, fixed versioned production toolset. A release cannot add, remove or
# redirect an installed host tool through data in the release itself.
toolset_entries=(
  'scripts/prod/deploy-gateway.sh|100644|sbin/pkuba-deploy-gateway|755|bash'
  'scripts/prod/record-deploy-ssh-verification.sh|100755|sbin/pkuba-record-deploy-ssh-verification|700|bash'
  'scripts/prod/finalize-deploy-ssh.sh|100755|sbin/pkuba-finalize-deploy-ssh|700|bash'
  'scripts/prod/deploy-blue-green.sh|100644|sbin/pkuba-deploy-blue-green|755|bash'
  'scripts/prod/rollback-retained-application.sh|100755|sbin/pkuba-rollback-retained-application|700|bash'
  'scripts/prod/recover-release-transaction.sh|100755|sbin/pkuba-recover-release-transaction|700|bash'
  'scripts/prod/restore-paired-data.sh|100644|sbin/pkuba-restore-paired-data|700|bash'
  'scripts/prod/start-current-application.sh|100755|sbin/pkuba-start-current-application|700|bash'
  'scripts/prod/backup-current-server.sh|100644|sbin/pkuba-backup-current|700|bash'
  'scripts/prod/sync-release-tools.sh|100644|sbin/pkuba-sync-release-tools|700|bash'
  'scripts/prod/acquire-deploy-lock.py|100644|libexec/acquire-deploy-lock.py|700|python'
  'scripts/prod/fence-deploy-writers.sh|100755|libexec/fence-deploy-writers.sh|700|bash'
  'scripts/prod/verify-paired-backup.py|100644|libexec/verify-paired-backup.py|700|python'
  'scripts/prod/parse-release-state.sh|100755|libexec/parse-release-state.sh|755|bash'
  'scripts/prod/parse-release-contract.sh|100755|libexec/parse-release-contract.sh|755|bash'
  'scripts/prod/derive-release-capability.sh|100755|libexec/derive-release-capability.sh|755|bash'
  'scripts/prod/validate-release-identity.sh|100755|libexec/validate-release-identity.sh|755|bash'
  'scripts/prod/check-app-capability.sh|100755|libexec/check-app-capability.sh|755|bash'
)

for command_name in awk bash chmod cmp cp date dirname find git grep install ln \
  mkdir mktemp mv python3 readlink realpath rm sha256sum sort stat sync wc; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done

fail_point() {
  [[ ${PKUBA_TEST_TOOLSET_FAIL_POINT:-} != "$1" ]] \
    || die "injected toolset failure: $1"
  if [[ $1 == before-toolset-pointer-rename \
    && ${PKUBA_TEST_FAIL_TOOLSET_SWITCH:-0} == 1 ]]; then
    die "injected toolset pointer failure"
  fi
}

require_safe_roots() {
  [[ -d $state_dir && ! -L $state_dir ]] \
    || die "missing regular deployment state directory"
  [[ -d $repository_dir/.git && ! -L $repository_dir ]] \
    || die "missing read-only deployment repository"
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G:%a' "$state_dir") == root:root:700 ]] \
      || die "deployment state directory must be root:root mode 700"
  fi
}

ensure_toolset_directory() {
  local directory=$1 mode=$2 created=0
  if [[ -e $directory || -L $directory ]]; then
    [[ -d $directory && ! -L $directory ]] \
      || die "toolset path is not a regular directory: $directory"
  else
    mkdir -p "$directory"
    created=1
  fi
  if [[ $created == 1 ]]; then
    chmod "$mode" "$directory"
  else
    [[ $(stat -c '%a' "$directory") == "$mode" ]] \
      || die "existing toolset directory has an unsafe mode: $directory"
  fi
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G' "$directory") == root:root ]] \
      || die "toolset directory must be root-owned: $directory"
  fi
}

validate_stable_roots() {
  local directory mode
  for directory in "$local_sbin_dir" "$local_libexec_dir"; do
    [[ -d $directory && ! -L $directory ]] \
      || die "stable production tool root is not a regular directory: $directory"
    mode=$(stat -c '%a' "$directory")
    [[ $mode == 755 || $mode == 711 ]] \
      || die "stable production tool root is not safely traversable: $directory"
    if [[ $test_mode != 1 ]]; then
      [[ $(stat -c '%U:%G' "$directory") == root:root ]] \
        || die "stable production tool root must be root-owned: $directory"
    fi
  done
}

validate_commit_on_main() {
  local release_commit=$1 main_ref=origin/main
  [[ $release_commit =~ ^[0-9a-f]{40}$ ]] || die "invalid release commit"
  if [[ ${PKUBA_TEST_SKIP_FETCH:-0} == 1 ]]; then
    main_ref=main
  else
    git -C "$repository_dir" fetch --force origin main
  fi
  git -C "$repository_dir" merge-base --is-ancestor "$release_commit" "$main_ref" \
    || die "toolset commit is not reachable from origin/main"
}

validate_release_identity() {
  local release_tag=$1 release_commit=$2 release_dir
  [[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid release tag"
  [[ $release_commit =~ ^[0-9a-f]{40}$ ]] || die "invalid release commit"
  if [[ ${PKUBA_TEST_SKIP_FETCH:-0} != 1 ]]; then
    git -C "$repository_dir" fetch --force origin main \
      "+refs/tags/$release_tag:refs/tags/$release_tag"
  fi
  [[ $(git -C "$repository_dir" rev-parse "$release_tag^{commit}") == "$release_commit" ]] \
    || die "release tag does not resolve to requested commit"
  validate_commit_on_main "$release_commit"
  release_dir=$release_root/$release_tag
  if [[ -e $release_dir || -L $release_dir ]]; then
    [[ -d $release_dir && ! -L $release_dir ]] || die "invalid release directory"
    [[ -d $release_dir/.git || -f $release_dir/.git ]] || die "release directory is not a worktree"
    [[ $(git -C "$release_dir" rev-parse HEAD) == "$release_commit" ]] \
      || die "release directory points to another commit"
  else
    git -C "$repository_dir" worktree add --detach "$release_dir" "$release_commit" \
      >/dev/null
  fi
  printf '%s\n' "$release_dir"
}

validate_source_entry() {
  local source_root=$1 release_commit=$2 source_rel=$3 expected_git_mode=$4 kind=$5
  local source_file=$source_root/$source_rel expected_blob actual_blob
  local tree_entry actual_git_mode object_type tree_blob committed_path
  [[ -f $source_file && ! -L $source_file ]] \
    || die "toolset source must be a regular non-symlink file: $source_rel"
  expected_blob=$(git -C "$source_root" rev-parse "$release_commit:$source_rel") \
    || die "toolset source is absent from release commit: $source_rel"
  [[ $(git -C "$source_root" cat-file -t "$expected_blob") == blob ]] \
    || die "toolset source is not a Git blob: $source_rel"
  actual_blob=$(git -C "$source_root" hash-object -- "$source_file")
  [[ $actual_blob == "$expected_blob" ]] \
    || die "toolset source differs from the release commit: $source_rel"
  tree_entry=$(git -C "$source_root" ls-tree "$release_commit" -- "$source_rel")
  read -r actual_git_mode object_type tree_blob committed_path <<<"$tree_entry"
  [[ $actual_git_mode == "$expected_git_mode" && $object_type == blob \
    && $tree_blob == "$expected_blob" && $committed_path == "$source_rel" ]] \
    || die "toolset commit mode or blob is invalid: $source_rel"
  case "$kind" in
    bash) bash -n "$source_file" ;;
    python)
      python3 - "$source_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
compile(path.read_bytes(), str(path), "exec")
PY
      ;;
    *) die "unknown toolset source kind: $kind" ;;
  esac
}

write_toolset_metadata() {
  local target_dir=$1 release_commit=$2 entry source_rel git_mode target_rel mode kind
  cat >"$target_dir/TOOLSET.env" <<EOF
TOOLSET_VERSION=1
RELEASE_COMMIT=$release_commit
ENTRY_COUNT=${#toolset_entries[@]}
EOF
  : >"$target_dir/SHA256SUMS"
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    (cd "$target_dir" && sha256sum "$target_rel") >>"$target_dir/SHA256SUMS"
  done
  chmod 600 "$target_dir/TOOLSET.env" "$target_dir/SHA256SUMS"
}

validate_installed_toolset() {
  local target_dir=$1 release_commit=$2 entry source_rel git_mode target_rel mode kind
  local expected_count=${#toolset_entries[@]} actual_files actual_count actual_dirs expected_target
  [[ -d $target_dir && ! -L $target_dir ]] || die "toolset release directory is invalid"
  [[ $(stat -c '%a' "$target_dir") == 711 \
    && $(stat -c '%a' "$target_dir/sbin") == 711 \
    && $(stat -c '%a' "$target_dir/libexec") == 700 ]] \
    || die "toolset directory modes are invalid"
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G' "$target_dir") == root:root \
      && $(stat -c '%U:%G' "$target_dir/sbin") == root:root \
      && $(stat -c '%U:%G' "$target_dir/libexec") == root:root ]] \
      || die "toolset directories must be root-owned"
  fi
  grep -Fxq 'TOOLSET_VERSION=1' "$target_dir/TOOLSET.env" \
    || die "toolset identity version is invalid"
  grep -Fxq "RELEASE_COMMIT=$release_commit" "$target_dir/TOOLSET.env" \
    || die "toolset identity commit is invalid"
  grep -Fxq "ENTRY_COUNT=$expected_count" "$target_dir/TOOLSET.env" \
    || die "toolset identity entry count is invalid"
  [[ $(wc -l <"$target_dir/TOOLSET.env") -eq 3 ]] \
    || die "toolset identity contains unexpected fields"
  [[ $(wc -l <"$target_dir/SHA256SUMS") -eq $expected_count ]] \
    || die "toolset checksum manifest has an unexpected entry count"
  (cd "$target_dir" && sha256sum --check SHA256SUMS >/dev/null) \
    || die "toolset checksum verification failed"
  actual_files=$(find "$target_dir" -mindepth 1 -type f -printf '%P\n' | sort)
  actual_dirs=$(find "$target_dir" -mindepth 1 -type d -printf '%P\n' | sort)
  actual_count=$(wc -l <<<"$actual_files")
  [[ $actual_count -eq $((expected_count + 2)) ]] \
    || die "toolset contains unexpected regular files"
  [[ -z $(find "$target_dir" -mindepth 1 -type l -print -quit) ]] \
    || die "toolset contains a symbolic link"
  [[ $actual_dirs == $'libexec\nsbin' ]] \
    || die "toolset contains unexpected directories"
  for expected_target in TOOLSET.env SHA256SUMS; do
    grep -Fxq "$expected_target" <<<"$actual_files" \
      || die "toolset is missing $expected_target"
  done
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    [[ -f $target_dir/$target_rel && ! -L $target_dir/$target_rel ]] \
      || die "toolset entry is missing: $target_rel"
    [[ $(stat -c '%a' "$target_dir/$target_rel") == "$mode" ]] \
      || die "toolset entry mode is invalid: $target_rel"
    grep -Fxq "$target_rel" <<<"$actual_files" \
      || die "toolset file list is incomplete: $target_rel"
  done
  [[ $(stat -c '%a' "$target_dir/TOOLSET.env") == 600 \
    && $(stat -c '%a' "$target_dir/SHA256SUMS") == 600 ]] \
    || die "toolset metadata modes are invalid"
  for upstream_writer in pkuba-deploy-blue-green pkuba-rollback-retained-application \
    pkuba-restore-paired-data; do
    ! grep -Fq '\treverse_proxy' "$target_dir/sbin/$upstream_writer" \
      || die "$upstream_writer writes a literal backslash-t into Caddy configuration"
  done
}

install_toolset_from_source() (
  local source_root=$1 release_commit=$2 target_dir staging entry
  local source_rel git_mode target_rel mode kind
  source_root=$(realpath "$source_root")
  [[ -d $source_root && ! -L $source_root ]] || die "invalid toolset source root"
  [[ $(git -C "$source_root" rev-parse HEAD) == "$release_commit" ]] \
    || die "toolset source worktree is not the requested commit"
  validate_commit_on_main "$release_commit"
  validate_stable_roots
  ensure_toolset_directory "$toolset_root" 711
  ensure_toolset_directory "$toolset_release_root" 711
  ensure_toolset_directory "$toolset_legacy_root" 711
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    validate_source_entry "$source_root" "$release_commit" "$source_rel" "$git_mode" "$kind"
  done
  target_dir=$toolset_release_root/$release_commit
  if [[ -e $target_dir || -L $target_dir ]]; then
    validate_installed_toolset "$target_dir" "$release_commit"
    printf '%s\n' "$target_dir"
    return 0
  fi
  staging=$(mktemp -d "$toolset_release_root/.toolset-$release_commit.XXXXXX")
  cleanup_toolset_stage() {
    local status=$?
    trap - EXIT
    [[ -z ${staging:-} ]] || rm -rf -- "$staging"
    exit "$status"
  }
  trap cleanup_toolset_stage EXIT
  mkdir -p "$staging/sbin" "$staging/libexec"
  chmod 711 "$staging" "$staging/sbin"
  chmod 700 "$staging/libexec"
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    install -m "$mode" "$source_root/$source_rel" "$staging/$target_rel"
  done
  write_toolset_metadata "$staging" "$release_commit"
  validate_installed_toolset "$staging" "$release_commit"
  while IFS= read -r payload; do sync -f "$payload"; done \
    < <(find "$staging" -type f -print | sort)
  sync -f "$staging/sbin" "$staging/libexec" "$staging"
  fail_point after-toolset-stage-sync
  mv "$staging" "$target_dir"
  staging=
  sync -f "$toolset_release_root"
  fail_point after-toolset-release-rename
  validate_installed_toolset "$target_dir" "$release_commit"
  printf '%s\n' "$target_dir"
)

switch_current() {
  local pointer_target=$1 expected_dir=$2 link_tmp=$toolset_root/.current.$$.tmp resolved
  [[ -d $expected_dir && ! -L $expected_dir ]] || die "toolset pointer target is invalid"
  mkdir -p "$toolset_root"
  rm -f -- "$link_tmp"
  ln -s "$pointer_target" "$link_tmp"
  if [[ ${PKUBA_TEST_TOOLSET_FAIL_POINT:-} == before-toolset-pointer-rename \
    || ${PKUBA_TEST_FAIL_TOOLSET_SWITCH:-0} == 1 ]]; then
    rm -f -- "$link_tmp"
    die "injected toolset pointer failure"
  fi
  mv -Tf "$link_tmp" "$toolset_current"
  sync -f "$toolset_root"
  resolved=$(readlink -f "$toolset_current")
  [[ $resolved == "$expected_dir" ]] || die "active toolset pointer did not commit"
}

activate_toolset() {
  local release_commit=$1 target_dir
  target_dir=$toolset_release_root/$release_commit
  validate_installed_toolset "$target_dir" "$release_commit"
  switch_current "releases/$release_commit" "$target_dir"
  validate_installed_toolset "$(readlink -f "$toolset_current")" "$release_commit"
}

stable_destination() {
  local target_rel=$1
  case "$target_rel" in
    sbin/*) printf '%s/%s\n' "$local_sbin_dir" "${target_rel#sbin/}" ;;
    libexec/*) printf '%s/%s\n' "$local_libexec_dir" "${target_rel#libexec/}" ;;
    *) die "invalid installed toolset path: $target_rel" ;;
  esac
}

install_stable_links() {
  local entry source_rel git_mode target_rel mode kind destination link_tmp
  validate_stable_roots
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    destination=$(stable_destination "$target_rel")
    link_tmp=$destination.toolset-link.$$
    rm -f -- "$link_tmp"
    ln -s "$toolset_current/$target_rel" "$link_tmp"
    mv -Tf "$link_tmp" "$destination"
  done
  sync -f "$local_sbin_dir" "$local_libexec_dir"
}

validate_stable_links() {
  local expected_root=$1 entry source_rel git_mode target_rel mode kind destination resolved
  [[ $(stat -c '%a' "$local_libexec_dir") == 755 \
    || $(stat -c '%a' "$local_libexec_dir") == 711 ]] \
    || die "stable libexec directory is not safely traversable"
  [[ $(stat -c '%a' "$toolset_root") == 711 ]] \
    || die "stable toolset traversal modes are invalid"
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    destination=$(stable_destination "$target_rel")
    [[ -L $destination ]] || die "stable production tool is not a symlink: $destination"
    resolved=$(readlink -f "$destination")
    [[ $resolved == "$expected_root/$target_rel" ]] \
      || die "stable production tool resolves outside the active toolset: $destination"
  done
  [[ $(stat -Lc '%a' "$local_sbin_dir/pkuba-deploy-gateway") == 755 ]] \
    || die "deployment gateway is not executable by the forced-command user"
}

with_deploy_lock() {
  local resolved_lock
  resolved_lock=$(realpath "$lock_helper")
  [[ -f $resolved_lock && ! -L $resolved_lock ]] || die "missing secure deployment lock helper"
  exec env PKUBA_DEPLOY_LOCK_HELD=1 \
    PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} \
    python3 "$resolved_lock" --state-dir "$state_dir" --timeout 1800 -- \
    bash "$0" "$@"
}

install_sudoers() {
  local temporary=$sudoers_file.tmp.$$
  command -v visudo >/dev/null 2>&1 || die "missing command: visudo"
  cat >"$temporary" <<EOF
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools verify *
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools deploy *
EOF
  chmod 440 "$temporary"
  visudo --check --file="$temporary" >/dev/null
  sync -f "$temporary"
  mv -f "$temporary" "$sudoers_file"
  sync -f "$(dirname "$sudoers_file")"
}

install_transition_sudoers() {
  local temporary=$sudoers_file.tmp.$$
  command -v visudo >/dev/null 2>&1 || die "missing command: visudo"
  cat >"$temporary" <<EOF
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-deploy-blue-green *
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-record-deploy-ssh-verification *
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools verify *
$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools deploy *
EOF
  chmod 440 "$temporary"
  visudo --check --file="$temporary" >/dev/null
  sync -f "$temporary"
  mv -f "$temporary" "$sudoers_file"
  sync -f "$(dirname "$sudoers_file")"
}

assert_clear_migration_state() {
  local marker
  for marker in maintenance.enabled release-transaction release-transaction-completed \
    paired-restore-transaction paired-restore-completed paired-restore-incomplete.env \
    release-recovery-required.env; do
    [[ ! -e $state_dir/$marker && ! -L $state_dir/$marker ]] \
      || die "production toolset migration requires a clear state: $marker"
  done
  [[ -f $state_dir/current.env && ! -L $state_dir/current.env ]] \
    || die "production toolset migration requires current release state"
}

create_migration_backup() {
  local backup_dir=$1 backup_manifest=$2 entry source_rel git_mode target_rel mode kind
  local destination key
  mkdir -p "$backup_dir/files"
  chmod 700 "$backup_dir" "$backup_dir/files"
  : >"$backup_manifest"
  for entry in "${toolset_entries[@]}"; do
    IFS='|' read -r source_rel git_mode target_rel mode kind <<<"$entry"
    destination=$(stable_destination "$target_rel")
    if [[ $target_rel == sbin/pkuba-sync-release-tools ]]; then
      [[ ! -e $destination && ! -L $destination ]] \
        || die "unexpected pre-existing sync entrypoint: $destination"
      printf 'absent\t-\t%s\t%s\n' "$target_rel" "$destination" >>"$backup_manifest"
    elif [[ -f $destination && ! -L $destination ]]; then
      key=$(printf '%s' "$destination" | sha256sum | awk '{print $1}')
      cp -a "$destination" "$backup_dir/files/$key"
      printf 'present\t%s\t%s\t%s\n' "$key" "$target_rel" "$destination" >>"$backup_manifest"
    else
      die "legacy production tool is not a regular file: $destination"
    fi
  done
  [[ -f $sudoers_file && ! -L $sudoers_file ]] \
    || die "production deploy sudoers must be a regular file"
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G:%a' "$sudoers_file") == root:root:440 ]] \
      || die "production deploy sudoers must be root:root mode 440"
  fi
  key=$(printf '%s' "$sudoers_file" | sha256sum | awk '{print $1}')
  cp -a "$sudoers_file" "$backup_dir/files/$key"
  printf 'present\t%s\tsudoers\t%s\n' "$key" "$sudoers_file" >>"$backup_manifest"
  chmod 600 "$backup_manifest"
  sync -f "$backup_manifest" "$backup_dir/files" "$backup_dir"
}

create_legacy_compatibility_toolset() {
  local backup_dir=$1 backup_manifest=$2 legacy_dir=$3 candidate_dir=$4
  local disposition key target_rel destination
  mkdir -p "$legacy_dir/sbin" "$legacy_dir/libexec"
  chmod 711 "$legacy_dir" "$legacy_dir/sbin"
  chmod 700 "$legacy_dir/libexec"
  while IFS=$'\t' read -r disposition key target_rel destination; do
    [[ $target_rel != sudoers ]] || continue
    if [[ $disposition == present ]]; then
      cp -a "$backup_dir/files/$key" "$legacy_dir/$target_rel"
      cmp -s "$backup_dir/files/$key" "$legacy_dir/$target_rel" \
        || die "legacy compatibility tool differs from its backup: $target_rel"
    elif [[ $target_rel == sbin/pkuba-sync-release-tools ]]; then
      cp -a "$candidate_dir/$target_rel" "$legacy_dir/$target_rel"
      cmp -s "$candidate_dir/$target_rel" "$legacy_dir/$target_rel" \
        || die "legacy compatibility sync differs from the verified candidate"
    else
      die "legacy tool backup is incomplete"
    fi
  done <"$backup_manifest"
  while IFS= read -r payload; do sync -f "$payload"; done \
    < <(find "$legacy_dir" -type f -print | sort)
  sync -f "$legacy_dir/sbin" "$legacy_dir/libexec" "$legacy_dir"
  sync -f "$toolset_legacy_root"
}

restore_migration_backup() {
  local backup_dir=$1 backup_manifest=$2 old_pointer=$3
  local disposition key target_rel destination restore_tmp restore_failed=0
  set +e
  while IFS=$'\t' read -r disposition key target_rel destination; do
    if [[ $disposition == present ]]; then
      restore_tmp=$destination.restore.$$
      if ! cp -a "$backup_dir/files/$key" "$restore_tmp" \
        || ! sync -f "$restore_tmp" \
        || ! mv -Tf "$restore_tmp" "$destination"; then
        restore_failed=1
      fi
    else
      rm -f -- "$destination" || restore_failed=1
    fi
  done <"$backup_manifest"
  if [[ -n $old_pointer ]]; then
    if ! ln -s "$old_pointer" "$toolset_root/.current.rollback.$$" \
      || ! mv -Tf "$toolset_root/.current.rollback.$$" "$toolset_current"; then
      restore_failed=1
    fi
  else
    rm -f -- "$toolset_current" || restore_failed=1
  fi
  sync -f "$toolset_root" "$local_sbin_dir" "$local_libexec_dir" \
    "$(dirname "$sudoers_file")" 2>/dev/null || restore_failed=1
  while IFS=$'\t' read -r disposition key target_rel destination; do
    if [[ $disposition == present ]]; then
      [[ -f $destination && ! -L $destination \
        && $(stat -c '%a' "$destination") == \
          "$(stat -c '%a' "$backup_dir/files/$key")" \
        && $(stat -c '%u:%g' "$destination") == \
          "$(stat -c '%u:%g' "$backup_dir/files/$key")" ]] \
        || restore_failed=1
      cmp -s "$backup_dir/files/$key" "$destination" || restore_failed=1
    else
      [[ ! -e $destination && ! -L $destination ]] || restore_failed=1
    fi
  done <"$backup_manifest"
  if [[ -n $old_pointer ]]; then
    [[ -L $toolset_current && $(readlink "$toolset_current") == "$old_pointer" ]] \
      || restore_failed=1
  else
    [[ ! -e $toolset_current && ! -L $toolset_current ]] || restore_failed=1
  fi
  [[ ${PKUBA_TEST_FAIL_MIGRATION_RESTORE_VERIFY:-0} != 1 ]] \
    || restore_failed=1
  (( restore_failed == 0 ))
}

validate_systemd_resolution() {
  local unit exec_path active_root expected_rel
  active_root=$(readlink -f "$toolset_current")
  for unit in pkuba-release-recovery.service pkuba-application-start.service pkuba-backup@.service; do
    [[ -f $systemd_dir/$unit && ! -L $systemd_dir/$unit ]] \
      || die "missing production systemd unit: $unit"
    exec_path=$(awk -F= '/^ExecStart=/{print $2; exit}' "$systemd_dir/$unit")
    exec_path=${exec_path%% *}
    [[ -n $exec_path && -f $exec_path ]] || die "invalid production systemd command: $unit"
    case "$unit" in
      pkuba-release-recovery.service) expected_rel=sbin/pkuba-recover-release-transaction ;;
      pkuba-application-start.service) expected_rel=sbin/pkuba-start-current-application ;;
      pkuba-backup@.service) expected_rel=sbin/pkuba-backup-current ;;
      *) die "unknown production systemd unit: $unit" ;;
    esac
    [[ $(readlink -f "$exec_path") == "$active_root/$expected_rel" ]] \
      || die "production systemd command does not resolve through the active toolset: $unit"
  done
}

migrate_source() (
  local release_commit=$1 source_root=$2 migration_root migration_stamp backup_dir
  local backup_manifest old_pointer= legacy_dir migration_complete=0
  [[ -f $config_file && ! -L $config_file ]] || die "missing root-owned deployment config"
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G:%a' "$config_file") == root:root:600 ]] \
      || die "deployment config must be root:root mode 600"
  fi
  assert_clear_migration_state
  source_root=$(realpath "$source_root")
  [[ $(git -C "$source_root" rev-parse HEAD) == "$release_commit" ]] \
    || die "migration source is not the requested commit"
  install_toolset_from_source "$source_root" "$release_commit" >/dev/null

  migration_root=$deploy_root/toolset-migrations
  migration_stamp=$(date -u +%Y%m%dT%H%M%SZ)
  ensure_toolset_directory "$migration_root" 700
  backup_dir=$(mktemp -d "$migration_root/$migration_stamp-pre-$release_commit.XXXXXX")
  backup_manifest=$backup_dir/MANIFEST.tsv
  create_migration_backup "$backup_dir" "$backup_manifest"
  if [[ -L $toolset_current ]]; then old_pointer=$(readlink "$toolset_current"); fi
  printf 'OLD_POINTER=%s\nRELEASE_COMMIT=%s\n' "$old_pointer" "$release_commit" \
    >"$backup_dir/POINTER.env"
  chmod 600 "$backup_dir/POINTER.env"
  sync -f "$backup_dir/POINTER.env" "$backup_dir" "$migration_root"

  legacy_dir=$toolset_legacy_root/${backup_dir##*/}
  create_legacy_compatibility_toolset "$backup_dir" "$backup_manifest" "$legacy_dir" \
    "$toolset_release_root/$release_commit"

  rollback_migration() {
    local status=$?
    trap - EXIT
    if [[ $migration_complete != 1 ]]; then
      if restore_migration_backup "$backup_dir" "$backup_manifest" "$old_pointer"; then
        echo "Production toolset migration failed; prior entrypoints and pointer were restored." >&2
      else
        echo "PKUBA_TOOLSET_MIGRATION_RECOVERY_REQUIRED=1" >&2
        echo "CRITICAL: production toolset migration failed and the prior entrypoints could not be fully restored; use the root-only migration backup before any deployment." >&2
        status=2
      fi
      (( status != 0 )) || status=1
    fi
    exit "$status"
  }
  trap rollback_migration EXIT

  switch_current "legacy/${legacy_dir##*/}" "$legacy_dir"
  install_stable_links
  validate_stable_links "$legacy_dir"
  fail_point after-migration-stable-links
  install_transition_sudoers
  fail_point after-migration-transition-sudoers
  validate_systemd_resolution
  activate_toolset "$release_commit"
  validate_stable_links "$toolset_release_root/$release_commit"
  validate_systemd_resolution
  fail_point after-migration-toolset-activation
  install_sudoers
  grep -Fqx "$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools verify *" \
    "$sudoers_file" || die "toolset verify sudoers entry is missing"
  grep -Fqx "$deploy_user ALL=(root) NOPASSWD: $local_sbin_dir/pkuba-sync-release-tools deploy *" \
    "$sudoers_file" || die "toolset deploy sudoers entry is missing"
  fail_point before-migration-commit
  migration_complete=1
  sync -f "$backup_dir" "$migration_root"
  echo "PKUBA_TOOLSET_MIGRATION_RESULT=success"
  echo "PKUBA_TOOLSET_COMMIT=$release_commit"
  echo "PKUBA_TOOLSET_BACKUP_DIR=$backup_dir"
)

usage() {
  echo "usage: pkuba-sync-release-tools verify 64_HEX_NONCE" >&2
  echo "   or: pkuba-sync-release-tools deploy TAG COMMIT API_IMAGE WEB_IMAGE" >&2
  echo "   or: pkuba-sync-release-tools install TAG COMMIT" >&2
  echo "   or: pkuba-sync-release-tools activate-source COMMIT SOURCE_ROOT" >&2
  echo "   or: pkuba-sync-release-tools migrate-source COMMIT SOURCE_ROOT" >&2
  exit 64
}

[[ $# -ge 1 ]] || usage
operation=$1
shift

if [[ $operation == verify ]]; then
  [[ $# -eq 1 && $1 =~ ^[0-9a-f]{64}$ ]] || usage
  exec bash "$toolset_current/sbin/pkuba-record-deploy-ssh-verification" "$1"
fi

require_safe_roots

case "$operation" in
  deploy)
    [[ $# -eq 4 ]] || usage
    release_tag=$1
    release_commit=$2
    api_image=$3
    web_image=$4
    [[ $api_image =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] || die "invalid API image"
    [[ $web_image =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] || die "invalid web image"
    if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
      with_deploy_lock deploy "$release_tag" "$release_commit" "$api_image" "$web_image"
    fi
    release_dir=$(validate_release_identity "$release_tag" "$release_commit")
    install_toolset_from_source "$release_dir" "$release_commit" >/dev/null
    activate_toolset "$release_commit"
    validate_stable_links "$toolset_release_root/$release_commit"
    fail_point before-versioned-deploy-exec
    exec env PKUBA_DEPLOY_LOCK_HELD=1 \
      bash "$toolset_current/sbin/pkuba-deploy-blue-green" \
      "$release_tag" "$release_commit" "$api_image" "$web_image"
    ;;
  install)
    [[ $# -eq 2 ]] || usage
    if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
      with_deploy_lock install "$1" "$2"
    fi
    release_dir=$(validate_release_identity "$1" "$2")
    install_toolset_from_source "$release_dir" "$2" >/dev/null
    install_stable_links
    activate_toolset "$2"
    validate_stable_links "$toolset_release_root/$2"
    ;;
  activate-source)
    [[ $# -eq 2 ]] || usage
    if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
      with_deploy_lock activate-source "$1" "$2"
    fi
    install_toolset_from_source "$2" "$1" >/dev/null
    install_stable_links
    activate_toolset "$1"
    validate_stable_links "$toolset_release_root/$1"
    echo "PKUBA_TOOLSET_ACTIVATION_RESULT=success"
    echo "PKUBA_TOOLSET_COMMIT=$1"
    ;;
  migrate-source)
    [[ $# -eq 2 ]] || usage
    if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
      with_deploy_lock migrate-source "$1" "$2"
    fi
    migrate_source "$1" "$2"
    ;;
  *) usage ;;
esac
