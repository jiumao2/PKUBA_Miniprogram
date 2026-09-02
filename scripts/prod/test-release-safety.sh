#!/usr/bin/env bash
set -euo pipefail

# Production deployment entrypoints run as root and intentionally create
# root-only state.  Elevate the isolated fixture as one unit so assertions do
# not require weakening those permissions on non-root CI runners.
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  exec sudo -n -- bash "$0" "$@"
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fixture=$(mktemp -d)

for upstream_writer in bootstrap-server.sh deploy-blue-green.sh \
  rollback-retained-application.sh restore-paired-data.sh; do
  if grep -Fq '\treverse_proxy' "$script_dir/$upstream_writer"; then
    echo "$upstream_writer writes a literal backslash-t into Caddy configuration" >&2
    exit 1
  fi
done

run_root() {
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

trap 'run_root /usr/bin/rm -rf -- "$fixture"' EXIT

# A release updates the complete operational toolset before it enters any
# deployment state machine. Exercise the real launcher against a tiny Git
# repository so a bootstrap-era script cannot silently survive a later tag.
tool_sync_fixture=$fixture/tool-sync
tool_sync_repository=$tool_sync_fixture/repository
tool_sync_release_root=$tool_sync_fixture/deploy/releases
tool_sync_state=$tool_sync_fixture/deploy/state
tool_sync_toolsets=$tool_sync_fixture/toolsets
tool_sync_sbin=$tool_sync_fixture/sbin
tool_sync_libexec=$tool_sync_fixture/libexec
mkdir -p "$tool_sync_repository/scripts/prod" "$tool_sync_release_root" \
  "$tool_sync_state" "$tool_sync_sbin" "$tool_sync_libexec"
chmod 700 "$tool_sync_state"
chmod 711 "$fixture" "$tool_sync_fixture" "$tool_sync_sbin"
git -C "$tool_sync_repository" init -q
git -C "$tool_sync_repository" config user.name PKUBA-Tool-Test
git -C "$tool_sync_repository" config user.email pkuba-tool-test@example.invalid
git -C "$tool_sync_repository" config core.filemode true
git -C "$tool_sync_repository" branch -M main

tool_sources=(
  deploy-gateway.sh record-deploy-ssh-verification.sh finalize-deploy-ssh.sh
  deploy-blue-green.sh rollback-retained-application.sh
  recover-release-transaction.sh restore-paired-data.sh
  start-current-application.sh backup-current-server.sh sync-release-tools.sh
  acquire-deploy-lock.py fence-deploy-writers.sh verify-paired-backup.py
  parse-release-state.sh parse-release-contract.sh derive-release-capability.sh
  validate-release-identity.sh check-app-capability.sh
)
tool_executable_sources=(
  record-deploy-ssh-verification.sh finalize-deploy-ssh.sh
  rollback-retained-application.sh recover-release-transaction.sh
  start-current-application.sh fence-deploy-writers.sh parse-release-state.sh
  parse-release-contract.sh derive-release-capability.sh
  validate-release-identity.sh check-app-capability.sh
)

write_tool_writer() {
  local destination=$1 output_variable=$2 indent=$3 marker=${4:-}
  {
    cat <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
EOF
    [[ -z $marker ]] || cat <<'EOF'
[[ ${PKUBA_DEPLOY_LOCK_HELD:-0} == 1 ]]
[[ -n ${PKUBA_DEPLOY_LOCK_FD:-} ]]
printf 'candidate\n' >"${PKUBA_TEST_TOOL_DEPLOY_CAPTURE:?}"
EOF
    printf 'output=${%s:?}\n' "$output_variable"
    printf "indent='%s'\n" "$indent"
    cat <<'EOF'
cat >"$output" <<EOF_UPSTREAMS
(active_api) {
${indent}reverse_proxy pkuba-blue-api:8000
}

(active_web) {
${indent}reverse_proxy pkuba-blue-web:8080
}
EOF_UPSTREAMS
EOF
  } >"$destination"
}

write_tool_generation() {
  local indent=$1 source
  for source in "${tool_sources[@]}"; do
    case "$source" in
      *.py) printf '%s\n' '#!/usr/bin/env python3' 'pass' \
        >"$tool_sync_repository/scripts/prod/$source" ;;
      *) printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'exit 0' \
        >"$tool_sync_repository/scripts/prod/$source" ;;
    esac
  done
  cp "$script_dir/sync-release-tools.sh" \
    "$tool_sync_repository/scripts/prod/sync-release-tools.sh"
  cp "$script_dir/deploy-gateway.sh" \
    "$tool_sync_repository/scripts/prod/deploy-gateway.sh"
  cp "$script_dir/acquire-deploy-lock.py" \
    "$tool_sync_repository/scripts/prod/acquire-deploy-lock.py"
  write_tool_writer "$tool_sync_repository/scripts/prod/deploy-blue-green.sh" \
    PKUBA_TEST_TOOL_DEPLOY_UPSTREAMS "$indent" deploy
  write_tool_writer \
    "$tool_sync_repository/scripts/prod/rollback-retained-application.sh" \
    PKUBA_TEST_TOOL_ROLLBACK_UPSTREAMS "$indent"
  write_tool_writer "$tool_sync_repository/scripts/prod/restore-paired-data.sh" \
    PKUBA_TEST_TOOL_RESTORE_UPSTREAMS "$indent"
  git -C "$tool_sync_repository" add -A
  for source in "${tool_executable_sources[@]}"; do
    chmod 755 "$tool_sync_repository/scripts/prod/$source"
    git -C "$tool_sync_repository" update-index --chmod=+x \
      "scripts/prod/$source"
  done
}

write_tool_generation '    '
printf '\n# bootstrap-era generation\n' \
  >>"$tool_sync_repository/scripts/prod/deploy-gateway.sh"
git -C "$tool_sync_repository" add scripts/prod/deploy-gateway.sh
git -C "$tool_sync_repository" commit -qm legacy-tools
legacy_tool_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.5
write_tool_generation '    '
git -C "$tool_sync_repository" commit -qm candidate-tools
candidate_tool_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.6

tool_sync_env=(
  PKUBA_TEST_ALLOW_NON_ROOT=1
  PKUBA_TEST_SKIP_FETCH=1
  PKUBA_DEPLOY_ROOT="$tool_sync_fixture/deploy"
  PKUBA_REPOSITORY_DIR="$tool_sync_repository"
  PKUBA_DEPLOY_STATE_DIR="$tool_sync_state"
  PKUBA_RELEASE_TOOLSET_ROOT="$tool_sync_toolsets"
  PKUBA_RELEASE_TOOLS_SBIN_ROOT="$tool_sync_sbin"
  PKUBA_RELEASE_TOOLS_LIBEXEC_ROOT="$tool_sync_libexec"
  PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1
)
mkdir -p "$tool_sync_fixture/unsafe-toolset-target"
ln -s "$tool_sync_fixture/unsafe-toolset-target" \
  "$tool_sync_fixture/unsafe-toolset-link"
if env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
  PKUBA_RELEASE_TOOLSET_ROOT="$tool_sync_fixture/unsafe-toolset-link" \
  bash "$script_dir/sync-release-tools.sh" \
    install v1.0.5 "$legacy_tool_commit" >/dev/null 2>&1; then
  echo 'symlinked toolset root unexpectedly passed' >&2
  exit 1
fi
[[ ! -e $tool_sync_sbin/pkuba-deploy-gateway ]]
env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
  bash "$script_dir/sync-release-tools.sh" \
    install v1.0.5 "$legacy_tool_commit" >/dev/null
[[ $(readlink "$tool_sync_toolsets/current") == "releases/$legacy_tool_commit" ]]
tool_sbin_names=(
  pkuba-deploy-gateway pkuba-record-deploy-ssh-verification
  pkuba-finalize-deploy-ssh pkuba-deploy-blue-green
  pkuba-rollback-retained-application pkuba-recover-release-transaction
  pkuba-restore-paired-data pkuba-start-current-application
  pkuba-backup-current pkuba-sync-release-tools
)
tool_libexec_names=(
  acquire-deploy-lock.py fence-deploy-writers.sh verify-paired-backup.py
  parse-release-state.sh parse-release-contract.sh derive-release-capability.sh
  validate-release-identity.sh check-app-capability.sh
)
for stable_tool in "${tool_sbin_names[@]}"; do
  [[ -L $tool_sync_sbin/$stable_tool ]]
  [[ $(readlink -f "$tool_sync_sbin/$stable_tool") == \
    "$tool_sync_toolsets/releases/$legacy_tool_commit/sbin/$stable_tool" ]]
done
for stable_tool in "${tool_libexec_names[@]}"; do
  [[ -L $tool_sync_libexec/$stable_tool ]]
  [[ $(readlink -f "$tool_sync_libexec/$stable_tool") == \
    "$tool_sync_toolsets/releases/$legacy_tool_commit/libexec/$stable_tool" ]]
done
[[ $(stat -c '%a' "$tool_sync_toolsets") == 711 ]]
[[ $(stat -c '%a' "$tool_sync_toolsets/releases/$legacy_tool_commit") == 711 ]]
[[ $(stat -c '%a' "$tool_sync_toolsets/releases/$legacy_tool_commit/sbin") == 711 ]]
[[ $(stat -c '%a' "$tool_sync_toolsets/releases/$legacy_tool_commit/libexec") == 700 ]]
[[ $(stat -Lc '%a' "$tool_sync_sbin/pkuba-deploy-gateway") == 755 ]]
set +e
sudo -u nobody env SSH_ORIGINAL_COMMAND=invalid \
  "$tool_sync_sbin/pkuba-deploy-gateway" >/dev/null 2>&1
non_root_gateway_status=$?
set -e
[[ $non_root_gateway_status -eq 64 ]]

tool_deploy_capture=$tool_sync_fixture/deploy-invoked
tool_deploy_upstreams=$tool_sync_fixture/deploy-upstreams.caddy
tool_rollback_upstreams=$tool_sync_fixture/rollback-upstreams.caddy
tool_restore_upstreams=$tool_sync_fixture/restore-upstreams.caddy
env "${tool_sync_env[@]}" \
  PKUBA_TEST_TOOL_DEPLOY_CAPTURE="$tool_deploy_capture" \
  PKUBA_TEST_TOOL_DEPLOY_UPSTREAMS="$tool_deploy_upstreams" \
  "$tool_sync_sbin/pkuba-sync-release-tools" deploy \
    v1.0.6 "$candidate_tool_commit" \
    ghcr.io/jiumao2/pkuba-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    ghcr.io/jiumao2/pkuba-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
[[ $(readlink "$tool_sync_toolsets/current") == "releases/$candidate_tool_commit" ]]
[[ $(<"$tool_deploy_capture") == candidate ]]
env PKUBA_TEST_TOOL_ROLLBACK_UPSTREAMS="$tool_rollback_upstreams" \
  bash "$tool_sync_sbin/pkuba-rollback-retained-application"
env PKUBA_TEST_TOOL_RESTORE_UPSTREAMS="$tool_restore_upstreams" \
  bash "$tool_sync_sbin/pkuba-restore-paired-data"

assert_tool_upstreams() {
  local candidate=$1
  [[ $(grep -Fc 'reverse_proxy pkuba-' "$candidate") -eq 2 ]]
  ! grep -Fq '\treverse_proxy' "$candidate"
}
assert_tool_upstreams "$tool_deploy_upstreams"
assert_tool_upstreams "$tool_rollback_upstreams"
assert_tool_upstreams "$tool_restore_upstreams"

switch_tool_pointer() {
  local commit=$1 temporary=$tool_sync_toolsets/.current-test.$$
  rm -f "$temporary"
  ln -s "releases/$commit" "$temporary"
  mv -Tf "$temporary" "$tool_sync_toolsets/current"
}

assert_tool_sync_rejected() {
  local tag=$1 commit=$2 label=$3
  switch_tool_pointer "$legacy_tool_commit"
  rm -f "$tool_deploy_capture"
  printf 'unchanged\n' >"$tool_sync_fixture/data-sentinel"
  local sentinel_hash
  sentinel_hash=$(sha256sum "$tool_sync_fixture/data-sentinel")
  if env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
    bash "$script_dir/sync-release-tools.sh" install "$tag" "$commit" \
      >"$tool_sync_fixture/$label.out" 2>"$tool_sync_fixture/$label.err"; then
    echo "invalid release toolset unexpectedly passed: $label" >&2
    exit 1
  fi
  [[ $(readlink "$tool_sync_toolsets/current") == "releases/$legacy_tool_commit" ]]
  [[ ! -e $tool_deploy_capture ]]
  [[ $sentinel_hash == "$(sha256sum "$tool_sync_fixture/data-sentinel")" ]]
}

candidate_release=$tool_sync_release_root/v1.0.6
printf '\n# dirty\n' >>"$candidate_release/scripts/prod/deploy-gateway.sh"
assert_tool_sync_rejected v1.0.6 "$candidate_tool_commit" dirty-blob
git -C "$candidate_release" checkout -- scripts/prod/deploy-gateway.sh

switch_tool_pointer "$legacy_tool_commit"
if env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
  PKUBA_TEST_FAIL_TOOLSET_SWITCH=1 \
  bash "$script_dir/sync-release-tools.sh" \
    install v1.0.6 "$candidate_tool_commit" >/dev/null 2>&1; then
  echo 'injected toolset pointer failure unexpectedly passed' >&2
  exit 1
fi
[[ $(readlink "$tool_sync_toolsets/current") == "releases/$legacy_tool_commit" ]]

git -C "$tool_sync_repository" rm -q scripts/prod/check-app-capability.sh
git -C "$tool_sync_repository" commit -qm missing-tool
missing_tool_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.7
assert_tool_sync_rejected v1.0.7 "$missing_tool_commit" missing-tool

git -C "$tool_sync_repository" checkout v1.0.6 -- scripts/prod/check-app-capability.sh
rm -f "$tool_sync_repository/scripts/prod/derive-release-capability.sh"
ln -s check-app-capability.sh \
  "$tool_sync_repository/scripts/prod/derive-release-capability.sh"
