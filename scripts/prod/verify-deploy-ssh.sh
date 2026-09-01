#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: verify-deploy-ssh.sh \
  --host 203.0.113.10 \
  --port 22 \
  --user pkuba-deploy \
  --private-key-file /secure/pkuba-actions \
  --known-hosts-file /secure/pkuba-known-hosts \
  --root-key-fingerprint SHA256:ROOT_MAINTENANCE_KEY_FINGERPRINT

Run this from the trusted management machine that owns the GitHub Actions
deployment private key. It performs only a nonce probe and rejection checks;
it never deploys an application.
EOF
}

die() { echo "deployment SSH verification error: $*" >&2; exit 1; }

host=
port=
deploy_user=
private_key_file=
known_hosts_file=
root_key_fingerprint=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host=$2; shift 2 ;;
    --port) port=$2; shift 2 ;;
    --user) deploy_user=$2; shift 2 ;;
    --private-key-file) private_key_file=$2; shift 2 ;;
    --known-hosts-file) known_hosts_file=$2; shift 2 ;;
    --root-key-fingerprint) root_key_fingerprint=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[[ $host =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || die "invalid SSH host"
[[ $port =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) \
  || die "invalid SSH port"
[[ $deploy_user =~ ^[a-z_][a-z0-9_-]*$ ]] || die "invalid deployment user"
[[ $root_key_fingerprint =~ ^SHA256:[A-Za-z0-9+/]{43}$ ]] \
  || die "invalid root maintenance key fingerprint"
[[ -f $private_key_file && ! -L $private_key_file ]] \
  || die "deployment private key must be a regular non-symlink file"
[[ -f $known_hosts_file && ! -L $known_hosts_file ]] \
  || die "known_hosts must be a regular non-symlink file"
for command_name in mktemp od ssh tr; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done

nonce=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
[[ $nonce =~ ^[0-9a-f]{64}$ ]] || die "could not generate a verification nonce"
expected_marker=PKUBA_DEPLOY_GATEWAY_VERIFIED=$nonce
target=$deploy_user@$host
error_file=$(mktemp)
cleanup() { rm -f -- "$error_file"; }
trap cleanup EXIT

ssh_options=(
  -F /dev/null
  -p "$port"
  -i "$private_key_file"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o IdentityAgent=none
  -o PreferredAuthentications=publickey
  -o PasswordAuthentication=no
  -o KbdInteractiveAuthentication=no
  -o PubkeyAuthentication=yes
  -o StrictHostKeyChecking=yes
  -o UserKnownHostsFile="$known_hosts_file"
  -o GlobalKnownHostsFile=/dev/null
  -o ConnectTimeout=10
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=1
)

run_ssh() {
  local output_name=$1
  local status_name=$2
  shift 2
  local command_output command_status
  set +e
  command_output=$(ssh "$@" 2>"$error_file")
  command_status=$?
  set -e
  printf -v "$output_name" '%s' "$command_output"
  printf -v "$status_name" '%s' "$command_status"
}

run_ssh positive_output positive_status "${ssh_options[@]}" -T "$target" "verify $nonce"
[[ $positive_status -eq 0 && $positive_output == "$expected_marker" ]] \
  || die "the public-key forced-command probe did not return the exact marker"

run_ssh rejected_output rejected_status "${ssh_options[@]}" -T "$target" shell
[[ $rejected_status -eq 64 && -z $rejected_output ]] \
  || die "an arbitrary remote command was not rejected by the forced command"

tty_options=("${ssh_options[@]}")
tty_options+=( -tt )
run_ssh tty_output tty_status "${tty_options[@]}" "$target" "verify $nonce"
[[ $tty_status -ne 0 && $tty_output != *"$expected_marker"* ]] \
  || die "PTY allocation was unexpectedly accepted"
grep -Fq 'PTY allocation request failed' "$error_file" \
  || die "the SSH client did not report an explicit PTY rejection"

forward_options=("${ssh_options[@]}")
forward_options+=( -T -o ExitOnForwardFailure=yes -R 0:127.0.0.1:9 )
run_ssh forward_output forward_status "${forward_options[@]}" "$target" "verify $nonce"
[[ $forward_status -ne 0 && $forward_output != *"$expected_marker"* ]] \
  || die "remote forwarding was unexpectedly accepted"

# Refresh the root-owned proof only after every negative-capability check passed.
run_ssh final_output final_status "${ssh_options[@]}" -T "$target" "verify $nonce"
[[ $final_status -eq 0 && $final_output == "$expected_marker" ]] \
  || die "the final public-key forced-command probe failed"

cat <<EOF
Deployment SSH verification passed without password fallback, arbitrary command,
PTY or forwarding capability. Within 15 minutes, keep the current root recovery
session open and run this exact server-side command:

sudo /usr/local/sbin/pkuba-finalize-deploy-ssh --nonce $nonce --root-key-fingerprint $root_key_fingerprint --confirm-console-recovery
EOF
