#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir -p "$fixture/bin" "$fixture/state" "$fixture/ssh"
chmod 700 "$fixture/state"

ssh-keygen -q -t ed25519 -N '' -C pkuba-actions-test -f "$fixture/deploy-key"
ssh-keygen -q -t ed25519 -N '' -C pkuba-root-test -f "$fixture/root-key"
ssh-keygen -q -t ed25519 -N '' -C pkuba-other-root-test -f "$fixture/other-root-key"

nonce=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
other_nonce=abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789
authorized_keys=$fixture/ssh/authorized_keys
root_authorized_keys=$fixture/ssh/root-authorized_keys
drop_in=$fixture/ssh/00-pkuba-production.conf
printf 'restrict,command="/usr/local/sbin/pkuba-deploy-gateway" %s\n' \
  "$(<"$fixture/deploy-key.pub")" >"$authorized_keys"
cp "$fixture/root-key.pub" "$root_authorized_keys"
chmod 600 "$authorized_keys" "$root_authorized_keys"
root_key_fingerprint=$(ssh-keygen -lf "$fixture/root-key.pub" -E sha256 \
  | awk 'NR == 1 {print $2}')
other_root_key_fingerprint=$(ssh-keygen -lf "$fixture/other-root-key.pub" -E sha256 \
  | awk 'NR == 1 {print $2}')

cat >"$fixture/bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ ${1:-} == -n ]] && shift
case ${1:-} in
  /usr/local/sbin/pkuba-sync-release-tools)
    shift
    if [[ ${1:-} == verify ]]; then
      shift
      exec env PKUBA_TEST_ALLOW_NON_ROOT=1 \
        PKUBA_TEST_ROOT="$PKUBA_TEST_ROOT" \
        PKUBA_DEPLOY_SSH_STATE_DIR="$PKUBA_DEPLOY_SSH_STATE_DIR" \
        PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE="$PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE" \
        PKUBA_TEST_NOW_EPOCH="${PKUBA_TEST_NOW_EPOCH:-1000}" \
        bash "$PKUBA_RECORDER_UNDER_TEST" "$@"
    fi
    printf '%s\n' "$*" >"$PKUBA_DEPLOY_CAPTURE"
    ;;
  *) exit 99 ;;
esac
EOF
chmod 700 "$fixture/bin/sudo"

gateway_env=(
  PATH="$fixture/bin:$PATH"
  PKUBA_TEST_ROOT="$fixture"
  PKUBA_DEPLOY_SSH_STATE_DIR="$fixture/state"
  PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE="$authorized_keys"
  PKUBA_RECORDER_UNDER_TEST="$script_dir/record-deploy-ssh-verification.sh"
  PKUBA_DEPLOY_CAPTURE="$fixture/deploy.args"
  PKUBA_TEST_NOW_EPOCH=1000
)

gateway_output=$(env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="verify $nonce" \
  bash "$script_dir/deploy-gateway.sh")
[[ $gateway_output == "PKUBA_DEPLOY_GATEWAY_VERIFIED=$nonce" ]]
grep -Fqx "NONCE=$nonce" "$fixture/state/latest.env"
grep -Fqx 'VERIFIED_AT_EPOCH=1000' "$fixture/state/latest.env"
grep -Fqx "AUTHORIZED_KEYS_SHA256=$(sha256sum "$authorized_keys" | awk '{print $1}')" \
  "$fixture/state/latest.env"
grep -Fqx "KEY_FINGERPRINT=$(ssh-keygen -lf "$authorized_keys" -E sha256 | awk 'NR == 1 {print $2}')" \
  "$fixture/state/latest.env"
grep -Fqx 'FORCED_COMMAND=/usr/local/sbin/pkuba-deploy-gateway' \
  "$fixture/state/latest.env"

for rejected_command in verify "verify short" "verify  $nonce" "verify $nonce " \
  "verify $nonce extra" shell $'verify 0123\nwhoami'; do
  set +e
  env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="$rejected_command" \
    bash "$script_dir/deploy-gateway.sh" >/dev/null 2>&1
  rejected_status=$?
  set -e
  if [[ $rejected_status -ne 64 ]]; then
    echo "gateway accepted a rejected command: $rejected_command" >&2
    exit 1
  fi
done