git -C "$tool_sync_repository" add -A
git -C "$tool_sync_repository" commit -qm symlink-tool
symlink_tool_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.8
assert_tool_sync_rejected v1.0.8 "$symlink_tool_commit" symlink-tool

git -C "$tool_sync_repository" checkout v1.0.6 -- \
  scripts/prod/derive-release-capability.sh
git -C "$tool_sync_repository" add scripts/prod/derive-release-capability.sh
git -C "$tool_sync_repository" update-index --chmod=-x \
  scripts/prod/derive-release-capability.sh
git -C "$tool_sync_repository" commit -qm wrong-mode-tool
wrong_mode_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.9
assert_tool_sync_rejected v1.0.9 "$wrong_mode_commit" wrong-mode

git -C "$tool_sync_repository" checkout v1.0.6 -- \
  scripts/prod/derive-release-capability.sh
printf '%s\n' '#!/usr/bin/env bash' 'if then' \
  >"$tool_sync_repository/scripts/prod/derive-release-capability.sh"
git -C "$tool_sync_repository" add scripts/prod/derive-release-capability.sh
git -C "$tool_sync_repository" update-index --chmod=+x \
  scripts/prod/derive-release-capability.sh
git -C "$tool_sync_repository" commit -qm invalid-syntax-tool
invalid_syntax_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.10
assert_tool_sync_rejected v1.0.10 "$invalid_syntax_commit" invalid-syntax

assert_tool_sync_rejected main "$candidate_tool_commit" invalid-tag
assert_tool_sync_rejected v1.0.6 \
  0000000000000000000000000000000000000000 invalid-commit

printf 'unexpected\n' \
  >"$tool_sync_toolsets/releases/$candidate_tool_commit/UNMANIFESTED"
assert_tool_sync_rejected v1.0.6 "$candidate_tool_commit" extra-installed-file
rm -f "$tool_sync_toolsets/releases/$candidate_tool_commit/UNMANIFESTED"

git -C "$tool_sync_repository" restore --source=v1.0.6 --staged --worktree \
  scripts/prod
printf '\n# next valid toolset\n' \
  >>"$tool_sync_repository/scripts/prod/deploy-gateway.sh"
git -C "$tool_sync_repository" add scripts/prod/deploy-gateway.sh
git -C "$tool_sync_repository" commit -qm next-valid-toolset
next_tool_commit=$(git -C "$tool_sync_repository" rev-parse HEAD)
git -C "$tool_sync_repository" tag v1.0.11
next_tool_release=$tool_sync_release_root/v1.0.11
git -C "$tool_sync_repository" worktree add --detach \
  "$next_tool_release" "$next_tool_commit" >/dev/null
git -C "$next_tool_release" update-index --chmod=+x \
  scripts/prod/deploy-gateway.sh
switch_tool_pointer "$legacy_tool_commit"
if env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
  PKUBA_TEST_TOOLSET_FAIL_POINT=after-toolset-stage-sync \
  bash "$script_dir/sync-release-tools.sh" install \
    v1.0.11 "$next_tool_commit" >"$tool_sync_fixture/index-mode.out" \
    2>"$tool_sync_fixture/index-mode.err"; then
  echo 'worktree index-only mode change unexpectedly blocked the injected failure' >&2
  exit 1
fi
grep -Fq 'injected toolset failure: after-toolset-stage-sync' \
  "$tool_sync_fixture/index-mode.err"
[[ $(readlink "$tool_sync_toolsets/current") == \
  "releases/$legacy_tool_commit" ]]
[[ ! -e $tool_sync_toolsets/releases/$next_tool_commit ]]
[[ -z $(find "$tool_sync_toolsets/releases" -maxdepth 1 \
  -name ".toolset-$next_tool_commit.*" -print -quit) ]]
git -C "$next_tool_release" update-index --chmod=-x \
  scripts/prod/deploy-gateway.sh

for install_fail_point in after-toolset-release-rename; do
  switch_tool_pointer "$legacy_tool_commit"
  rm -f "$tool_deploy_capture"
  if env "${tool_sync_env[@]}" PKUBA_DEPLOY_LOCK_HELD=1 \
    PKUBA_TEST_TOOLSET_FAIL_POINT="$install_fail_point" \
    bash "$script_dir/sync-release-tools.sh" install \
      v1.0.11 "$next_tool_commit" >/dev/null 2>&1; then
    echo "injected toolset install failure unexpectedly passed: $install_fail_point" >&2
    exit 1
  fi
  [[ $(readlink "$tool_sync_toolsets/current") == \
    "releases/$legacy_tool_commit" ]]
  [[ ! -e $tool_deploy_capture ]]
  [[ -d $tool_sync_toolsets/releases/$next_tool_commit ]]
done

# Existing production has the original 17 flat tools and no sync entrypoint.
# The one-time root migration must bridge that exact layout without touching
# application state, and every injected failure must restore the flat files,
# sudoers and prior (absent) current pointer byte-for-byte.
migration_root=$tool_sync_fixture/migration
migration_deploy=$migration_root/deploy
migration_state=$migration_deploy/state
migration_toolsets=$migration_root/toolsets
migration_sbin=$migration_root/sbin
migration_libexec=$migration_root/libexec
migration_systemd=$migration_root/systemd
migration_config=$migration_root/pkuba-deploy.conf
migration_sudoers=$migration_root/pkuba-deploy.sudoers
migration_fake_bin=$migration_root/fake-bin
mkdir -p "$migration_state" "$migration_sbin" "$migration_libexec" \
  "$migration_systemd" "$migration_fake_bin"
chmod 700 "$migration_state"
printf 'ACTIVE_SLOT=blue\nCURRENT_TAG=v1.0.2\n' >"$migration_state/current.env"
printf 'PKUBA_PRODUCTION_AUTOMATION_ARMED=1\n' >"$migration_config"
printf '%s\n' \
  'pkuba-deploy ALL=(root) NOPASSWD: /legacy/pkuba-deploy-blue-green *' \
  >"$migration_sudoers"
chmod 600 "$migration_config"
chmod 440 "$migration_sudoers"
for stable_tool in "${tool_sbin_names[@]}"; do
  [[ $stable_tool == pkuba-sync-release-tools ]] && continue
  cp -a "$tool_sync_toolsets/releases/$legacy_tool_commit/sbin/$stable_tool" \
    "$migration_sbin/$stable_tool"
done
for stable_tool in "${tool_libexec_names[@]}"; do
  cp -a "$tool_sync_toolsets/releases/$legacy_tool_commit/libexec/$stable_tool" \
    "$migration_libexec/$stable_tool"
done
write_tool_writer "$migration_sbin/pkuba-deploy-blue-green" \
  PKUBA_TEST_TOOL_DEPLOY_UPSTREAMS '\t' deploy
write_tool_writer "$migration_sbin/pkuba-rollback-retained-application" \
  PKUBA_TEST_TOOL_ROLLBACK_UPSTREAMS '\t'
write_tool_writer "$migration_sbin/pkuba-restore-paired-data" \
  PKUBA_TEST_TOOL_RESTORE_UPSTREAMS '\t'
for legacy_writer in pkuba-deploy-blue-green \
  pkuba-rollback-retained-application pkuba-restore-paired-data; do
  grep -Fq "indent='\\t'" "$migration_sbin/$legacy_writer"
  grep -Fq '${indent}reverse_proxy' "$migration_sbin/$legacy_writer"
done
cat >"$migration_systemd/pkuba-release-recovery.service" <<EOF
[Service]
ExecStart=$migration_sbin/pkuba-recover-release-transaction
EOF
cat >"$migration_systemd/pkuba-application-start.service" <<EOF
[Service]
ExecStart=$migration_sbin/pkuba-start-current-application
EOF
cat >"$migration_systemd/pkuba-backup@.service" <<EOF
[Service]
ExecStart=$migration_sbin/pkuba-backup-current %i
EOF
cat >"$migration_fake_bin/visudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ ${1:-} == --check && ${2:-} == --file=* ]]
EOF
chmod 700 "$migration_fake_bin/visudo"

migration_env=(
  PATH="$migration_fake_bin:$PATH"
  PKUBA_TEST_ALLOW_NON_ROOT=1
  PKUBA_TEST_SKIP_FETCH=1
  PKUBA_DEPLOY_ROOT="$migration_deploy"
  PKUBA_REPOSITORY_DIR="$tool_sync_repository"
  PKUBA_DEPLOY_STATE_DIR="$migration_state"
  PKUBA_RELEASE_TOOLSET_ROOT="$migration_toolsets"
  PKUBA_RELEASE_TOOLS_SBIN_ROOT="$migration_sbin"
  PKUBA_RELEASE_TOOLS_LIBEXEC_ROOT="$migration_libexec"
  PKUBA_DEPLOY_CONFIG="$migration_config"
  PKUBA_DEPLOY_SUDOERS_FILE="$migration_sudoers"
  PKUBA_SYSTEMD_DIR="$migration_systemd"
  PKUBA_DEPLOY_LOCK_HELPER="$migration_libexec/acquire-deploy-lock.py"
  PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1
)
migration_files_before=$(find "$migration_sbin" "$migration_libexec" -type f \
  -exec sha256sum {} + | sort)
migration_modes_before=$(find "$migration_sbin" "$migration_libexec" -type f \
  -printf '%p %m\n' | sort)
migration_state_before=$(sha256sum "$migration_state/current.env")
migration_sudoers_before=$(sha256sum "$migration_sudoers")
migration_business_sentinel=$migration_root/business-data-sentinel
migration_deploy_spy=$migration_root/deploy-spy
printf 'business-data-unchanged\n' >"$migration_business_sentinel"
migration_business_before=$(sha256sum "$migration_business_sentinel")
for migration_fail_point in after-migration-stable-links \
  after-migration-transition-sudoers \
  after-migration-toolset-activation before-migration-commit; do
  migration_fail_stdout=$migration_root/$migration_fail_point.out
  migration_fail_stderr=$migration_root/$migration_fail_point.err
  if env "${migration_env[@]}" \
    PKUBA_TEST_TOOL_DEPLOY_CAPTURE="$migration_deploy_spy" \
    PKUBA_TEST_TOOLSET_FAIL_POINT="$migration_fail_point" \
    bash "$script_dir/sync-release-tools.sh" migrate-source \
      "$candidate_tool_commit" "$candidate_release" \
      >"$migration_fail_stdout" 2>"$migration_fail_stderr"; then
    echo "injected production toolset migration failure unexpectedly passed: $migration_fail_point" >&2
    exit 1
  fi
  grep -Fq "injected toolset failure: $migration_fail_point" \
    "$migration_fail_stderr"
  [[ ! -e $migration_toolsets/current && ! -L $migration_toolsets/current ]]
  [[ ! -e $migration_sbin/pkuba-sync-release-tools ]]
  [[ $migration_files_before == "$(find "$migration_sbin" "$migration_libexec" \
    -type f -exec sha256sum {} + | sort)" ]]
  [[ $migration_modes_before == "$(find "$migration_sbin" "$migration_libexec" \
    -type f -printf '%p %m\n' | sort)" ]]
  [[ $migration_sudoers_before == "$(sha256sum "$migration_sudoers")" ]]
  [[ $migration_state_before == "$(sha256sum "$migration_state/current.env")" ]]
  [[ $migration_business_before == "$(sha256sum "$migration_business_sentinel")" ]]
  [[ ! -e $migration_deploy_spy ]]
  [[ ! -e $migration_state/maintenance.enabled \
    && ! -e $migration_state/release-transaction \
    && ! -e $migration_state/paired-restore-transaction ]]
done

set +e
env "${migration_env[@]}" \
  PKUBA_TEST_TOOL_DEPLOY_CAPTURE="$migration_deploy_spy" \
  PKUBA_TEST_TOOLSET_FAIL_POINT=before-migration-commit \
  PKUBA_TEST_FAIL_MIGRATION_RESTORE_VERIFY=1 \
  bash "$script_dir/sync-release-tools.sh" migrate-source \
    "$candidate_tool_commit" "$candidate_release" \
    >"$migration_root/recovery-required.out" \
    2>"$migration_root/recovery-required.err"
migration_recovery_status=$?
set -e
[[ $migration_recovery_status -eq 2 ]]
grep -Fqx 'PKUBA_TOOLSET_MIGRATION_RECOVERY_REQUIRED=1' \
  "$migration_root/recovery-required.err"
[[ ! -e $migration_toolsets/current && ! -L $migration_toolsets/current ]]
[[ ! -e $migration_sbin/pkuba-sync-release-tools ]]
[[ $migration_files_before == "$(find "$migration_sbin" "$migration_libexec" \
  -type f -exec sha256sum {} + | sort)" ]]
[[ $migration_modes_before == "$(find "$migration_sbin" "$migration_libexec" \
  -type f -printf '%p %m\n' | sort)" ]]
[[ $migration_sudoers_before == "$(sha256sum "$migration_sudoers")" ]]
[[ $migration_state_before == "$(sha256sum "$migration_state/current.env")" ]]
[[ $migration_business_before == "$(sha256sum "$migration_business_sentinel")" ]]
[[ ! -e $migration_deploy_spy ]]

migration_output=$(env "${migration_env[@]}" \
  PKUBA_TEST_TOOL_DEPLOY_CAPTURE="$migration_deploy_spy" \
  bash "$script_dir/sync-release-tools.sh" migrate-source \
    "$candidate_tool_commit" "$candidate_release")
grep -Fqx 'PKUBA_TOOLSET_MIGRATION_RESULT=success' <<<"$migration_output"
grep -Fqx "PKUBA_TOOLSET_COMMIT=$candidate_tool_commit" <<<"$migration_output"
[[ $(readlink "$migration_toolsets/current") == \
  "releases/$candidate_tool_commit" ]]
for stable_tool in "${tool_sbin_names[@]}"; do
  [[ -L $migration_sbin/$stable_tool ]]
  [[ $(readlink -f "$migration_sbin/$stable_tool") == \
    "$migration_toolsets/releases/$candidate_tool_commit/sbin/$stable_tool" ]]
