#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

die() { echo "deployment SSH proof error: $*" >&2; exit 1; }

test_mode=${PKUBA_TEST_ALLOW_NON_ROOT:-0}
if [[ ${EUID:-$(id -u)} -ne 0 && $test_mode != 1 ]]; then
  die "this command must run as root"
fi
[[ $# -eq 1 ]] || die "expected one verification nonce"
nonce=$1
[[ $nonce =~ ^[0-9a-f]{64}$ ]] || die "invalid verification nonce"
forced_command=/usr/local/sbin/pkuba-deploy-gateway

if [[ $test_mode == 1 ]]; then
  state_dir=${PKUBA_DEPLOY_SSH_STATE_DIR:?missing test state directory}
  authorized_keys=${PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE:?missing test authorized_keys}
  test_root=${PKUBA_TEST_ROOT:?missing test root}
  resolved_state=$(realpath -m -- "$state_dir")
  resolved_authorized_keys=$(realpath -m -- "$authorized_keys")
  resolved_test_root=$(realpath -m -- "$test_root")
  [[ $resolved_state == "$resolved_test_root"/* ]] \
    || die "test state directory must remain under the test root"
  [[ $resolved_authorized_keys == "$resolved_test_root"/* ]] \
    || die "test authorized_keys must remain under the test root"
  expected_authorized_keys_owner=$(id -un):$(id -gn)
  expected_authorized_keys_mode=600
  now=${PKUBA_TEST_NOW_EPOCH:-$(date +%s)}
else
  state_dir=/var/lib/pkuba/deploy-ssh
  authorized_keys=/home/pkuba-deploy/.ssh/authorized_keys
  expected_authorized_keys_owner=root:root
  expected_authorized_keys_mode=644
  now=$(date +%s)
fi

for command_name in awk chmod cut date grep id mktemp mv readlink realpath \
  sha256sum ssh-keygen stat sync wc; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done

if [[ $test_mode != 1 ]]; then
  [[ ${SUDO_USER:-} == pkuba-deploy ]] \
    || die "verification proof must be requested by the deployment account"
  [[ ${SUDO_UID:-} == "$(id -u pkuba-deploy)" ]] \
    || die "verification proof has an unexpected deployment uid"

  ancestor_pid=$PPID
  sshd_ancestor_found=0
  for (( depth = 0; depth < 12; depth += 1 )); do
    [[ $ancestor_pid =~ ^[0-9]+$ && $ancestor_pid -gt 1 ]] || break
    ancestor_status=/proc/$ancestor_pid/status
    [[ -r $ancestor_status ]] || break
    ancestor_uid=$(awk '/^Uid:/ {print $2; exit}' "$ancestor_status")
    ancestor_exe=$(readlink -f "/proc/$ancestor_pid/exe" 2>/dev/null || true)
    if [[ $ancestor_uid == 0 && $ancestor_exe == /usr/sbin/sshd ]]; then
      sshd_ancestor_found=1
      break
    fi
    ancestor_pid=$(awk '/^PPid:/ {print $2; exit}' "$ancestor_status")
  done
  [[ $sshd_ancestor_found == 1 ]] \
    || die "verification proof must originate from a root sshd session"
fi

[[ -d $state_dir && ! -L $state_dir ]] || die "verification state directory is invalid"
if [[ $test_mode != 1 ]]; then
  [[ $(stat -c '%U:%G:%a' "$state_dir") == root:root:700 ]] \
    || die "verification state directory must be root:root mode 700"
fi
[[ -f $authorized_keys && ! -L $authorized_keys ]] \
  || die "deployment authorized_keys is invalid"
[[ $(stat -c '%h' "$authorized_keys") == 1 ]] \
  || die "deployment authorized_keys must not be hard-linked"
[[ $(stat -c '%U:%G' "$authorized_keys") == "$expected_authorized_keys_owner" ]] \
  || die "deployment authorized_keys ownership is invalid"
[[ $(stat -c '%a' "$authorized_keys") == "$expected_authorized_keys_mode" ]] \
  || die "deployment authorized_keys mode is invalid"
[[ $(wc -l <"$authorized_keys") -eq 1 ]] \
  || die "deployment authorized_keys must contain exactly one key"
authorized_key=$(<"$authorized_keys")
[[ $authorized_key =~ ^restrict,command=\"/usr/local/sbin/pkuba-deploy-gateway\"\ (ssh-ed25519|ecdsa-sha2-nistp256)\ [A-Za-z0-9+/=]+([[:space:]][^[:cntrl:]]*)?$ ]] \
  || die "deployment authorized_keys lacks the exact forced-command restrictions"
authorized_keys_sha256=$(sha256sum -- "$authorized_keys" | awk '{print $1}')
[[ $authorized_keys_sha256 =~ ^[0-9a-f]{64}$ ]] \
  || die "could not hash deployment authorized_keys"
key_fingerprint=$(ssh-keygen -lf "$authorized_keys" -E sha256 | awk 'NR == 1 {print $2}')
[[ $key_fingerprint =~ ^SHA256:[A-Za-z0-9+/]+$ ]] \
  || die "could not fingerprint the deployment key"
[[ $now =~ ^[0-9]+$ ]] || die "invalid verification timestamp"

proof_file=$state_dir/latest.env
temporary_file=$(mktemp "$state_dir/.latest.env.XXXXXX")
cleanup() { rm -f -- "$temporary_file"; }
trap cleanup EXIT
printf 'NONCE=%s\nVERIFIED_AT_EPOCH=%s\nAUTHORIZED_KEYS_SHA256=%s\nKEY_FINGERPRINT=%s\nFORCED_COMMAND=%s\n' \
  "$nonce" "$now" "$authorized_keys_sha256" "$key_fingerprint" "$forced_command" \
  >"$temporary_file"
chmod 600 "$temporary_file"
if [[ $test_mode != 1 ]]; then
  chown root:root "$temporary_file"
fi
mv -f -- "$temporary_file" "$proof_file"
sync -f "$proof_file" "$state_dir"
trap - EXIT

printf 'PKUBA_DEPLOY_GATEWAY_VERIFIED=%s\n' "$nonce"