release_commit=0123456789abcdef0123456789abcdef01234567
api_image=ghcr.io/jiumao2/pkuba-api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
web_image=ghcr.io/jiumao2/pkuba-web@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="deploy v1.2.3 $release_commit $api_image $web_image" \
  bash "$script_dir/deploy-gateway.sh"
[[ $(<"$fixture/deploy.args") == "deploy v1.2.3 $release_commit $api_image $web_image" ]]

cat >"$fixture/bin/ssh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
has_forward=0
has_tty=0
for argument in "$@"; do
  [[ $argument == 0:127.0.0.1:9 ]] && has_forward=1
  [[ $argument == -tt ]] && has_tty=1
done
(( has_forward == 0 )) || exit 255
if (( has_tty == 1 )); then
  echo 'PTY allocation request failed on channel 0' >&2
  exit 255
fi
original_command=${!#}
exec env \
  PATH="$PKUBA_FAKE_SSH_PATH" \
  PKUBA_TEST_ROOT="$PKUBA_TEST_ROOT" \
  PKUBA_DEPLOY_SSH_STATE_DIR="$PKUBA_DEPLOY_SSH_STATE_DIR" \
  PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE="$PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE" \
  PKUBA_RECORDER_UNDER_TEST="$PKUBA_RECORDER_UNDER_TEST" \
  PKUBA_DEPLOY_CAPTURE="$PKUBA_DEPLOY_CAPTURE" \
  PKUBA_TEST_NOW_EPOCH=1000 \
  SSH_ORIGINAL_COMMAND="$original_command" \
  bash "$PKUBA_GATEWAY_UNDER_TEST"
EOF
chmod 700 "$fixture/bin/ssh"
printf 'example.test ssh-ed25519 AAAATestHostKey\n' >"$fixture/known-hosts"
client_output=$(env \
  PATH="$fixture/bin:$PATH" \
  PKUBA_FAKE_SSH_PATH="$fixture/bin:$PATH" \
  PKUBA_TEST_ROOT="$fixture" \
  PKUBA_DEPLOY_SSH_STATE_DIR="$fixture/state" \
  PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE="$authorized_keys" \
  PKUBA_RECORDER_UNDER_TEST="$script_dir/record-deploy-ssh-verification.sh" \
  PKUBA_DEPLOY_CAPTURE="$fixture/deploy.args" \
  PKUBA_GATEWAY_UNDER_TEST="$script_dir/deploy-gateway.sh" \
  bash "$script_dir/verify-deploy-ssh.sh" \
    --host example.test --port 22 --user pkuba-deploy \
    --private-key-file "$fixture/deploy-key" \
    --known-hosts-file "$fixture/known-hosts" \
    --root-key-fingerprint "$root_key_fingerprint")
client_nonce=$(grep -Eo -- '--nonce [0-9a-f]{64}' <<<"$client_output" | awk '{print $2}')
[[ $client_nonce =~ ^[0-9a-f]{64}$ ]]
grep -Fq -- "--root-key-fingerprint $root_key_fingerprint" <<<"$client_output"
grep -Fqx "NONCE=$client_nonce" "$fixture/state/latest.env"

cat >"$fixture/bin/sshd" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
case ${1:-} in
  -t) exit 0 ;;
  -T)
    if grep -Fqx 'PasswordAuthentication no' "$PKUBA_SSHD_DROP_IN" 2>/dev/null; then
      pubkey_authentication='pubkeyauthentication yes'
      [[ ${PKUBA_FAKE_EFFECTIVE_PUBKEY_NO:-0} != 1 ]] \
        || pubkey_authentication='pubkeyauthentication no'
      printf '%s\n' 'passwordauthentication no' 'kbdinteractiveauthentication no' \
        "$pubkey_authentication" 'permitrootlogin prohibit-password'
    else
      printf '%s\n' 'passwordauthentication yes' 'kbdinteractiveauthentication no' \
        'pubkeyauthentication yes' 'permitrootlogin yes'
    fi
    ;;
  *) exit 2 ;;
esac
EOF
cat >"$fixture/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
count=0
[[ ! -f $PKUBA_SYSTEMCTL_COUNT ]] || count=$(<"$PKUBA_SYSTEMCTL_COUNT")
count=$((count + 1))
printf '%s\n' "$count" >"$PKUBA_SYSTEMCTL_COUNT"
if [[ ${PKUBA_FAIL_FIRST_RELOAD:-0} == 1 && $count -eq 1 ]]; then
  exit 1