done
for stable_tool in "${tool_libexec_names[@]}"; do
  [[ -L $migration_libexec/$stable_tool ]]
  [[ $(readlink -f "$migration_libexec/$stable_tool") == \
    "$migration_toolsets/releases/$candidate_tool_commit/libexec/$stable_tool" ]]
done
for migrated_writer in pkuba-deploy-blue-green \
  pkuba-rollback-retained-application pkuba-restore-paired-data; do
  ! grep -Fq '\treverse_proxy' "$migration_sbin/$migrated_writer"
done
[[ $(wc -l <"$migration_sudoers") -eq 2 ]]
grep -Fq 'pkuba-sync-release-tools verify *' "$migration_sudoers"
grep -Fq 'pkuba-sync-release-tools deploy *' "$migration_sudoers"
! grep -Fq 'pkuba-deploy-blue-green *' "$migration_sudoers"
! grep -Fq 'pkuba-record-deploy-ssh-verification *' "$migration_sudoers"
for unit in pkuba-release-recovery.service pkuba-application-start.service \
  pkuba-backup@.service; do
  unit_exec=$(awk -F= '/^ExecStart=/{print $2; exit}' "$migration_systemd/$unit")
  unit_exec=${unit_exec%% *}
  case "$unit" in
    pkuba-release-recovery.service) unit_expected=sbin/pkuba-recover-release-transaction ;;
    pkuba-application-start.service) unit_expected=sbin/pkuba-start-current-application ;;
    pkuba-backup@.service) unit_expected=sbin/pkuba-backup-current ;;
  esac
  [[ $(readlink -f "$unit_exec") == \
    "$migration_toolsets/releases/$candidate_tool_commit/$unit_expected" ]]
done
[[ $migration_state_before == "$(sha256sum "$migration_state/current.env")" ]]
[[ $migration_business_before == "$(sha256sum "$migration_business_sentinel")" ]]
[[ ! -e $migration_deploy_spy ]]
[[ ! -e $migration_state/maintenance.enabled \
  && ! -e $migration_state/release-transaction \
  && ! -e $migration_state/paired-restore-transaction ]]

echo 'Versioned release toolset synchronization checks passed.'

repository=$fixture/repository
release_root=$fixture/deploy/releases
state_root=$fixture/deploy/state
mkdir -p "$repository" "$release_root" "$state_root/slots" "$fixture/fake-bin"
chmod 700 "$state_root"
git -C "$repository" init -q
git -C "$repository" config user.name PKUBA-Test
git -C "$repository" config user.email pkuba-test@example.invalid
git -C "$repository" branch -M main
mkdir -p "$repository/infra"
printf 'services: {}\n' >"$repository/infra/compose.prod.slot.yml"
printf 'services: {}\n' >"$repository/infra/compose.prod.data.yml"
printf 'services: {}\n' >"$repository/infra/compose.prod.gateway.yml"
printf ':443 {}\n' >"$repository/infra/Caddyfile.gateway"
printf ':8080 {}\n' >"$repository/infra/Caddyfile.slot"
mkdir -p "$repository/scripts/prod"
for helper in check-app-capability.sh parse-release-contract.sh \
  derive-release-capability.sh parse-release-state.sh validate-release-identity.sh \
  recover-release-transaction.sh acquire-deploy-lock.py fence-deploy-writers.sh \
  verify-paired-backup.py; do
  cp "$script_dir/$helper" "$repository/scripts/prod/$helper"
done
cat >"$repository/infra/release-contract.env" <<'EOF'
PKUBA_PREVIOUS_APP_COMPATIBLE=1
PKUBA_APP_CAPABILITY=reschedule-route-v1
PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1
EOF
git -C "$repository" add infra scripts
git -C "$repository" commit -qm first
commit_one=$(git -C "$repository" rev-parse HEAD)
git -C "$repository" tag v1.2.3
git -C "$repository" worktree add -q --detach "$release_root/v1.2.3" "$commit_one"
printf 'second\n' >"$repository/release-generation"
cat >"$repository/infra/release-contract.env" <<'EOF'
PKUBA_PREVIOUS_APP_COMPATIBLE=1
PKUBA_APP_CAPABILITY=reschedule-route-v2
PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY=reschedule-route-v1
EOF
git -C "$repository" add release-generation
git -C "$repository" add infra/release-contract.env
git -C "$repository" commit -qm second
commit_two=$(git -C "$repository" rev-parse HEAD)
git -C "$repository" tag v1.2.4
git -C "$repository" worktree add -q --detach "$release_root/v1.2.4" "$commit_two"
for release_number in 5 6 7 8; do
  printf '%s\n' "$release_number" >"$repository/release-generation"
  git -C "$repository" add release-generation
  git -C "$repository" commit -qm "release $release_number"
  printf -v commit_name 'commit_%s' "$release_number"
  printf -v "$commit_name" '%s' "$(git -C "$repository" rev-parse HEAD)"
  tag=v1.2.$release_number
  git -C "$repository" tag "$tag"
done
origin=$fixture/origin.git
git init -q --bare "$origin"
git -C "$repository" remote add origin "$origin"
git -C "$repository" push -q origin main --tags

api_one=ghcr.io/jiumao2/pkuba-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
web_one=ghcr.io/jiumao2/pkuba-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
api_two=ghcr.io/jiumao2/pkuba-api@sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
web_two=ghcr.io/jiumao2/pkuba-web@sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd
api_five=ghcr.io/jiumao2/pkuba-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee
web_five=ghcr.io/jiumao2/pkuba-web@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff
api_six=ghcr.io/jiumao2/pkuba-api@sha256:1111111111111111111111111111111111111111111111111111111111111111
web_six=ghcr.io/jiumao2/pkuba-web@sha256:2222222222222222222222222222222222222222222222222222222222222222
api_seven=ghcr.io/jiumao2/pkuba-api@sha256:3333333333333333333333333333333333333333333333333333333333333333
web_seven=ghcr.io/jiumao2/pkuba-web@sha256:4444444444444444444444444444444444444444444444444444444444444444
api_eight=ghcr.io/jiumao2/pkuba-api@sha256:5555555555555555555555555555555555555555555555555555555555555555
web_eight=ghcr.io/jiumao2/pkuba-web@sha256:6666666666666666666666666666666666666666666666666666666666666666
docker_log=$fixture/docker.log
cat >"$fixture/fake-bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$PKUBA_FAKE_DOCKER_LOG"
if [[ ${1:-} == image && ${2:-} == inspect ]]; then
  if [[ ${3:-} == --format ]]; then
    image=${!#}
    case "$image" in
      *sha256:aaaaaaaa*|*sha256:bbbbbbbb*) printf '%s\n' "$PKUBA_TEST_COMMIT_ONE" ;;
      *sha256:cccccccc*|*sha256:dddddddd*) printf '%s\n' "$PKUBA_TEST_COMMIT_TWO" ;;
      *sha256:eeeeeeee*|*sha256:ffffffff*) printf '%s\n' "$PKUBA_TEST_COMMIT_FIVE" ;;
      *sha256:11111111*|*sha256:22222222*) printf '%s\n' "$PKUBA_TEST_COMMIT_SIX" ;;
      *sha256:33333333*|*sha256:44444444*) printf '%s\n' "$PKUBA_TEST_COMMIT_SEVEN" ;;
      *sha256:55555555*|*sha256:66666666*) printf '%s\n' "$PKUBA_TEST_COMMIT_EIGHT" ;;
      *) exit 1 ;;
    esac
  fi
  exit 0
fi
if [[ ${1:-} == inspect && ${2:-} == --format ]]; then
  case "${3:-}" in
    *State.Running*)
      container=${!#}
      if [[ $container == writer-* ]]; then
        if [[ -n ${PKUBA_FAIL_DOCKER_INSPECT_ONCE_MARKER:-} \
          && ! -e $PKUBA_FAIL_DOCKER_INSPECT_ONCE_MARKER ]]; then
          : >"$PKUBA_FAIL_DOCKER_INSPECT_ONCE_MARKER"
          exit 42
        fi
        if [[ -f ${PKUBA_FAKE_WRITERS_FILE:-/nonexistent} ]]; then
          printf 'true\n'
        else
          printf 'false\n'
        fi
      else
        printf 'true\n'
      fi
      ;;
    *RestartCount*) printf '0\n' ;;
    *) exit 1 ;;
  esac
  exit 0
fi
if [[ ${1:-} == ps ]]; then
  if [[ -n ${PKUBA_DOCKER_PS_COUNT_FILE:-} ]]; then
    ps_count=0
    [[ -f $PKUBA_DOCKER_PS_COUNT_FILE ]] && ps_count=$(<"$PKUBA_DOCKER_PS_COUNT_FILE")
    ps_count=$((ps_count + 1))
    printf '%s\n' "$ps_count" >"$PKUBA_DOCKER_PS_COUNT_FILE"
    if [[ -n ${PKUBA_FAIL_DOCKER_PS_AT:-} && $ps_count -eq $PKUBA_FAIL_DOCKER_PS_AT ]]; then
      exit 42
    fi
  fi
  if [[ ${2:-} == -aq ]]; then
    printf 'container-db\n'
  elif [[ ${2:-} == -q && -f ${PKUBA_FAKE_WRITERS_FILE:-/nonexistent} ]]; then
    printf 'writer-%s\n' "${PKUBA_FAKE_WRITER_COUNTER:-0}"
  fi
  exit 0
fi
if [[ ${1:-} == stop ]]; then
  if [[ -n ${PKUBA_FAIL_DOCKER_STOP_ONCE_MARKER:-} \
    && ! -e $PKUBA_FAIL_DOCKER_STOP_ONCE_MARKER ]]; then
    : >"$PKUBA_FAIL_DOCKER_STOP_ONCE_MARKER"
    exit 42
  fi
  [[ -n ${PKUBA_FAKE_WRITERS_FILE:-} ]] && rm -f "$PKUBA_FAKE_WRITERS_FILE"
  exit 0
fi
if [[ ${1:-} == volume && ${2:-} == inspect ]]; then
  exit 0
fi
if [[ ${1:-} == exec ]]; then
  joined=" $* "
  if [[ $joined == *pg_dump* ]]; then
    printf 'FAKE-PG-DUMP\n'
  elif [[ $joined == *'SELECT 1'* ]]; then
    printf '1\n'
  fi
  exit 0
fi
if [[ ${1:-} == compose ]]; then
  joined=" $* "
  if [[ $joined == *' caddy reload '* ]]; then
    count=0
    [[ -f ${PKUBA_RELOAD_COUNT_FILE:-} ]] && count=$(<"$PKUBA_RELOAD_COUNT_FILE")
    count=$((count + 1))
    printf '%s\n' "$count" >"$PKUBA_RELOAD_COUNT_FILE"
    if [[ -n ${PKUBA_FAIL_RELOAD_AT:-} && $count -eq $PKUBA_FAIL_RELOAD_AT ]]; then
      exit 79
    fi
  fi
  if [[ $joined == *pg_database_size* ]]; then
    printf '1024\n'
    exit 0
  fi
  if [[ $joined == *pg_dump* ]]; then
    printf 'FAKE-PG-DUMP\n'
    exit 0
  fi
  if [[ $joined == *audit_season_integrity* ]]; then
    printf '{"violations":0}\n'
    exit 0
  fi
  if [[ $joined == *showmigrations* ]]; then
    printf '[X] core.0039_reschedule_process_route\n'
    exit 0
  fi
  for argument in "$@"; do
    if [[ $argument == ps ]]; then
      printf 'container-%s\n' "${!#}"
      break
    fi
  done
  exit 0
fi
if [[ ${1:-} == run ]]; then
  joined=" $* "
  if [[ $joined == *audit_season_integrity* ]]; then
    printf '{"violations":0}\n'
    exit 0
  fi
  if [[ $joined == *'du -sk /source'* ]]; then
    printf '1024\n'
    exit 0
  fi
  backup_mount=
  incident_mount=
  for argument in "$@"; do
    if [[ $argument == *:/backup ]]; then
      backup_mount=${argument%:/backup}
    fi
    if [[ $argument == *:/incident ]]; then
      incident_mount=${argument%:/incident}
    fi
  done
  if [[ -n $backup_mount ]]; then
    [[ $joined == *private-media.tar.gz* ]] \
      && /usr/bin/tar -czf "$backup_mount/private-media.tar.gz" --files-from /dev/null
    [[ $joined == *archive-staging.tar.gz* ]] \
      && /usr/bin/tar -czf "$backup_mount/archive-staging.tar.gz" --files-from /dev/null
    [[ $joined == *private-media.files.sha256* ]] \
      && : >"$backup_mount/private-media.files.sha256"
    [[ $joined == *archive-staging.files.sha256* ]] \
      && : >"$backup_mount/archive-staging.files.sha256"
  fi
  if [[ -n $incident_mount ]]; then
    [[ $joined == *private-media.tar.gz* ]] \
      && /usr/bin/tar -czf "$incident_mount/private-media.tar.gz" --files-from /dev/null
    [[ $joined == *archive-staging.tar.gz* ]] \
      && /usr/bin/tar -czf "$incident_mount/archive-staging.tar.gz" --files-from /dev/null
  fi
  exit 0
fi
exit 0
EOF
chmod +x "$fixture/fake-bin/docker"
cat >"$fixture/fake-bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url=${!#}
if [[ $url == *:18000/* || $url == *:18080/* ]]; then
  tag=v1.2.3
  commit=$PKUBA_TEST_COMMIT_ONE
elif [[ $url == *:18001/* || $url == *:18081/* ]]; then
  tag=v1.2.4
  commit=$PKUBA_TEST_COMMIT_TWO
elif grep -q 'pkuba-blue-' "$PKUBA_TEST_STATE_ROOT/upstreams.caddy"; then
  tag=v1.2.3
  commit=$PKUBA_TEST_COMMIT_ONE
else
  tag=v1.2.4
  commit=$PKUBA_TEST_COMMIT_TWO
fi
if [[ $url == https://* && -n ${PKUBA_FAIL_PUBLIC_TAG:-} \
  && $tag == "$PKUBA_FAIL_PUBLIC_TAG" ]]; then
  exit 22
fi
if [[ $url == https://*/api/v1/health/ready \
  && -n ${PKUBA_FAIL_PUBLIC_READY_TAG:-} \
  && $tag == "$PKUBA_FAIL_PUBLIC_READY_TAG" ]]; then
  exit 22
