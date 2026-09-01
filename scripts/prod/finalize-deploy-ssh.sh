#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

usage() {
  echo "Usage: sudo pkuba-finalize-deploy-ssh --nonce 64_HEX_NONCE --root-key-fingerprint SHA256:FINGERPRINT --confirm-console-recovery"
}

die() { echo "deployment SSH finalization error: $*" >&2; exit 1; }

nonce=
root_key_fingerprint=
console_recovery_confirmed=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --nonce)
      [[ $# -ge 2 ]] || die "--nonce requires a value"
      nonce=$2
      shift 2
      ;;
    --root-key-fingerprint)
      [[ $# -ge 2 ]] || die "--root-key-fingerprint requires a value"
      root_key_fingerprint=$2
      shift 2
      ;;
    --confirm-console-recovery)
      console_recovery_confirmed=1
      shift
      ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done
[[ $nonce =~ ^[0-9a-f]{64}$ ]] || die "invalid verification nonce"
[[ $root_key_fingerprint =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] \
  || die "invalid root maintenance key fingerprint"
[[ $console_recovery_confirmed == 1 ]] \
  || die "keep the current root session open and explicitly confirm console recovery"

test_mode=${PKUBA_TEST_ALLOW_NON_ROOT:-0}
if [[ ${EUID:-$(id -u)} -ne 0 && $test_mode != 1 ]]; then
  die "this command must run as root"
fi

if [[ $test_mode == 1 ]]; then
  test_root=${PKUBA_TEST_ROOT:?missing test root}
  state_dir=${PKUBA_DEPLOY_SSH_STATE_DIR:?missing test state directory}
  authorized_keys=${PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE:?missing test authorized_keys}
  root_authorized_keys=${PKUBA_ROOT_AUTHORIZED_KEYS_FILE:?missing test root authorized_keys}
  sshd_drop_in=${PKUBA_SSHD_DROP_IN:?missing test sshd drop-in}
  expected_deploy_owner=$(id -un):$(id -gn)
  expected_deploy_mode=600
  expected_root_owner=$(id -un):$(id -gn)
  now=${PKUBA_TEST_NOW_EPOCH:-$(date +%s)}
  resolved_test_root=$(realpath -m -- "$test_root")
  for test_path in "$state_dir" "$authorized_keys" "$root_authorized_keys" "$sshd_drop_in"; do
    resolved_path=$(realpath -m -- "$test_path")
    [[ $resolved_path == "$resolved_test_root"/* ]] \
      || die "test paths must remain under the test root"
  done
else
  state_dir=/var/lib/pkuba/deploy-ssh
  authorized_keys=/home/pkuba-deploy/.ssh/authorized_keys
  root_authorized_keys=/root/.ssh/authorized_keys
  sshd_drop_in=/etc/ssh/sshd_config.d/00-pkuba-production.conf
  expected_deploy_owner=root:root
  expected_deploy_mode=644
  expected_root_owner=root:root
  now=$(date +%s)
fi
proof_file=$state_dir/latest.env
finalized_file=$state_dir/finalized.env
sshd_drop_in_dir=$(dirname "$sshd_drop_in")
forced_command=/usr/local/sbin/pkuba-deploy-gateway

for command_name in awk chmod chown cp cut date dirname grep id mktemp mv \
  realpath rm sha256sum ssh-keygen sshd stat sync systemctl wc; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done

[[ -d $state_dir && ! -L $state_dir ]] || die "verification state directory is invalid"
if [[ $test_mode != 1 ]]; then
  [[ $(stat -c '%U:%G:%a' "$state_dir") == root:root:700 ]] \
    || die "verification state directory must be root:root mode 700"
fi
[[ ! -e $finalized_file ]] || die "deployment SSH has already been finalized"
[[ -f $proof_file && ! -L $proof_file ]] || die "missing root-owned verification proof"
[[ $(stat -c '%h' "$proof_file") == 1 ]] || die "verification proof must not be hard-linked"
if [[ $test_mode != 1 ]]; then
  [[ $(stat -c '%U:%G:%a' "$proof_file") == root:root:600 ]] \
    || die "verification proof must be root:root mode 600"
fi
[[ $(wc -l <"$proof_file") -eq 5 ]] || die "verification proof has unexpected fields"
[[ $(grep -Ec '^NONCE=[0-9a-f]{64}$' "$proof_file") -eq 1 ]] \
  || die "verification proof nonce is invalid"
[[ $(grep -Ec '^VERIFIED_AT_EPOCH=[0-9]+$' "$proof_file") -eq 1 ]] \
  || die "verification proof timestamp is invalid"
[[ $(grep -Ec '^AUTHORIZED_KEYS_SHA256=[0-9a-f]{64}$' "$proof_file") -eq 1 ]] \
  || die "verification proof authorized_keys hash is invalid"
[[ $(grep -Ec '^KEY_FINGERPRINT=SHA256:[A-Za-z0-9+/]+$' "$proof_file") -eq 1 ]] \
  || die "verification proof key fingerprint is invalid"
[[ $(grep -Fxc "FORCED_COMMAND=$forced_command" "$proof_file") -eq 1 ]] \
  || die "verification proof forced command is invalid"
proof_nonce=$(grep '^NONCE=' "$proof_file" | cut -d= -f2)
verified_at=$(grep '^VERIFIED_AT_EPOCH=' "$proof_file" | cut -d= -f2)
proof_authorized_keys_sha256=$(grep '^AUTHORIZED_KEYS_SHA256=' "$proof_file" | cut -d= -f2)
proof_key_fingerprint=$(grep '^KEY_FINGERPRINT=' "$proof_file" | cut -d= -f2-)
[[ $proof_nonce == "$nonce" ]] || die "verification proof does not match the supplied nonce"
[[ $now =~ ^[0-9]+$ && $verified_at =~ ^[0-9]+$ ]] || die "invalid proof time"
(( now >= verified_at && now - verified_at <= 900 )) \
  || die "verification proof is stale or from the future"

[[ -f $authorized_keys && ! -L $authorized_keys ]] || die "deployment authorized_keys is invalid"
[[ $(stat -c '%h' "$authorized_keys") == 1 ]] \
  || die "deployment authorized_keys must not be hard-linked"
[[ $(stat -c '%U:%G' "$authorized_keys") == "$expected_deploy_owner" ]] \
  || die "deployment authorized_keys ownership is invalid"
[[ $(stat -c '%a' "$authorized_keys") == "$expected_deploy_mode" ]] \
  || die "deployment authorized_keys mode is invalid"
[[ $(wc -l <"$authorized_keys") -eq 1 ]] \
  || die "deployment authorized_keys must contain exactly one key"
authorized_key=$(<"$authorized_keys")
[[ $authorized_key =~ ^restrict,command=\"/usr/local/sbin/pkuba-deploy-gateway\"\ (ssh-ed25519|ecdsa-sha2-nistp256)\ [A-Za-z0-9+/=]+([[:space:]][^[:cntrl:]]*)?$ ]] \
  || die "deployment authorized_keys lacks the exact forced-command restrictions"
authorized_keys_sha256=$(sha256sum -- "$authorized_keys" | awk '{print $1}')
key_fingerprint=$(ssh-keygen -lf "$authorized_keys" -E sha256 | awk 'NR == 1 {print $2}')
[[ $authorized_keys_sha256 == "$proof_authorized_keys_sha256" ]] \
  || die "deployment authorized_keys changed after external verification"
[[ $key_fingerprint == "$proof_key_fingerprint" ]] \
  || die "deployment key fingerprint changed after external verification"

[[ -f $root_authorized_keys && ! -L $root_authorized_keys ]] \
  || die "root maintenance authorized_keys is invalid"
[[ $(stat -c '%h' "$root_authorized_keys") == 1 ]] \
  || die "root maintenance authorized_keys must not be hard-linked"
[[ $(stat -c '%U:%G' "$root_authorized_keys") == "$expected_root_owner" ]] \
  || die "root maintenance authorized_keys ownership is invalid"
root_key_mode=$(stat -c '%a' "$root_authorized_keys")
(( (8#$root_key_mode & 022) == 0 )) \
  || die "root maintenance authorized_keys must not be group/world writable"
root_key_fingerprints=$(ssh-keygen -lf "$root_authorized_keys" -E sha256 \
  | awk '{print $2}') \
  || die "root maintenance authorized_keys contains no valid public key"
grep -Fqx -- "$root_key_fingerprint" <<<"$root_key_fingerprints" \
  || die "the externally verified root maintenance key is not authorized"

[[ -d $sshd_drop_in_dir && ! -L $sshd_drop_in_dir ]] || die "sshd drop-in directory is invalid"
if [[ $test_mode != 1 ]]; then
  [[ $(stat -c '%U:%G' "$sshd_drop_in_dir") == root:root ]] \
    || die "sshd drop-in directory must be root-owned"
  sshd_drop_in_dir_mode=$(stat -c '%a' "$sshd_drop_in_dir")
  (( (8#$sshd_drop_in_dir_mode & 022) == 0 )) \
    || die "sshd drop-in directory must not be group/world writable"
  [[ $(realpath -e -- "$authorized_keys") == "$authorized_keys" ]] \
    || die "deployment authorized_keys path must not traverse symlinks"
  [[ $(realpath -e -- "$root_authorized_keys") == "$root_authorized_keys" ]] \
    || die "root authorized_keys path must not traverse symlinks"
  for protected_dir in /home/pkuba-deploy /home/pkuba-deploy/.ssh; do
    [[ -d $protected_dir && ! -L $protected_dir ]] \
      || die "deployment home hierarchy is invalid"
    [[ $(stat -c '%U:%G:%a' "$protected_dir") == root:root:755 ]] \
      || die "deployment home hierarchy must be root:root mode 755"
  done
fi
if [[ -e $sshd_drop_in ]]; then
  [[ -f $sshd_drop_in && ! -L $sshd_drop_in ]] \
    || die "existing sshd drop-in is not a regular file"
  [[ $(stat -c '%h' "$sshd_drop_in") == 1 ]] \
    || die "existing sshd drop-in must not be hard-linked"
  if [[ $test_mode != 1 ]]; then
    [[ $(stat -c '%U:%G' "$sshd_drop_in") == root:root ]] \
      || die "existing sshd drop-in must be root-owned"
    existing_drop_in_mode=$(stat -c '%a' "$sshd_drop_in")
    (( (8#$existing_drop_in_mode & 022) == 0 )) \
      || die "existing sshd drop-in must not be group/world writable"
  fi
fi
sshd -t || die "current sshd configuration is invalid"

candidate=$(mktemp "$sshd_drop_in_dir/.00-pkuba-production.conf.XXXXXX")
backup=
finalized_temporary=
had_prior=0
prior_hash=
target_changed=0
finalized=0
finalized_written=0
recovery_failed=0
cleanup() {
  local status=$?
  local restore_content_ok=1
  local restore_durable_ok=1
  local restore_reload_ok=1
  trap - EXIT
  set +e
  [[ -z $candidate ]] || rm -f -- "$candidate"
  [[ -z $finalized_temporary ]] || rm -f -- "$finalized_temporary"
  if [[ $target_changed == 1 && $finalized != 1 ]]; then
    if [[ $had_prior == 1 ]]; then
      if [[ -z $backup || ! -f $backup ]] || ! mv -f -- "$backup" "$sshd_drop_in"; then
        restore_content_ok=0
      elif [[ $(sha256sum -- "$sshd_drop_in" | awk '{print $1}') != "$prior_hash" ]]; then
        restore_content_ok=0
      fi
    else
      rm -f -- "$sshd_drop_in" || restore_content_ok=0
      [[ ! -e $sshd_drop_in ]] || restore_content_ok=0
    fi
    if [[ $restore_content_ok == 1 ]]; then
      if [[ $had_prior == 1 ]]; then
        sync -f "$sshd_drop_in" "$sshd_drop_in_dir" || restore_durable_ok=0
      else
        sync -f "$sshd_drop_in_dir" || restore_durable_ok=0
      fi
      sshd -t >/dev/null 2>&1 || restore_reload_ok=0
      if [[ $restore_reload_ok == 1 ]]; then
        systemctl reload ssh >/dev/null 2>&1 || restore_reload_ok=0
      fi
    else
      restore_durable_ok=0
      restore_reload_ok=0
    fi
    if [[ $restore_content_ok == 1 && $restore_durable_ok == 1 && $restore_reload_ok == 1 ]]; then
      echo "The prior sshd configuration was restored after finalization failed." >&2
    else
      recovery_failed=1
      echo "CRITICAL: the prior sshd configuration could not be restored and reloaded; keep the current root session open and use the console." >&2
      [[ -z $backup || ! -e $backup ]] \
        || echo "The root-only prior configuration copy remains at $backup." >&2
    fi
  fi
  if [[ $finalized_written == 1 && $finalized != 1 ]]; then
    rm -f -- "$finalized_file"
    sync -f "$state_dir" >/dev/null 2>&1 || true
  fi
  if [[ $recovery_failed != 1 && -n $backup ]]; then
    rm -f -- "$backup"
  fi
  (( status != 0 )) || status=1
  if [[ $recovery_failed == 1 ]]; then
    exit 2
  fi
  exit "$status"
}
trap cleanup EXIT

cat >"$candidate" <<'EOF'
# Managed by pkuba-finalize-deploy-ssh. Keep this file first in lexical order:
# OpenSSH uses the first obtained value for each global keyword.
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
EOF
chmod 644 "$candidate"
if [[ $test_mode != 1 ]]; then
  chown root:root "$candidate"
fi
if [[ -e $sshd_drop_in ]]; then
  had_prior=1
  prior_hash=$(sha256sum -- "$sshd_drop_in" | awk '{print $1}')
  backup=$(mktemp "$sshd_drop_in_dir/.pkuba-sshd-backup.XXXXXX")
  cp --preserve=mode,ownership,timestamps -- "$sshd_drop_in" "$backup"
  sync -f "$backup" "$sshd_drop_in_dir"
fi
mv -f -- "$candidate" "$sshd_drop_in"
target_changed=1
sync -f "$sshd_drop_in" "$sshd_drop_in_dir"

sshd -t || die "candidate sshd configuration failed validation"
systemctl reload ssh || die "sshd reload failed"
effective_config=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1) \
  || die "could not read the effective root sshd configuration"
grep -Fqx 'passwordauthentication no' <<<"$effective_config" \
  || die "effective sshd config still allows password authentication"
grep -Fqx 'kbdinteractiveauthentication no' <<<"$effective_config" \
  || die "effective sshd config still allows keyboard-interactive authentication"
grep -Fqx 'pubkeyauthentication yes' <<<"$effective_config" \
  || die "effective sshd config does not allow public-key authentication"
grep -Eq '^permitrootlogin (prohibit-password|without-password)$' <<<"$effective_config" \
  || die "effective sshd config does not restrict root to keys"

finalized_at=$(date +%s)
finalized_temporary=$(mktemp "$state_dir/.finalized.env.XXXXXX")
printf 'NONCE=%s\nVERIFIED_AT_EPOCH=%s\nAUTHORIZED_KEYS_SHA256=%s\nKEY_FINGERPRINT=%s\nROOT_KEY_FINGERPRINT=%s\nFORCED_COMMAND=%s\nFINALIZED_AT_EPOCH=%s\n' \
  "$nonce" "$verified_at" "$authorized_keys_sha256" "$key_fingerprint" \
  "$root_key_fingerprint" "$forced_command" "$finalized_at" >"$finalized_temporary"
chmod 600 "$finalized_temporary"
if [[ $test_mode != 1 ]]; then
  chown root:root "$finalized_temporary"
fi
mv -f -- "$finalized_temporary" "$finalized_file"
finalized_written=1
sync -f "$sshd_drop_in" "$sshd_drop_in_dir" "$finalized_file" "$state_dir"
finalized=1
target_changed=0

if ! rm -f -- "$proof_file" "$backup" || ! sync -f "$sshd_drop_in_dir" "$state_dir"; then
  die "SSH gate is finalized, but root-only temporary cleanup failed"
fi
trap - EXIT

echo "Deployment SSH gate finalized: passwords are disabled and root accepts keys only."