fi
[[ ${1:-} == reload && ${2:-} == ssh ]]
EOF
cat >"$fixture/bin/mv" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ ${PKUBA_FAIL_RESTORE_MOVE:-0} == 1 && ${1:-} == -f && ${2:-} == -- \
  && ${3:-} == *'.pkuba-sshd-backup.'* \
  && ${4:-} == *00-pkuba-production.conf ]]; then
  exit 73
fi
exec /usr/bin/mv "$@"
EOF
chmod 700 "$fixture/bin/sshd" "$fixture/bin/systemctl" "$fixture/bin/mv"

finalizer_env=(
  PATH="$fixture/bin:$PATH"
  PKUBA_TEST_ALLOW_NON_ROOT=1
  PKUBA_TEST_ROOT="$fixture"
  PKUBA_DEPLOY_SSH_STATE_DIR="$fixture/state"
  PKUBA_DEPLOY_AUTHORIZED_KEYS_FILE="$authorized_keys"
  PKUBA_ROOT_AUTHORIZED_KEYS_FILE="$root_authorized_keys"
  PKUBA_SSHD_DROP_IN="$drop_in"
  PKUBA_TEST_NOW_EPOCH=1000
  PKUBA_SYSTEMCTL_COUNT="$fixture/systemctl.count"
)

# The root operator must explicitly confirm that the recovery session and
# console remain available before any sshd write.
if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint "$root_key_fingerprint" \
  >/dev/null 2>&1; then
  echo "finalizer accepted a missing console recovery confirmation" >&2
  exit 1
fi
[[ ! -e $drop_in && ! -e $fixture/systemctl.count ]]

if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --confirm-console-recovery >/dev/null 2>&1; then
  echo "finalizer accepted a missing root maintenance key fingerprint" >&2
  exit 1
fi
if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint SHA256:short \
  --confirm-console-recovery >/dev/null 2>&1; then
  echo "finalizer accepted a malformed root maintenance key fingerprint" >&2
  exit 1
fi
if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint "$other_root_key_fingerprint" \
  --confirm-console-recovery >/dev/null 2>&1; then
  echo "finalizer accepted an unverified root maintenance key" >&2
  exit 1
fi
[[ ! -e $drop_in && ! -e $fixture/systemctl.count ]]

# The proof is bound to the exact root-owned authorized_keys line, not just a
# key fingerprint that would ignore changed forced-command restrictions.
original_authorized_key=$(<"$authorized_keys")
printf '%s changed-comment\n' "$original_authorized_key" >"$authorized_keys"
if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint "$root_key_fingerprint" \
  --confirm-console-recovery >/dev/null 2>&1; then
  echo "finalizer accepted authorized_keys changed after verification" >&2
  exit 1
fi
printf '%s\n' "$original_authorized_key" >"$authorized_keys"
[[ ! -e $drop_in && ! -e $fixture/systemctl.count ]]

mv "$root_authorized_keys" "$root_authorized_keys.missing"
if env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint "$root_key_fingerprint" \
  --confirm-console-recovery >/dev/null 2>&1; then
  echo "finalizer accepted a missing root maintenance key" >&2
  exit 1
fi
mv "$root_authorized_keys.missing" "$root_authorized_keys"
[[ ! -e $drop_in && ! -e $fixture/systemctl.count ]]

env "${finalizer_env[@]}" bash "$script_dir/finalize-deploy-ssh.sh" \
  --nonce "$client_nonce" --root-key-fingerprint "$root_key_fingerprint" \
  --confirm-console-recovery \
  | grep -Fqx 'Deployment SSH gate finalized: passwords are disabled and root accepts keys only.'
grep -Fqx 'PasswordAuthentication no' "$drop_in"
grep -Fqx 'PubkeyAuthentication yes' "$drop_in"
[[ ! -e $fixture/state/latest.env ]]
grep -Fqx "NONCE=$client_nonce" "$fixture/state/finalized.env"
grep -Fqx "AUTHORIZED_KEYS_SHA256=$(sha256sum "$authorized_keys" | awk '{print $1}')" \
  "$fixture/state/finalized.env"