fi
printf '{"status":"ok","release_tag":"%s","git_commit":"%s"}\n' "$tag" "$commit"
EOF
chmod +x "$fixture/fake-bin/curl"
cat >"$fixture/fake-bin/mv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
destination=${!#}
if [[ -n ${PKUBA_FAIL_ALWAYS_MV_DEST_PATTERN:-} \
  && $destination == *"$PKUBA_FAIL_ALWAYS_MV_DEST_PATTERN"* \
  && -e ${PKUBA_FAIL_MV_AFTER_MARKER:-/nonexistent} ]]; then
  exit 75
fi
if [[ -n ${PKUBA_FAIL_MV_DEST_PATTERN:-} \
  && $destination == *"$PKUBA_FAIL_MV_DEST_PATTERN"* \
  && ! -e ${PKUBA_FAIL_ONCE_MARKER:-/nonexistent} ]]; then
  : >"$PKUBA_FAIL_ONCE_MARKER"
  exit 73
fi
exec /usr/bin/mv "$@"
EOF
chmod +x "$fixture/fake-bin/mv"
cat >"$fixture/fake-bin/rm" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do
  if [[ -n ${PKUBA_FAIL_RM_ARG_PATTERN:-} \
    && $argument == *"$PKUBA_FAIL_RM_ARG_PATTERN"* \
    && ! -e ${PKUBA_FAIL_ONCE_MARKER:-/nonexistent} ]]; then
    : >"$PKUBA_FAIL_ONCE_MARKER"
    exit 74
  fi
done
exec /usr/bin/rm "$@"
EOF
chmod +x "$fixture/fake-bin/rm"
cat >"$fixture/fake-bin/df" <<'EOF'
#!/usr/bin/env bash
printf 'Filesystem 1-blocks Used Available Capacity Mounted on\n'
printf 'fixture 100000000000 1000 90000000000 1%% /tmp\n'
EOF
chmod +x "$fixture/fake-bin/df"
cat >"$fixture/fake-bin/tar" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fixture/fake-bin/tar"
cat >"$fixture/fake-bin/find" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${PKUBA_FAIL_BACKUP_ENUM_FIND:-0} == 1 && " $* " == *' -name SUCCESS '* ]]; then
  exit 42
fi
exec /usr/bin/find "$@"
EOF
chmod +x "$fixture/fake-bin/find"
cat >"$fixture/fake-bin/sort" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${PKUBA_FAIL_BACKUP_ENUM_SORT:-0} == 1 && " $* " == *' -nr '* ]]; then
  exit 42
fi
exec /usr/bin/sort "$@"
EOF
chmod +x "$fixture/fake-bin/sort"
cat >"$fixture/fake-bin/awk" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${PKUBA_FAIL_BACKUP_ENUM_AWK:-0} == 1 \
  && " $* " == *'sub(/^ /, ""); print'* ]]; then
  exit 42
fi
exec /usr/bin/awk "$@"
EOF
chmod +x "$fixture/fake-bin/awk"

export PKUBA_FAKE_DOCKER_LOG=$docker_log
export PKUBA_TEST_COMMIT_ONE=$commit_one
export PKUBA_TEST_COMMIT_TWO=$commit_two
export PKUBA_TEST_COMMIT_FIVE=$commit_5
export PKUBA_TEST_COMMIT_SIX=$commit_6
export PKUBA_TEST_COMMIT_SEVEN=$commit_7
export PKUBA_TEST_COMMIT_EIGHT=$commit_8
export PKUBA_TEST_STATE_ROOT=$state_root
export PKUBA_RELOAD_COUNT_FILE=$fixture/reload-count
test_path=$fixture/fake-bin:$PATH

write_state() {
  local destination=$1 slot=$2 tag=$3 commit=$4 api=$5 web=$6 directory=$7 capability=$8
  local rollback_allowed_from=${9:-}
  cat >"$destination" <<EOF
ACTIVE_SLOT=$slot
CURRENT_TAG=$tag
CURRENT_COMMIT=$commit
CURRENT_API_IMAGE=$api
CURRENT_WEB_IMAGE=$web
CURRENT_RELEASE_DIR=$directory
CURRENT_APP_CAPABILITY=$capability
EOF
  if [[ -n $rollback_allowed_from ]]; then
    printf 'ROLLBACK_ALLOWED_FROM_CAPABILITY=%s\n' "$rollback_allowed_from" >>"$destination"
  fi
  cat >>"$destination" <<EOF
SWITCHED_AT=2026-08-25T00:00:00Z
EOF
}

valid_state=$fixture/valid.env
write_state "$valid_state" green v1.2.4 "$commit_two" "$api_two" "$web_two" \
  "$release_root/v1.2.4" reschedule-route-v2
PATH=$test_path bash "$script_dir/validate-release-identity.sh" \
  "$valid_state" "$release_root" "$repository" >/dev/null
[[ $(bash "$script_dir/derive-release-capability.sh" "$release_root/v1.2.3") == reschedule-route-v1 ]]
[[ $(bash "$script_dir/derive-release-capability.sh" "$release_root/v1.2.4") == reschedule-route-v2 ]]
mkdir -p "$fixture/legacy/infra"
printf 'PKUBA_PREVIOUS_APP_COMPATIBLE=0\n' >"$fixture/legacy/infra/release-contract.env"
[[ $(bash "$script_dir/derive-release-capability.sh" "$fixture/legacy") == legacy-request-type-v0 ]]
! grep -Fq -- '--current-app-capability' "$script_dir/bootstrap-server.sh"

config=$fixture/deploy.conf
env_file=$fixture/server.env
printf 'PKUBA_DOMAIN=example.test\n' >"$env_file"
cat >"$config" <<EOF
PKUBA_DEPLOY_ROOT=$fixture/deploy
PKUBA_REPOSITORY_DIR=$repository
PKUBA_ENV_FILE=$env_file
PKUBA_DEPLOY_LOCK_FILE=$fixture/deploy.lock
PKUBA_PRODUCTION_AUTOMATION_ARMED=1
PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1
PKUBA_SERVICE_STABILITY_SECONDS=0
PKUBA_GATEWAY_PROBE_ATTEMPTS=1
PKUBA_GATEWAY_PROBE_DELAY_SECONDS=0
EOF

# The shared deployment lock lives inside the pre-created 0700 state directory
# and must never follow a state-directory symlink, lock symlink or hard link.
lock_fixture=$fixture/lock-safety
lock_real=$lock_fixture/state
lock_marker=$lock_fixture/command-ran
lock_sentinel=$lock_fixture/external-sentinel
mkdir -p "$lock_real"
chmod 700 "$lock_real"
printf 'sentinel\n' >"$lock_sentinel"
sentinel_hash=$(sha256sum "$lock_sentinel")
ln -s "$lock_real" "$lock_fixture/state-link"
if run_root env PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1 \
  python3 "$script_dir/acquire-deploy-lock.py" \
    --state-dir "$lock_fixture/state-link" --timeout 0 -- \
    /usr/bin/touch "$lock_marker" >/dev/null 2>&1; then
  echo "symlinked deployment state directory unexpectedly passed" >&2
  exit 1
fi
[[ ! -e $lock_marker ]]
ln -s "$lock_sentinel" "$lock_real/deploy.lock"
if run_root env PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1 \
  python3 "$script_dir/acquire-deploy-lock.py" \
    --state-dir "$lock_real" --timeout 0 -- \
    /usr/bin/touch "$lock_marker" >/dev/null 2>&1; then
  echo "symlinked deployment lock unexpectedly passed" >&2
  exit 1
fi
[[ ! -e $lock_marker && $sentinel_hash == "$(sha256sum "$lock_sentinel")" ]]
/usr/bin/rm -f "$lock_real/deploy.lock"
run_root ln "$lock_sentinel" "$lock_real/deploy.lock"
if run_root env PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=1 \
  python3 "$script_dir/acquire-deploy-lock.py" \
    --state-dir "$lock_real" --timeout 0 -- \
    /usr/bin/touch "$lock_marker" >/dev/null 2>&1; then
  echo "hard-linked deployment lock unexpectedly passed" >&2
  exit 1
fi
[[ ! -e $lock_marker && $sentinel_hash == "$(sha256sum "$lock_sentinel")" ]]
/usr/bin/rm -f "$lock_real/deploy.lock" "$lock_fixture/state-link"

assert_invalid_deploy_state_fails_before_docker() {
  local candidate=$1
  cp "$candidate" "$state_root/current.env"
  : >"$docker_log"
  if run_root env \
    PATH="$test_path" \
    PKUBA_FAKE_DOCKER_LOG="$docker_log" \
    PKUBA_TEST_COMMIT_ONE="$commit_one" \
    PKUBA_TEST_COMMIT_TWO="$commit_two" \
    PKUBA_TEST_COMMIT_FIVE="$commit_5" \
    PKUBA_TEST_COMMIT_SIX="$commit_6" \
    PKUBA_TEST_COMMIT_SEVEN="$commit_7" \
    PKUBA_TEST_COMMIT_EIGHT="$commit_8" \
    PKUBA_TEST_STATE_ROOT="$state_root" \
    PKUBA_RELOAD_COUNT_FILE="$fixture/reload-count" \
    PKUBA_DEPLOY_CONFIG="$config" \
    PKUBA_RELEASE_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
    bash "$script_dir/deploy-blue-green.sh" \
      v9.9.9 eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
      ghcr.io/jiumao2/pkuba-api@sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee \
      ghcr.io/jiumao2/pkuba-web@sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff \
      >/dev/null 2>&1; then
    echo "invalid deployment state unexpectedly passed" >&2
    exit 1
  fi
  [[ ! -s $docker_log ]]
  [[ ! -e $state_root/maintenance.enabled ]]
}

bad=$fixture/bad.env
cp "$valid_state" "$bad"
printf 'CURRENT_TAG=v1.2.4\n' >>"$bad"
assert_invalid_deploy_state_fails_before_docker "$bad"
for replacement in \
  's/^ACTIVE_SLOT=.*/ACTIVE_SLOT=yellow/' \
  's/^CURRENT_TAG=.*/CURRENT_TAG=latest/' \
  's/^CURRENT_COMMIT=.*/CURRENT_COMMIT=abc/' \
  's#^CURRENT_API_IMAGE=.*#CURRENT_API_IMAGE=ghcr.io/jiumao2/pkuba-api:latest#' \
  's#^CURRENT_RELEASE_DIR=.*#CURRENT_RELEASE_DIR=/tmp/../unsafe#' \
  's/^CURRENT_APP_CAPABILITY=.*/CURRENT_APP_CAPABILITY=legacy-request-type-v0/'; do
  sed "$replacement" "$valid_state" >"$bad"
  assert_invalid_deploy_state_fails_before_docker "$bad"
done
sed "s/^CURRENT_COMMIT=.*/CURRENT_COMMIT=$commit_one/" "$valid_state" >"$bad"
assert_invalid_deploy_state_fails_before_docker "$bad"
injection_marker=$fixture/injection-marker
cp "$valid_state" "$bad"
printf 'UNEXPECTED=$(touch %s)\n' "$injection_marker" >>"$bad"
assert_invalid_deploy_state_fails_before_docker "$bad"
[[ ! -e $injection_marker ]]

backup=$fixture/backup
mkdir -p "$backup"
for file in MANIFEST.env database.dump private-media.tar.gz \
  private-media.files.sha256 archive-staging.tar.gz archive-staging.files.sha256; do
  : >"$backup/$file"
done
cp "$bad" "$backup/previous-release.env"
(cd "$backup" && sha256sum MANIFEST.env database.dump private-media.tar.gz \
  private-media.files.sha256 archive-staging.tar.gz archive-staging.files.sha256 \
  previous-release.env >SHA256SUMS)
: >"$docker_log"
if run_root env \
  PATH="$test_path" \
  PKUBA_FAKE_DOCKER_LOG="$docker_log" \
  PKUBA_TEST_COMMIT_ONE="$commit_one" \
  PKUBA_TEST_COMMIT_TWO="$commit_two" \
  PKUBA_DEPLOY_CONFIG="$config" \
  PKUBA_RELEASE_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
  bash "$script_dir/restore-paired-data.sh" "$backup" RESTORE_PAIRED_DATA \
    >/dev/null 2>&1; then
  echo "hostile paired restore state unexpectedly passed" >&2
  exit 1
fi
[[ ! -s $docker_log ]]
[[ ! -e $injection_marker ]]
[[ ! -e $state_root/maintenance.enabled ]]

# Exercise the deployment commit phase with fake Compose, health endpoints and
# data snapshots. Every injected state-file failure must return the application,
# gateway and all authoritative state files to the exact pre-deploy snapshot.
mkdir -p "$fixture/data-sentinels"
printf 'database\n' >"$fixture/data-sentinels/database"
printf 'media\n' >"$fixture/data-sentinels/media"
printf 'archive\n' >"$fixture/data-sentinels/archive"
before_hash=$(sha256sum "$fixture"/data-sentinels/*)

reset_deploy_fixture() {
  run_root /usr/bin/rm -rf "$fixture/deploy/backups" "$fixture/deploy/logs"
  run_root /usr/bin/rm -rf "$state_root/release-transaction" \
    "$state_root/release-transaction-completed"
  run_root /usr/bin/rm -f "$fixture/reload-count"
  mkdir -p "$state_root/slots" "$fixture/deploy/backups" "$fixture/deploy/logs"
  run_root /usr/bin/rm -f "$state_root/slots/"*.env \
    "$state_root/slots/"*.retain-until "$state_root/maintenance.enabled" \
    "$state_root/release-recovery-required.env"
  write_state "$state_root/current.env" blue v1.2.3 "$commit_one" "$api_one" "$web_one" \
    "$release_root/v1.2.3" reschedule-route-v1
  cat >"$state_root/upstreams.caddy" <<'EOF'
(active_api) {
    reverse_proxy pkuba-blue-api:8000
}

(active_web) {
    reverse_proxy pkuba-blue-web:8080
}
EOF
}

run_deploy() {
  run_root env \
    PATH="$test_path" \
    PKUBA_FAKE_DOCKER_LOG="$docker_log" \
    PKUBA_TEST_COMMIT_ONE="$commit_one" \
    PKUBA_TEST_COMMIT_TWO="$commit_two" \
    PKUBA_TEST_COMMIT_FIVE="$commit_5" \
    PKUBA_TEST_COMMIT_SIX="$commit_6" \
    PKUBA_TEST_COMMIT_SEVEN="$commit_7" \
    PKUBA_TEST_COMMIT_EIGHT="$commit_8" \
    PKUBA_TEST_STATE_ROOT="$state_root" \
    PKUBA_RELOAD_COUNT_FILE="$fixture/reload-count" \
    PKUBA_DEPLOY_CONFIG="$config" \
    PKUBA_RELEASE_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
    "${@}"
}

# Backup-retention enumeration is a preflight, not a best-effort process
# substitution. A producer failure must stop before fetch/worktree cleanup,
# maintenance, transaction state or authoritative-data access.
for enumeration_failure in find sort awk; do
  reset_deploy_fixture
  retention_sentinel=$fixture/deploy/backups/retention-sentinel
  mkdir -p "$retention_sentinel"
  printf 'keep\n' >"$retention_sentinel/SUCCESS"
  printf 'paired-backup\n' >"$retention_sentinel/payload"
  before_state_hash=$(sha256sum "$state_root/current.env" \
    "$state_root/upstreams.caddy")
  before_backup_tree=$(/usr/bin/find "$fixture/deploy/backups" -printf '%P %y %s\n' \
    | /usr/bin/sort)
  before_release_tree=$(/usr/bin/find "$release_root" -printf '%P %y %s\n' \
    | /usr/bin/sort)
  before_worktrees=$(git -C "$repository" worktree list --porcelain)
  : >"$docker_log"
  failure_env=()
  case "$enumeration_failure" in
    find) failure_env+=(PKUBA_FAIL_BACKUP_ENUM_FIND=1) ;;
    sort) failure_env+=(PKUBA_FAIL_BACKUP_ENUM_SORT=1) ;;
    awk) failure_env+=(PKUBA_FAIL_BACKUP_ENUM_AWK=1) ;;
  esac
  ! run_deploy "${failure_env[@]}" \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" \
      >"$fixture/backup-enumeration-$enumeration_failure.log" 2>&1
  grep -Fq 'could not enumerate existing paired backups' \
    "$fixture/backup-enumeration-$enumeration_failure.log"
  [[ $before_state_hash == "$(sha256sum "$state_root/current.env" \
    "$state_root/upstreams.caddy")" ]]
  [[ $before_backup_tree == "$(/usr/bin/find "$fixture/deploy/backups" \
    -printf '%P %y %s\n' | /usr/bin/sort)" ]]
  [[ $before_release_tree == "$(/usr/bin/find "$release_root" \
    -printf '%P %y %s\n' | /usr/bin/sort)" ]]
  [[ $before_worktrees == "$(git -C "$repository" worktree list --porcelain)" ]]
  [[ ! -e $state_root/maintenance.enabled \
    && ! -e $state_root/release-transaction ]]
  ! grep -Eq 'volume inspect|pg_dump|database\.dump|private-media|archive-staging|deployment_preflight' \
    "$docker_log"
done

assert_deploy_commit_failure_restores_everything() {
  local failure_kind=$1 failure_pattern=$2 failure_marker=$fixture/deploy-failure-$1
  reset_deploy_fixture
  /usr/bin/rm -f "$failure_marker"
  before_state_hash=$(sha256sum "$state_root/current.env" "$state_root/upstreams.caddy")
  cp "$state_root/upstreams.caddy" "$fixture/expected-deploy-upstreams.caddy"
  : >"$docker_log"
  local failure_env=()
  if [[ $failure_kind == rm-* ]]; then
    failure_env=(PKUBA_FAIL_RM_ARG_PATTERN="$failure_pattern")
  else
    failure_env=(PKUBA_FAIL_MV_DEST_PATTERN="$failure_pattern")
  fi
  if run_deploy \
    "${failure_env[@]}" \
    PKUBA_FAIL_ONCE_MARKER="$failure_marker" \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" \
      >"$fixture/deploy-failure.log" 2>&1; then
    echo "deployment state failure unexpectedly succeeded: $failure_kind" >&2
    exit 1
  fi
  if [[ ! -e $failure_marker ]]; then
    cat "$fixture/deploy-failure.log" >&2
    echo "deployment did not reach injected state operation: $failure_kind" >&2
    exit 1
  fi
  after_state_hash=$(sha256sum "$state_root/current.env" "$state_root/upstreams.caddy")
  if [[ $before_state_hash != "$after_state_hash" ]]; then
    cat "$fixture/deploy-failure.log" >&2
    diff -u "$fixture/expected-deploy-upstreams.caddy" "$state_root/upstreams.caddy" >&2 || true
    exit 1
  fi
  [[ ! -e $state_root/slots/blue.env ]]
  [[ ! -e $state_root/slots/blue.env.retain-until ]]
  [[ ! -e $state_root/slots/green.env ]]
  [[ ! -e $state_root/slots/green.env.retain-until ]]
  [[ ! -e $state_root/maintenance.enabled ]]
  [[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]
  failed_backup=$(find "$fixture/deploy/backups" -mindepth 1 -maxdepth 1 -type d -print -quit)
  [[ -n $failed_backup ]]
  [[ ! -e $failed_backup/release.json && ! -e $failed_backup/SUCCESS ]]
  if [[ -e $failed_backup/SHA256SUMS ]]; then
    (cd "$failed_backup" && sha256sum --check SHA256SUMS >/dev/null)
  fi
}

assert_deploy_commit_failure_restores_everything mv-retained-state '/slots/blue.env'
assert_deploy_commit_failure_restores_everything mv-retained-deadline '/slots/blue.env.retain-until'
assert_deploy_commit_failure_restores_everything mv-current-state '/current.env'
assert_deploy_commit_failure_restores_everything rm-candidate-state '/slots/green.env'
assert_deploy_commit_failure_restores_everything mv-release-json '/release.json'
assert_deploy_commit_failure_restores_everything mv-checksum '/SHA256SUMS'
assert_deploy_commit_failure_restores_everything mv-success '/SUCCESS'

# Exercise the retained application rollback with all external commands faked.
# Only application Compose operations are allowed; three data sentinels and
# their hashes must remain byte-for-byte unchanged.
reset_rollback_fixture() {
  # A new independent mktemp scenario must not inherit archived transactions.
  # Recovery retries within a scenario never call this reset.
  run_root /usr/bin/rm -rf "$state_root/release-transaction" \
    "$state_root/release-transaction-completed" \
    "$fixture/deploy/logs/release-transactions"
  run_root /usr/bin/rm -f "$fixture/reload-count"
  mkdir -p "$state_root/slots" "$fixture/deploy/logs"
  write_state "$state_root/current.env" green v1.2.4 "$commit_two" "$api_two" "$web_two" \
    "$release_root/v1.2.4" reschedule-route-v2
  write_state "$state_root/slots/blue.env" blue v1.2.3 "$commit_one" "$api_one" "$web_one" \
    "$release_root/v1.2.3" reschedule-route-v1 reschedule-route-v2
  printf '%s\n' "$(( $(date +%s) + 3600 ))" \
    >"$state_root/slots/blue.env.retain-until"
  run_root /usr/bin/rm -f "$state_root/slots/green.env" \
    "$state_root/slots/green.env.retain-until" \
    "$state_root/maintenance.enabled" "$state_root/release-recovery-required.env" \
    "$fixture/deploy/logs/"*-application-rollback.env \
    "$fixture/deploy/logs/"*-release-recovery.env
  cat >"$state_root/upstreams.caddy" <<'EOF'
(active_api) {
    reverse_proxy pkuba-green-api:8000
}

(active_web) {
    reverse_proxy pkuba-green-web:8080
}
EOF
}

run_rollback() {
  run_root env \
    PATH="$test_path" \
    PKUBA_FAKE_DOCKER_LOG="$docker_log" \
    PKUBA_TEST_COMMIT_ONE="$commit_one" \
    PKUBA_TEST_COMMIT_TWO="$commit_two" \
    PKUBA_TEST_STATE_ROOT="$state_root" \
    PKUBA_RELOAD_COUNT_FILE="$fixture/reload-count" \
    PKUBA_DEPLOY_CONFIG="$config" \
    PKUBA_RELEASE_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
    "${@}"
}

assert_rollback_commit_failure_restores_everything() {
  local failure_kind=$1 failure_pattern=$2 failure_marker=$fixture/failure-$1
  reset_rollback_fixture
  /usr/bin/rm -f "$failure_marker"
  before_state_hash=$(sha256sum "$state_root/current.env" \
    "$state_root/slots/blue.env" "$state_root/slots/blue.env.retain-until" \
    "$state_root/upstreams.caddy")
  cp "$state_root/upstreams.caddy" "$fixture/expected-upstreams.caddy"
  : >"$docker_log"
  local failure_env=()
  if [[ $failure_kind == rm-* ]]; then
    failure_env=(PKUBA_FAIL_RM_ARG_PATTERN="$failure_pattern")
  else
    failure_env=(PKUBA_FAIL_MV_DEST_PATTERN="$failure_pattern")
  fi
  if run_rollback \
    "${failure_env[@]}" \
    PKUBA_FAIL_ONCE_MARKER="$failure_marker" \
    bash "$script_dir/rollback-retained-application.sh" \
      blue ROLLBACK_APPLICATION_ONLY >"$fixture/rollback-failure.log" 2>&1; then
    echo "rollback state failure unexpectedly succeeded: $failure_kind" >&2
    exit 1
  fi
  [[ -e $failure_marker ]]
  after_state_hash=$(sha256sum "$state_root/current.env" \
    "$state_root/slots/blue.env" "$state_root/slots/blue.env.retain-until" \
    "$state_root/upstreams.caddy")
  if ! cmp -s "$fixture/expected-upstreams.caddy" "$state_root/upstreams.caddy"; then
    cat "$fixture/rollback-failure.log" >&2
    diff -u "$fixture/expected-upstreams.caddy" "$state_root/upstreams.caddy" >&2 || true
  fi
  [[ $before_state_hash == "$after_state_hash" ]]
  [[ ! -e $state_root/slots/green.env ]]
  [[ ! -e $state_root/slots/green.env.retain-until ]]
  [[ ! -e $state_root/maintenance.enabled ]]
  [[ -z $(find "$fixture/deploy/logs" -name '*-application-rollback.env' -print -quit) ]]
  [[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]
  ! grep -Eq 'volume inspect|pg_dump|database\.dump|private-media|archive-staging' "$docker_log"
}

assert_rollback_commit_failure_restores_everything mv-retained-state '/slots/green.env'
assert_rollback_commit_failure_restores_everything mv-retained-deadline '/slots/green.env.retain-until'
assert_rollback_commit_failure_restores_everything mv-current-state '/current.env'
assert_rollback_commit_failure_restores_everything rm-target-state '/slots/blue.env'
assert_rollback_commit_failure_restores_everything mv-audit 'application-rollback.env'

# Journal-owned paths are parsed before any state mutation or Compose command.
# String prefixes are insufficient because `..` or symlinks could otherwise
# escape the backup/log roots and overwrite unrelated files.
reset_deploy_fixture
! run_deploy PKUBA_TEST_CRASH_POINT=prepared \
  bash "$script_dir/deploy-blue-green.sh" \
    v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null 2>&1
deploy_escape=$fixture/deploy/journal-path-escape
mkdir -p "$deploy_escape"
printf 'outside-backup-root\n' >"$deploy_escape/SHA256SUMS"
deploy_escape_hash=$(sha256sum "$deploy_escape/SHA256SUMS")
sed "s#^BACKUP_DIR=.*#BACKUP_DIR=$fixture/deploy/backups/../journal-path-escape#" \
  "$state_root/release-transaction/journal.env" \
  >"$state_root/release-transaction/journal.env.path-test"
  /usr/bin/mv -f "$state_root/release-transaction/journal.env.path-test" \
    "$state_root/release-transaction/journal.env"
  grep -v '^PHASE=' "$state_root/release-transaction/journal.env" \
    >"$state_root/release-transaction/immutable.env"
  (cd "$state_root/release-transaction" && sha256sum immutable.env >immutable.sha256)
: >"$docker_log"
deploy_path_writers=$fixture/deploy-path-writers
printf 'running\n' >"$deploy_path_writers"
! run_deploy PKUBA_FAKE_WRITERS_FILE="$deploy_path_writers" \
  bash "$script_dir/recover-release-transaction.sh" \
  >"$fixture/deploy-journal-path.log" 2>&1
[[ -e $state_root/maintenance.enabled && -d $state_root/release-transaction ]]
[[ ! -e $deploy_path_writers ]]
grep -Eq '^stop ' "$docker_log"
[[ -f $state_root/release-recovery-required.env ]]
! grep -Eq '^(compose|exec|run|volume) ' "$docker_log"
[[ $deploy_escape_hash == "$(sha256sum "$deploy_escape/SHA256SUMS")" ]]
grep -Fq 'deployment journal backup path is invalid' \
  "$fixture/deploy-journal-path.log"

reset_rollback_fixture
! run_rollback PKUBA_TEST_CRASH_POINT=prepared \
  bash "$script_dir/rollback-retained-application.sh" \
    blue ROLLBACK_APPLICATION_ONLY >/dev/null 2>&1
rollback_escape=$fixture/deploy/unrelated-sentinel
printf 'outside-log-root\n' >"$rollback_escape"
rollback_escape_hash=$(sha256sum "$rollback_escape")
sed "s#^AUDIT_FILE=.*#AUDIT_FILE=$fixture/deploy/logs/../unrelated-sentinel#" \
  "$state_root/release-transaction/journal.env" \
  >"$state_root/release-transaction/journal.env.path-test"
  /usr/bin/mv -f "$state_root/release-transaction/journal.env.path-test" \
    "$state_root/release-transaction/journal.env"
  grep -v '^PHASE=' "$state_root/release-transaction/journal.env" \
    >"$state_root/release-transaction/immutable.env"
  (cd "$state_root/release-transaction" && sha256sum immutable.env >immutable.sha256)
: >"$docker_log"
rollback_path_writers=$fixture/rollback-path-writers
printf 'running\n' >"$rollback_path_writers"
! run_rollback PKUBA_FAKE_WRITERS_FILE="$rollback_path_writers" \
  bash "$script_dir/recover-release-transaction.sh" \
  >"$fixture/rollback-journal-path.log" 2>&1
[[ -e $state_root/maintenance.enabled && -d $state_root/release-transaction ]]
[[ ! -e $rollback_path_writers ]]
grep -Eq '^stop ' "$docker_log"
[[ -f $state_root/release-recovery-required.env ]]
! grep -Eq '^(compose|exec|run|volume) ' "$docker_log"
[[ $rollback_escape_hash == "$(sha256sum "$rollback_escape")" ]]
grep -Fq 'application rollback journal audit path is invalid' \
  "$fixture/rollback-journal-path.log"

prepare_prevalidation_transaction() {
  reset_deploy_fixture
  : >"$docker_log"
  ! run_deploy PKUBA_TEST_CRASH_POINT=prepared \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null 2>&1
  [[ -d $state_root/release-transaction ]]
  /usr/bin/rm -f "$state_root/release-recovery-required.env"
  : >"$docker_log"
}

assert_prevalidation_rejection_is_fenced() {
  local writers_file=$1
  [[ -e $state_root/maintenance.enabled ]]
  [[ -e $state_root/release-transaction \
    || -e $state_root/release-transaction-completed ]]
  [[ -f $state_root/release-recovery-required.env ]]
  [[ ! -e $writers_file ]]
  grep -Eq '^stop ' "$docker_log"
  grep -Eq '^inspect .*State\.Running.* writer-' "$docker_log"
  ! grep -Eq '^(compose|exec|run|volume) ' "$docker_log"
  [[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]
  parsed_after_rejection=$(bash "$script_dir/parse-release-state.sh" \
    "$state_root/current.env")
  IFS=$'\t' read -r rejected_slot rejected_tag _ <<<"$parsed_after_rejection"
  [[ $rejected_slot == blue && $rejected_tag == v1.2.3 ]]
}

identity_order_spy=$fixture/identity-order-spy.sh
cat >"$identity_order_spy" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ -e ${PKUBA_IDENTITY_WRITER_FILE:?} ]]; then
  : >"${PKUBA_IDENTITY_BEFORE_FENCE_MARKER:?}"
  exit 97
fi
printf 'identity\n' >>"${PKUBA_IDENTITY_TRACE:?}"
exec bash "${PKUBA_REAL_IDENTITY_VALIDATOR:?}" "$@"
EOF
chmod +x "$identity_order_spy"

# Every application recovery artifact is fenced before its untrusted journal is
# parsed.  This includes mutually present active/completed directories, an
# invalid completed directory and a tampered active journal.
for prevalidation_case in overlap invalid-completed tampered-active; do
  prepare_prevalidation_transaction
  case "$prevalidation_case" in
    overlap)
      cp -a "$state_root/release-transaction" \
        "$state_root/release-transaction-completed"
      ;;
    invalid-completed)
      /usr/bin/mv "$state_root/release-transaction" \
        "$state_root/release-transaction-completed"
      ;;
    tampered-active)
      printf 'UNEXPECTED=tampered\n' \
        >>"$state_root/release-transaction/journal.env"
      ;;
  esac
  writers_file=$fixture/prevalidation-writers-$prevalidation_case
  printf 'running\n' >"$writers_file"
  ! run_deploy PKUBA_FAKE_WRITERS_FILE="$writers_file" \
    bash "$script_dir/recover-release-transaction.sh" \
      >"$fixture/prevalidation-$prevalidation_case.log" 2>&1
  assert_prevalidation_rejection_is_fenced "$writers_file"
  grep -Fq 'PRE-VALIDATION FAILED' \
    "$fixture/prevalidation-$prevalidation_case.log"
done

# Identity validation is also downstream of the completed stop-and-verify
# fence.  The spy fails if it can observe a writer marker.
prepare_prevalidation_transaction
printf 'UNEXPECTED=tampered\n' \
  >>"$state_root/release-transaction/next/current.env"
identity_writers=$fixture/prevalidation-writers-identity
identity_trace=$fixture/prevalidation-identity.trace
identity_violation=$fixture/prevalidation-identity-before-fence
printf 'running\n' >"$identity_writers"
! run_deploy \
  PKUBA_FAKE_WRITERS_FILE="$identity_writers" \
  PKUBA_RELEASE_IDENTITY_VALIDATOR="$identity_order_spy" \
  PKUBA_REAL_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
  PKUBA_IDENTITY_WRITER_FILE="$identity_writers" \
  PKUBA_IDENTITY_TRACE="$identity_trace" \
  PKUBA_IDENTITY_BEFORE_FENCE_MARKER="$identity_violation" \
  bash "$script_dir/recover-release-transaction.sh" \
    >"$fixture/prevalidation-identity.log" 2>&1
assert_prevalidation_rejection_is_fenced "$identity_writers"
[[ -s $identity_trace && ! -e $identity_violation ]]

# docker ps, stop and inspect failures are all fatal.  The early failure trap
# retries the fence, preserves maintenance/journal/diagnostics and performs no
# Compose or authoritative-data operation.
for fence_failure in first-ps stop inspect final-ps; do
  prepare_prevalidation_transaction
  writers_file=$fixture/fence-failure-writers-$fence_failure
  printf 'running\n' >"$writers_file"
  ps_count_file=$fixture/fence-failure-ps-count-$fence_failure
  stop_marker=$fixture/fence-failure-stop-$fence_failure
  inspect_marker=$fixture/fence-failure-inspect-$fence_failure
  identity_trace=$fixture/fence-failure-identity-$fence_failure
  failure_env=(
    PKUBA_FAKE_WRITERS_FILE="$writers_file"
    PKUBA_DOCKER_PS_COUNT_FILE="$ps_count_file"
    PKUBA_RELEASE_IDENTITY_VALIDATOR="$identity_order_spy"
    PKUBA_REAL_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh"
    PKUBA_IDENTITY_WRITER_FILE="$writers_file"
    PKUBA_IDENTITY_TRACE="$identity_trace"
    PKUBA_IDENTITY_BEFORE_FENCE_MARKER="$fixture/fence-identity-violation-$fence_failure"
  )
  case "$fence_failure" in
    first-ps) failure_env+=(PKUBA_FAIL_DOCKER_PS_AT=1) ;;
    final-ps) failure_env+=(PKUBA_FAIL_DOCKER_PS_AT=11) ;;
    stop) failure_env+=(PKUBA_FAIL_DOCKER_STOP_ONCE_MARKER="$stop_marker") ;;
    inspect) failure_env+=(PKUBA_FAIL_DOCKER_INSPECT_ONCE_MARKER="$inspect_marker") ;;
  esac
  ! run_deploy "${failure_env[@]}" \
    bash "$script_dir/recover-release-transaction.sh" \
      >"$fixture/fence-failure-$fence_failure.log" 2>&1
  assert_prevalidation_rejection_is_fenced "$writers_file"
  [[ ! -e $identity_trace ]]
  grep -Fq 'pre-validation two-slot writer fence' \
    "$fixture/fence-failure-$fence_failure.log"
done

assert_recovery_is_fail_closed() {
  [[ -e $state_root/maintenance.enabled ]]
  [[ -d $state_root/release-transaction ]]
  grep -Eq '^PHASE=RECOVERY_REQUIRED_(OLD|NEW)$' \
    "$state_root/release-transaction/journal.env"
  [[ -f $state_root/release-transaction/recovery-required.env ]]
  [[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]
}

# A failed restore write, second gateway reload, or stable-entry probe must
# never remove maintenance or discard the recovery journal.
for failure_mode in second-reload stable-probe persistent-state-restore; do
  reset_deploy_fixture
  marker=$fixture/deploy-recovery-$failure_mode
  /usr/bin/rm -f "$marker"
  common_failure=(
    PKUBA_FAIL_ONCE_MARKER="$marker"
  )
  case "$failure_mode" in
    second-reload)
      common_failure+=(PKUBA_FAIL_MV_DEST_PATTERN=/current.env PKUBA_FAIL_RELOAD_AT=2)
      ;;
    stable-probe)
      common_failure+=(PKUBA_FAIL_MV_DEST_PATTERN=/current.env PKUBA_FAIL_PUBLIC_TAG=v1.2.3)
      ;;
    persistent-state-restore)
      common_failure+=(
        PKUBA_FAIL_RM_ARG_PATTERN=/slots/green.env
        PKUBA_FAIL_ALWAYS_MV_DEST_PATTERN=/current.env
        PKUBA_FAIL_MV_AFTER_MARKER="$marker"
      )
      ;;
  esac
  ! run_deploy "${common_failure[@]}" \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" \
      >"$fixture/deploy-recovery-$failure_mode.log" 2>&1
  assert_recovery_is_fail_closed
  grep -Fq 'RELEASE RECOVERY FAILED' "$fixture/deploy-recovery-$failure_mode.log"
  case "$failure_mode" in
    second-reload) grep -Fq 'gateway reload failed' "$fixture/deploy-recovery-$failure_mode.log" ;;
    stable-probe) grep -Fq 'stable gateway did not expose' "$fixture/deploy-recovery-$failure_mode.log" ;;
    persistent-state-restore) grep -Fq 'current.env' "$fixture/deploy-recovery-$failure_mode.log" ;;
  esac
  [[ -z $(find "$fixture/deploy/logs" -name '*release-recovery.env' -print -quit) ]]
done

for failure_mode in second-reload stable-probe persistent-state-restore; do
  reset_rollback_fixture
  marker=$fixture/rollback-recovery-$failure_mode
  /usr/bin/rm -f "$marker"
  common_failure=(
    PKUBA_FAIL_ONCE_MARKER="$marker"
  )
  case "$failure_mode" in
    second-reload)
      common_failure+=(PKUBA_FAIL_MV_DEST_PATTERN=/current.env PKUBA_FAIL_RELOAD_AT=2)
      ;;
    stable-probe)
      common_failure+=(PKUBA_FAIL_MV_DEST_PATTERN=/current.env PKUBA_FAIL_PUBLIC_TAG=v1.2.4)
      ;;
    persistent-state-restore)
      common_failure+=(
        PKUBA_FAIL_RM_ARG_PATTERN=/slots/blue.env
        PKUBA_FAIL_ALWAYS_MV_DEST_PATTERN=/current.env
        PKUBA_FAIL_MV_AFTER_MARKER="$marker"
      )
      ;;
  esac
  ! run_rollback "${common_failure[@]}" \
    bash "$script_dir/rollback-retained-application.sh" \
      blue ROLLBACK_APPLICATION_ONLY \
      >"$fixture/rollback-recovery-$failure_mode.log" 2>&1
  assert_recovery_is_fail_closed
  grep -Fq 'RELEASE RECOVERY FAILED' "$fixture/rollback-recovery-$failure_mode.log"
  case "$failure_mode" in
    second-reload) grep -Fq 'gateway reload failed' "$fixture/rollback-recovery-$failure_mode.log" ;;
    stable-probe) grep -Fq 'stable gateway did not expose' "$fixture/rollback-recovery-$failure_mode.log" ;;
    persistent-state-restore) grep -Fq 'current.env' "$fixture/rollback-recovery-$failure_mode.log" ;;
  esac
  [[ -z $(find "$fixture/deploy/logs" -name '*application-rollback.env' -print -quit) ]]
done

# The stable gateway identity can be correct while the public readiness probe
# still fails. That path must retain maintenance and the durable journal, and it
# must never emit a successful recovery audit.
reset_deploy_fixture
! run_deploy PKUBA_TEST_CRASH_POINT=new-committed \
  bash "$script_dir/deploy-blue-green.sh" \
    v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null 2>&1
! run_deploy PKUBA_FAIL_PUBLIC_READY_TAG=v1.2.4 \
  bash "$script_dir/recover-release-transaction.sh" \
    >"$fixture/recovery-public-ready-failure.log" 2>&1
assert_recovery_is_fail_closed
grep -Fq 'stable public entry did not expose the recovered release' \
  "$fixture/recovery-public-ready-failure.log"
[[ -z $(find "$fixture/deploy/logs" -name '*release-recovery.env' -print -quit) ]]
! grep -R -Eq '^result=(completed_candidate|restored_original)$' \
  "$fixture/deploy/logs"

recover_durable_transaction() {
  run_deploy bash "$script_dir/recover-release-transaction.sh" >/dev/null
}

assert_upstreams_for_slot() {
  local expected_slot=$1
  cat >"$fixture/expected-upstreams.caddy" <<EOF
(active_api) {
    reverse_proxy pkuba-$expected_slot-api:8000
}

(active_web) {
    reverse_proxy pkuba-$expected_slot-web:8080
}
EOF
  cmp -s "$fixture/expected-upstreams.caddy" "$state_root/upstreams.caddy"
}

assert_current_release() {
  local expected_slot=$1 expected_tag=$2
  parsed=$(bash "$script_dir/parse-release-state.sh" "$state_root/current.env")
  IFS=$'\t' read -r actual_slot actual_tag _ <<<"$parsed"
  [[ $actual_slot == "$expected_slot" && $actual_tag == "$expected_tag" ]]
  assert_upstreams_for_slot "$expected_slot"
  [[ ! -e $state_root/maintenance.enabled ]]
  [[ ! -e $state_root/release-transaction ]]
  [[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]
}

assert_rollback_fixture_has_no_data_commands() {
  local grep_status
  if grep -Eq 'volume inspect|pg_dump|database\.dump|private-media|archive-staging' "$docker_log"; then
    echo 'rollback fixture unexpectedly logged a data command' >&2
    exit 1
  else
    grep_status=$?
  fi
  if [[ $grep_status != 1 ]]; then
    echo "rollback fixture data-command check failed (exit $grep_status)" >&2
    exit 1
  fi
}

# Independent scenarios must not inherit an earlier scenario's archived
# transaction, even when the clock and from/to tags are identical. Only this
# fixture's PATH is changed; the system clock and production IDs stay intact.
[[ ! -e $fixture/fake-bin/date ]]
cat >"$fixture/fake-bin/date" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${PKUBA_FAKE_FIXED_UTC:-0} == 1 && $# == 2 \
  && $1 == -u && $2 == '+%Y-%m-%dT%H:%M:%SZ' ]]; then
  printf '%s\n' '2026-08-28T00:00:00Z'
else
  exec /usr/bin/date "$@"
fi
EOF
chmod +x "$fixture/fake-bin/date"
same_second_transaction=
for same_second_case in first second; do
  reset_rollback_fixture
  : >"$docker_log"
  if run_rollback PKUBA_FAKE_FIXED_UTC=1 PKUBA_TEST_CRASH_POINT=prepared \
    bash "$script_dir/rollback-retained-application.sh" \
      blue ROLLBACK_APPLICATION_ONLY \
      >"$fixture/same-second-$same_second_case-crash.log" 2>&1; then
    echo 'rollback crash fixture unexpectedly succeeded' >&2
    exit 1
  fi
  current_transaction=$(sed -n 's/^TRANSACTION_ID=//p' \
    "$state_root/release-transaction/journal.env")
  [[ -n $current_transaction ]]
  if [[ -n $same_second_transaction ]]; then
    [[ $current_transaction == "$same_second_transaction" ]]
  fi
  same_second_transaction=$current_transaction
  same_second_archive=$fixture/deploy/logs/release-transactions/$current_transaction
  [[ ! -e $same_second_archive ]] || {
    echo 'independent rollback fixture retained an earlier transaction archive' >&2
    exit 1
  }
  run_rollback PKUBA_FAKE_FIXED_UTC=1 \
    bash "$script_dir/recover-release-transaction.sh" \
      >"$fixture/same-second-$same_second_case-recovery.log" 2>&1
  assert_current_release green v1.2.4
  [[ -d $same_second_archive && ! -e $state_root/release-transaction-completed ]]
  (cd "$same_second_archive" && sha256sum --check completion.sha256 >/dev/null)
  assert_rollback_fixture_has_no_data_commands
done

# Within the same scenario, an actual archive collision must still fail
# closed. Do not call the fixture reset between these two recovery attempts.
archive_before=$(find "$same_second_archive" -type f -exec sha256sum {} + | sort)
cp -a "$same_second_archive" "$state_root/release-transaction"
collision_writers=$fixture/archive-collision-writers
printf 'running\n' >"$collision_writers"
: >"$docker_log"
if run_rollback PKUBA_FAKE_FIXED_UTC=1 PKUBA_FAKE_WRITERS_FILE="$collision_writers" \
  bash "$script_dir/recover-release-transaction.sh" \
    >"$fixture/retained-archive-collision.log" 2>&1; then
  echo 'archive collision recovery unexpectedly succeeded' >&2
  exit 1
fi
grep -Fq 'release transaction archive already exists' "$fixture/retained-archive-collision.log"
assert_recovery_is_fail_closed
[[ ! -e $collision_writers ]]
grep -Eq '^stop ' "$docker_log"
[[ $archive_before == "$(find "$same_second_archive" -type f -exec sha256sum {} + | sort)" ]]
assert_rollback_fixture_has_no_data_commands
/usr/bin/rm -f "$fixture/fake-bin/date"
echo 'Rollback fixture same-second isolation and existing-archive guard passed.'

# SIGKILL cannot run EXIT traps. Every pre-commit crash recovers the old stack;
# only the durable NEW_COMMITTED point finishes the candidate.
deploy_crash_points=(
  prepared runtime-switched state-committing after-retained-state
  after-retained-deadline after-current-state after-candidate-state-removal
  after-release-json after-backup-checksum after-success-marker new-committed
)
for crash in "${deploy_crash_points[@]}"; do
  reset_deploy_fixture
  ! run_deploy PKUBA_TEST_CRASH_POINT="$crash" \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" \
      >"$fixture/deploy-crash-$crash.log" 2>&1
  [[ -e $state_root/maintenance.enabled && -d $state_root/release-transaction ]]
  recover_durable_transaction
  if [[ $crash == new-committed ]]; then
    assert_current_release green v1.2.4
  else
    assert_current_release blue v1.2.3
  fi
done

rollback_crash_points=(
  prepared runtime-switched state-committing after-retained-state
  after-retained-deadline after-current-state after-target-state-removal
  after-audit new-committed
)
for crash in "${rollback_crash_points[@]}"; do
  reset_rollback_fixture
  ! run_rollback PKUBA_TEST_CRASH_POINT="$crash" \
    bash "$script_dir/rollback-retained-application.sh" \
      blue ROLLBACK_APPLICATION_ONLY \
      >"$fixture/rollback-crash-$crash.log" 2>&1
  [[ -e $state_root/maintenance.enabled && -d $state_root/release-transaction ]]
  recover_durable_transaction
  if [[ $crash == new-committed ]]; then
    assert_current_release blue v1.2.3
  else
    assert_current_release green v1.2.4
  fi
done

# Recovery itself is restartable at each durable boundary, including after
# maintenance removal and after the journal directory is atomically retired.
for crash in after-state-recovery after-gateway-reload after-recovery-committed \
  after-maintenance-removal after-transaction-rename; do
  reset_deploy_fixture
  ! run_deploy PKUBA_TEST_CRASH_POINT=new-committed \
    bash "$script_dir/deploy-blue-green.sh" \
      v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null 2>&1
  ! run_deploy PKUBA_TEST_RECOVERY_CRASH_POINT="$crash" \
    bash "$script_dir/recover-release-transaction.sh" >/dev/null 2>&1
  run_deploy bash "$script_dir/recover-release-transaction.sh" >/dev/null
  assert_current_release green v1.2.4
  run_root /usr/bin/rm -rf "$state_root/release-transaction-completed"
done

reset_rollback_fixture
: >"$docker_log"
run_rollback bash "$script_dir/rollback-retained-application.sh" \
  blue ROLLBACK_APPLICATION_ONLY >/dev/null
after_hash=$(sha256sum "$fixture"/data-sentinels/*)
[[ $before_hash == "$after_hash" ]]
parsed_current=$(bash "$script_dir/parse-release-state.sh" "$state_root/current.env")
IFS=$'\t' read -r active_slot active_tag _ _ _ _ active_capability _ <<<"$parsed_current"
[[ $active_slot == blue && $active_tag == v1.2.3 ]]
[[ $active_capability == reschedule-route-v1 ]]
parsed_retained=$(bash "$script_dir/parse-release-state.sh" "$state_root/slots/green.env")
IFS=$'\t' read -r retained_slot retained_tag _ _ _ _ retained_capability retained_allowed <<<"$parsed_retained"
[[ $retained_slot == green && $retained_tag == v1.2.4 ]]
[[ $retained_capability == reschedule-route-v2 ]]
[[ $retained_allowed == reschedule-route-v1 ]]
rollback_audit=$(find "$fixture/deploy/logs" -name '*-application-rollback.env' -print -quit)
grep -Fqx 'database_restored=0' "$rollback_audit"
grep -Fqx 'media_restored=0' "$rollback_audit"
grep -Fqx 'archive_restored=0' "$rollback_audit"
! grep -Eq 'volume inspect|pg_dump|database\.dump|private-media|archive-staging' "$docker_log"

# Host boot follows recovery with one authoritative startup command.  Even if
# Docker brought stale blue/green writers back, the first runtime action must
# stop both slots before Compose starts the current application.
reset_deploy_fixture
printf 'running\n' >"$fixture/writers-running"
: >"$docker_log"
run_deploy PKUBA_FAKE_WRITERS_FILE="$fixture/writers-running" \
  PKUBA_START_STABILITY_SECONDS=0 \
  bash "$script_dir/start-current-application.sh" >/dev/null
boot_stop_line=$(grep -n '^stop ' "$docker_log" | head -n 1 | cut -d: -f1)
boot_up_line=$(grep -n '^compose .* up ' "$docker_log" | head -n 1 | cut -d: -f1)
[[ -n $boot_stop_line && -n $boot_up_line && $boot_stop_line -lt $boot_up_line ]]
[[ ! -e $fixture/writers-running ]]
assert_current_release blue v1.2.3
[[ $before_hash == "$(sha256sum "$fixture"/data-sentinels/*)" ]]

# Build one fully committed paired rollback point, then exercise the destructive
# restore only through fake Docker volumes/DB.  The real verifier still parses
# every manifest, tar member and release identity from the filesystem fixture.
reset_deploy_fixture
: >"$docker_log"
run_deploy bash "$script_dir/deploy-blue-green.sh" \
  v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null
paired_backup=$(find "$fixture/deploy/backups" -mindepth 2 -maxdepth 2 \
  -type f -name SUCCESS -printf '%h\n' | head -n 1)
[[ -n $paired_backup ]]
PATH="$test_path" PKUBA_FAKE_DOCKER_LOG="$docker_log" \
PKUBA_TEST_COMMIT_ONE="$commit_one" PKUBA_TEST_COMMIT_TWO="$commit_two" \
python3 "$script_dir/verify-paired-backup.py" \
  --backup-dir "$paired_backup" --backup-root "$fixture/deploy/backups" \
  --release-root "$release_root" --repository-dir "$repository" \
  --identity-validator "$script_dir/validate-release-identity.sh" \
  --scratch-root "$fixture/paired-preflight" --allow-test-root >/dev/null

reset_paired_fixture() {
  run_root /usr/bin/rm -rf "$state_root/paired-restore-transaction" \
    "$state_root/paired-restore-completed" \
    "$fixture/deploy/incident-snapshots" \
    "$fixture/deploy/logs/paired-restore-transactions"
  run_root /usr/bin/rm -f "$state_root/maintenance.enabled" \
    "$fixture/deploy/logs/"*paired-restore.env "$fixture/reload-count"
  mkdir -p "$fixture/deploy/incident-snapshots" \
    "$fixture/deploy/logs/paired-restore-transactions"
  write_state "$state_root/current.env" green v1.2.4 "$commit_two" \
    "$api_two" "$web_two" "$release_root/v1.2.4" reschedule-route-v2 \
    reschedule-route-v1
  cat >"$state_root/upstreams.caddy" <<'EOF'
(active_api) {
    reverse_proxy pkuba-green-api:8000
}

(active_web) {
    reverse_proxy pkuba-green-web:8080
}
EOF
  : >"$docker_log"
}

run_paired() {
  run_root env \
    PATH="$test_path" \
    PKUBA_FAKE_DOCKER_LOG="$docker_log" \
    PKUBA_TEST_COMMIT_ONE="$commit_one" \
    PKUBA_TEST_COMMIT_TWO="$commit_two" \
    PKUBA_TEST_STATE_ROOT="$state_root" \
    PKUBA_RELOAD_COUNT_FILE="$fixture/reload-count" \
    PKUBA_DEPLOY_CONFIG="$config" \
    PKUBA_RELEASE_IDENTITY_VALIDATOR="$script_dir/validate-release-identity.sh" \
    PKUBA_PAIRED_BACKUP_VERIFIER="$script_dir/verify-paired-backup.py" \
    PKUBA_WRITER_FENCE_COMMAND="$script_dir/fence-deploy-writers.sh" \
    PKUBA_PAIRED_RESTORE_COMMAND="$script_dir/restore-paired-data.sh" \
    "${@}"
}

assert_paired_completed() {
  local parsed audit incident archive
  parsed=$(bash "$script_dir/parse-release-state.sh" "$state_root/current.env")
  IFS=$'\t' read -r slot tag _ <<<"$parsed"
  [[ $slot == blue && $tag == v1.2.3 ]]
  assert_upstreams_for_slot "$slot"
  [[ ! -e $state_root/maintenance.enabled \
    && ! -e $state_root/paired-restore-transaction \
    && ! -e $state_root/paired-restore-completed ]]
  audit=$(find "$fixture/deploy/logs" -maxdepth 1 -name '*-paired-restore.env' -print -quit)
  incident=$(find "$fixture/deploy/incident-snapshots" -mindepth 1 -maxdepth 1 \
    -type d -print -quit)
  archive=$(find "$fixture/deploy/logs/paired-restore-transactions" \
    -mindepth 1 -maxdepth 1 -type d -print -quit)
  [[ -n $audit && -n $incident && -n $archive ]]
  grep -Fqx 'DATABASE_RESTORED=1' "$audit"
  grep -Fqx 'MEDIA_RESTORED=1' "$audit"
  grep -Fqx 'ARCHIVE_RESTORED=1' "$audit"
  cmp -s "$audit" "$incident/RESTORE_COMPLETED"
}

# A recomputed checksum cannot bless a traversing tar.  Initial restore
# preflight may inspect immutable image metadata, but it must not create a
# journal/maintenance marker, stop a writer, inspect a volume or touch data.
reset_paired_fixture
cp "$paired_backup/private-media.tar.gz" "$fixture/original-media.tar.gz"
cp "$paired_backup/SHA256SUMS" "$fixture/original-backup-sha"
cp "$paired_backup/SUCCESS" "$fixture/original-backup-success"
python3 - "$paired_backup/private-media.tar.gz" <<'PY'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as bundle:
    payload = b"escape"
    member = tarfile.TarInfo("../outside-sentinel")
    member.size = len(payload)
    bundle.addfile(member, io.BytesIO(payload))
PY
(
  cd "$paired_backup"
  sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
    private-media.files.sha256 archive-staging.files.sha256 \
    previous-release.env MANIFEST.env season-integrity-after-migrate.json \
    core-migrations.txt release.json >SHA256SUMS
  manifest_sha=$(sha256sum SHA256SUMS | awk '{print $1}')
  transaction=$(sed -n 's/^TRANSACTION_ID=//p' MANIFEST.env)
  cat >SUCCESS <<EOF
TRANSACTION_ID=$transaction
MANIFEST_SHA256=$manifest_sha
COMMITTED_AT=2026-08-25T00:00:00Z
EOF
)
: >"$docker_log"
! run_paired bash "$script_dir/restore-paired-data.sh" \
  "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
[[ ! -e $state_root/maintenance.enabled \
  && ! -e $state_root/paired-restore-transaction \
  && ! -e $fixture/outside-sentinel ]]
! grep -Eq '(^| )(stop|volume inspect|exec|compose)( |$)' "$docker_log"
cp "$fixture/original-media.tar.gz" "$paired_backup/private-media.tar.gz"
cp "$fixture/original-backup-sha" "$paired_backup/SHA256SUMS"
cp "$fixture/original-backup-success" "$paired_backup/SUCCESS"

# Journal paths are bound to the transaction identity. Recomputing the plain
# checksum after swapping an audit object must still fail before that object is
# read or overwritten. An existing transaction intentionally remains fenced.
reset_paired_fixture
! run_paired PKUBA_TEST_PAIRED_CRASH_POINT=after-writer-fence \
  bash "$script_dir/restore-paired-data.sh" \
    "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
paired_sentinel=$fixture/deploy/logs/unrelated-paired-sentinel
printf 'unrelated\n' >"$paired_sentinel"
paired_sentinel_hash=$(sha256sum "$paired_sentinel")
sed "s#^AUDIT_FILE=.*#AUDIT_FILE=$paired_sentinel#" \
  "$state_root/paired-restore-transaction/journal.env" \
  >"$state_root/paired-restore-transaction/journal.env.tampered"
/usr/bin/mv -f "$state_root/paired-restore-transaction/journal.env.tampered" \
  "$state_root/paired-restore-transaction/journal.env"
grep -v '^PHASE=' "$state_root/paired-restore-transaction/journal.env" \
  >"$state_root/paired-restore-transaction/immutable.env"
(
  cd "$state_root/paired-restore-transaction"
  sha256sum immutable.env >immutable.sha256
)
: >"$docker_log"
! run_paired bash "$script_dir/recover-release-transaction.sh" >/dev/null 2>&1
[[ -e $state_root/maintenance.enabled \
  && -d $state_root/paired-restore-transaction \
  && $paired_sentinel_hash == "$(sha256sum "$paired_sentinel")" ]]
! grep -Eq 'image inspect|volume inspect|compose | exec ' "$docker_log"

# A public readiness failure happens while maintenance is still durable and
# before completion/audit creation. Retrying the same journal may then finish.
reset_paired_fixture
! run_paired PKUBA_FAIL_PUBLIC_READY_TAG=v1.2.3 \
  bash "$script_dir/restore-paired-data.sh" \
    "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
[[ -e $state_root/maintenance.enabled \
  && -d $state_root/paired-restore-transaction ]]
[[ -z $(find "$fixture/deploy/logs" -maxdepth 1 \
  -name '*-paired-restore.env' -print -quit) ]]
run_paired bash "$script_dir/recover-release-transaction.sh" >/dev/null
assert_paired_completed

# Resume must fence both slots before the repeated full backup/image check.
reset_paired_fixture
! run_paired PKUBA_TEST_PAIRED_CRASH_POINT=after-writer-fence \
  bash "$script_dir/restore-paired-data.sh" \
    "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
printf 'running\n' >"$fixture/writers-running"
: >"$docker_log"
run_paired PKUBA_FAKE_WRITERS_FILE="$fixture/writers-running" \
  bash "$script_dir/recover-release-transaction.sh" >/dev/null
stop_line=$(grep -n '^stop ' "$docker_log" | head -n 1 | cut -d: -f1)
image_line=$(grep -n '^image inspect ' "$docker_log" | head -n 1 | cut -d: -f1)
[[ -n $stop_line && -n $image_line && $stop_line -lt $image_line ]]
[[ ! -e $fixture/writers-running ]]
assert_paired_completed

# Recovery is itself restartable: a second SIGKILL immediately after the
# reboot writer fence still leaves the same immutable transaction recoverable.
reset_paired_fixture
! run_paired PKUBA_TEST_PAIRED_CRASH_POINT=after-data-restore \
  bash "$script_dir/restore-paired-data.sh" \
    "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
! run_paired PKUBA_TEST_PAIRED_CRASH_POINT=after-writer-fence \
  bash "$script_dir/recover-release-transaction.sh" >/dev/null 2>&1
[[ -e $state_root/maintenance.enabled \
  && -d $state_root/paired-restore-transaction ]]
run_paired bash "$script_dir/recover-release-transaction.sh" >/dev/null
assert_paired_completed

paired_crash_points=(
  after-incident-snapshot after-data-restore after-runtime-restore
  after-completion-payload after-paired-committed after-transaction-rename
  after-completion-audit after-maintenance-removal
)
for crash in "${paired_crash_points[@]}"; do
  reset_paired_fixture
  ! run_paired PKUBA_TEST_PAIRED_CRASH_POINT="$crash" \
    bash "$script_dir/restore-paired-data.sh" \
      "$paired_backup" RESTORE_PAIRED_DATA \
      >"$fixture/paired-crash-$crash.log" 2>&1
  [[ -e $state_root/paired-restore-transaction \
    || -e $state_root/paired-restore-completed ]]
  run_paired bash "$script_dir/recover-release-transaction.sh" >/dev/null
  assert_paired_completed
done

# Cleanup is part of the durable protocol.  A failed maintenance-file removal
# or completed-journal archive must re-establish maintenance, keep a replayable
# journal and stop all writers; a second run finishes idempotently.
for cleanup_failure in maintenance-removal completed-archive; do
  reset_paired_fixture
  cleanup_marker=$fixture/paired-cleanup-$cleanup_failure
  /usr/bin/rm -f "$cleanup_marker"
  cleanup_env=(PKUBA_FAIL_ONCE_MARKER="$cleanup_marker")
  if [[ $cleanup_failure == maintenance-removal ]]; then
    cleanup_env+=(PKUBA_FAIL_RM_ARG_PATTERN=/maintenance.enabled)
  else
    cleanup_env+=(PKUBA_FAIL_MV_DEST_PATTERN=/paired-restore-transactions/)
  fi
  ! run_paired "${cleanup_env[@]}" \
    bash "$script_dir/restore-paired-data.sh" \
      "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
  [[ -e $cleanup_marker && -e $state_root/maintenance.enabled \
    && -d $state_root/paired-restore-transaction ]]
  run_paired bash "$script_dir/recover-release-transaction.sh" >/dev/null
  assert_paired_completed
done

# A crash after the journal archive is already a fully durable success: there
# is nothing to replay and maintenance must already be absent.
reset_paired_fixture
! run_paired PKUBA_TEST_PAIRED_CRASH_POINT=after-completed-archive \
  bash "$script_dir/restore-paired-data.sh" \
    "$paired_backup" RESTORE_PAIRED_DATA >/dev/null 2>&1
assert_paired_completed

# Retention is about recoverability, not directory count.  Build four older,
# independently valid rollback points with distinct FROM/TO worktrees, then
# perform one more deployment.  The newest three backups must all pass the
# identity verifier, including each backup's previous (FROM) application.
for release_number in 5 6 7 8; do
  printf -v commit_name 'commit_%s' "$release_number"
  git -C "$repository" worktree add -q --detach \
    "$release_root/v1.2.$release_number" "${!commit_name}"
done

create_synthetic_backup() {
  local template=$1 created_at=$2 from_tag=$3 from_commit=$4 from_api=$5
  local from_web=$6 from_release=$7 to_tag=$8 to_commit=$9
  local to_api=${10} to_web=${11}
  local compact=${created_at//[-:]/}
  local destination=$fixture/deploy/backups/$compact-pre-$to_tag
  local transaction_id=deploy-$compact-$from_tag-to-$to_tag
  local capability
  capability=$(bash "$script_dir/derive-release-capability.sh" "$from_release")
  mkdir -p "$destination"
  cp -a "$template/." "$destination/"
  write_state "$destination/previous-release.env" blue "$from_tag" "$from_commit" \
    "$from_api" "$from_web" "$from_release" "$capability"
  cat >"$destination/MANIFEST.env" <<EOF
MANIFEST_VERSION=2
TRANSACTION_ID=$transaction_id
CREATED_AT=$created_at
FROM_TAG=$from_tag
FROM_COMMIT=$from_commit
TO_TAG=$to_tag
TO_COMMIT=$to_commit
DATABASE_BYTES=$(stat -c %s "$destination/database.dump")
MEDIA_BYTES=$(stat -c %s "$destination/private-media.tar.gz")
ARCHIVE_BYTES=$(stat -c %s "$destination/archive-staging.tar.gz")
EOF
  cat >"$destination/release.json" <<EOF
{"tag":"$to_tag","commit":"$to_commit","slot":"green","previous_slot":"blue","api_image":"$to_api","web_image":"$to_web","postgres_source_digest":"sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73","postgres_mirror_digest":"sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73","caddy_source_digest":"sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d","caddy_mirror_digest":"sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d","switched_at":"$created_at"}
EOF
  (
    cd "$destination"
    sha256sum database.dump private-media.tar.gz archive-staging.tar.gz \
      private-media.files.sha256 archive-staging.files.sha256 \
      previous-release.env MANIFEST.env season-integrity-after-migrate.json \
      core-migrations.txt release.json >SHA256SUMS
    cat >SUCCESS <<EOF
TRANSACTION_ID=$transaction_id
MANIFEST_SHA256=$(sha256sum SHA256SUMS | awk '{print $1}')
COMMITTED_AT=$created_at
EOF
  )
  /usr/bin/touch -d "$created_at" "$destination/SUCCESS"
  printf '%s\n' "$destination"
}

synthetic_one=$(create_synthetic_backup "$paired_backup" 2026-08-20T00:00:00Z \
  v1.2.4 "$commit_two" "$api_two" "$web_two" "$release_root/v1.2.4" \
  v1.2.5 "$commit_5" "$api_five" "$web_five")
synthetic_two=$(create_synthetic_backup "$paired_backup" 2026-08-21T00:00:00Z \
  v1.2.5 "$commit_5" "$api_five" "$web_five" "$release_root/v1.2.5" \
  v1.2.6 "$commit_6" "$api_six" "$web_six")
synthetic_three=$(create_synthetic_backup "$paired_backup" 2026-08-22T00:00:00Z \
  v1.2.6 "$commit_6" "$api_six" "$web_six" "$release_root/v1.2.6" \
  v1.2.7 "$commit_7" "$api_seven" "$web_seven")
synthetic_four=$(create_synthetic_backup "$paired_backup" 2026-08-23T00:00:00Z \
  v1.2.7 "$commit_7" "$api_seven" "$web_seven" "$release_root/v1.2.7" \
  v1.2.8 "$commit_8" "$api_eight" "$web_eight")
run_root /usr/bin/rm -rf -- "$paired_backup"

# Reset only runtime state; keep the four synthetic rollback points.
run_root /usr/bin/rm -rf "$state_root/release-transaction" \
  "$state_root/release-transaction-completed"
run_root /usr/bin/rm -f "$fixture/reload-count" "$state_root/slots/"*.env \
  "$state_root/slots/"*.retain-until "$state_root/maintenance.enabled"
write_state "$state_root/current.env" blue v1.2.3 "$commit_one" \
  "$api_one" "$web_one" "$release_root/v1.2.3" reschedule-route-v1
cat >"$state_root/upstreams.caddy" <<'EOF'
(active_api) {
    reverse_proxy pkuba-blue-api:8000
}

(active_web) {
    reverse_proxy pkuba-blue-web:8080
}
EOF
: >"$docker_log"
run_deploy bash "$script_dir/deploy-blue-green.sh" \
  v1.2.4 "$commit_two" "$api_two" "$web_two" >/dev/null

mapfile -t retained_backup_dirs < <(
  find "$fixture/deploy/backups" -mindepth 2 -maxdepth 2 \
    -type f -name SUCCESS -printf '%h\n' | sort
)
[[ ${#retained_backup_dirs[@]} -eq 3 ]]
[[ ! -e $synthetic_one && ! -e $synthetic_two ]]
[[ -d $synthetic_three && -d $synthetic_four ]]
for retained_backup in "${retained_backup_dirs[@]}"; do
  PATH="$test_path" \
  PKUBA_FAKE_DOCKER_LOG="$docker_log" \
  PKUBA_TEST_COMMIT_ONE="$commit_one" \
  PKUBA_TEST_COMMIT_TWO="$commit_two" \
  PKUBA_TEST_COMMIT_FIVE="$commit_5" \
  PKUBA_TEST_COMMIT_SIX="$commit_6" \
  PKUBA_TEST_COMMIT_SEVEN="$commit_7" \
  PKUBA_TEST_COMMIT_EIGHT="$commit_8" \
  python3 "$script_dir/verify-paired-backup.py" \
    --backup-dir "$retained_backup" \
    --backup-root "$fixture/deploy/backups" \
    --release-root "$release_root" --repository-dir "$repository" \
    --identity-validator "$script_dir/validate-release-identity.sh" \
    --metadata-only --allow-test-root >/dev/null
done
for retained_tag in v1.2.3 v1.2.4 v1.2.6 v1.2.7 v1.2.8; do
  [[ -d $release_root/$retained_tag ]]
done
[[ ! -e $release_root/v1.2.5 ]]

echo "Release state, identity, capability and application-only rollback checks passed."