grep -Fqx "KEY_FINGERPRINT=$(ssh-keygen -lf "$authorized_keys" -E sha256 | awk 'NR == 1 {print $2}')" \
  "$fixture/state/finalized.env"
grep -Fqx "ROOT_KEY_FINGERPRINT=$root_key_fingerprint" \
  "$fixture/state/finalized.env"

# A stale proof must fail before touching sshd.
rm -f "$fixture/state/finalized.env"
env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="verify $other_nonce" \
  bash "$script_dir/deploy-gateway.sh" >/dev/null
rm -f "$drop_in" "$fixture/systemctl.count"
if env "${finalizer_env[@]}" PKUBA_TEST_NOW_EPOCH=2000 \
  bash "$script_dir/finalize-deploy-ssh.sh" --nonce "$other_nonce" \
    --root-key-fingerprint "$root_key_fingerprint" \
    --confirm-console-recovery >/dev/null 2>&1; then
  echo "stale verification proof unexpectedly finalized sshd" >&2
  exit 1
fi
[[ ! -e $drop_in && ! -e $fixture/systemctl.count ]]

# A server that does not actually enable public-key authentication must fail
# after the candidate reload and restore the prior (absent) drop-in.
rm -f "$fixture/systemctl.count"
set +e
env "${finalizer_env[@]}" PKUBA_FAKE_EFFECTIVE_PUBKEY_NO=1 \
  bash "$script_dir/finalize-deploy-ssh.sh" --nonce "$other_nonce" \
    --root-key-fingerprint "$root_key_fingerprint" \
    --confirm-console-recovery >"$fixture/pubkey-disabled.out" \
    2>"$fixture/pubkey-disabled.err"
pubkey_disabled_status=$?
set -e
[[ $pubkey_disabled_status -ne 0 ]]
grep -Fq 'effective sshd config does not allow public-key authentication' \
  "$fixture/pubkey-disabled.err"
[[ ! -e $drop_in && ! -e $fixture/state/finalized.env ]]
[[ $(<"$fixture/systemctl.count") == 2 ]]
[[ -f $fixture/state/latest.env ]]

# A reload failure must restore the exact previous file and reload it once.
env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="verify $other_nonce" \
  bash "$script_dir/deploy-gateway.sh" >/dev/null
printf 'PasswordAuthentication yes\n' >"$drop_in"
previous_hash=$(sha256sum "$drop_in")
rm -f "$fixture/systemctl.count"
if env "${finalizer_env[@]}" PKUBA_FAIL_FIRST_RELOAD=1 \
  bash "$script_dir/finalize-deploy-ssh.sh" --nonce "$other_nonce" \
    --root-key-fingerprint "$root_key_fingerprint" \
    --confirm-console-recovery >/dev/null 2>&1; then
  echo "reload failure unexpectedly finalized sshd" >&2
  exit 1
fi
[[ $previous_hash == "$(sha256sum "$drop_in")" ]]
[[ $(<"$fixture/systemctl.count") == 2 ]]
[[ -f $fixture/state/latest.env ]]

# If the recovery write itself fails, the finalizer must return the dedicated
# recovery-required status instead of claiming that the prior config reloaded.
rm -f "$fixture/state/finalized.env"
env "${gateway_env[@]}" SSH_ORIGINAL_COMMAND="verify $other_nonce" \
  bash "$script_dir/deploy-gateway.sh" >/dev/null
printf 'PasswordAuthentication yes\n' >"$drop_in"
rm -f "$fixture/systemctl.count"
set +e
env "${finalizer_env[@]}" PKUBA_FAIL_FIRST_RELOAD=1 PKUBA_FAIL_RESTORE_MOVE=1 \
  bash "$script_dir/finalize-deploy-ssh.sh" --nonce "$other_nonce" \
    --root-key-fingerprint "$root_key_fingerprint" \
    --confirm-console-recovery >"$fixture/restore-write.out" 2>"$fixture/restore-write.err"
restore_write_status=$?
set -e
[[ $restore_write_status -eq 2 ]]
grep -Fq 'CRITICAL: the prior sshd configuration could not be restored and reloaded' \
  "$fixture/restore-write.err"
[[ $(<"$fixture/systemctl.count") == 1 ]]
compgen -G "$fixture/ssh/.pkuba-sshd-backup.*" >/dev/null

echo "Deployment SSH gate tests passed."
