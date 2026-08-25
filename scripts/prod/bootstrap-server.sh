#!/usr/bin/env bash
set -euo pipefail

umask 077

usage() {
  cat <<'EOF'
Usage: sudo bootstrap-server.sh \
  --deploy-public-key-file /root/pkuba-actions.pub \
  --github-read-key-file /root/pkuba-github-readonly \
  --backup-dir /opt/pkuba/backups/LATEST-CONSISTENT-BACKUP \
  --current-tag v0.2.0 \
  --current-commit 40_HEX_COMMIT \
  --current-api-image ghcr.io/jiumao2/pkuba-api@sha256:64_HEX_DIGEST \
  --current-web-image ghcr.io/jiumao2/pkuba-web@sha256:64_HEX_DIGEST

The backup directory must contain SHA256SUMS, database.dump and
private-media.tar.gz from a consistent stopped-writer backup.
EOF
}

die() {
  echo "bootstrap error: $*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"

deploy_public_key_file=
github_read_key_file=
backup_dir=
current_tag=
current_commit=
current_api_image=
current_web_image=
allow_synthetic_test_data=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-public-key-file) deploy_public_key_file=$2; shift 2 ;;
    --github-read-key-file) github_read_key_file=$2; shift 2 ;;
    --backup-dir) backup_dir=$2; shift 2 ;;
    --current-tag) current_tag=$2; shift 2 ;;
    --current-commit) current_commit=$2; shift 2 ;;
    --current-api-image) current_api_image=$2; shift 2 ;;
    --current-web-image) current_web_image=$2; shift 2 ;;
    --allow-synthetic-test-data) allow_synthetic_test_data=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[[ -f $deploy_public_key_file ]] || die "missing GitHub Actions public key"
[[ -f $github_read_key_file ]] || die "missing GitHub repository read key"
[[ -d $backup_dir ]] || die "missing consistent backup directory"
[[ -f $backup_dir/SHA256SUMS ]] || die "backup has no SHA256SUMS"
[[ -f $backup_dir/database.dump ]] || die "backup has no database.dump"
[[ -f $backup_dir/private-media.tar.gz ]] || die "backup has no private-media.tar.gz"
(cd "$backup_dir" && sha256sum --check SHA256SUMS)

[[ $current_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid current tag"
[[ $current_commit =~ ^[0-9a-f]{40}$ ]] || die "invalid current commit"
[[ $current_api_image =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] \
  || die "current API image must use an immutable digest"
[[ $current_web_image =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] \
  || die "current web image must use an immutable digest"

runtime_dir=/opt/pkuba/ip-test
deploy_root=/opt/pkuba/deploy
repository_dir=/opt/pkuba/repository
release_root=$deploy_root/releases
state_dir=$deploy_root/state
deploy_user=pkuba-deploy
compose_project=pkuba-ip-test

[[ -f $runtime_dir/.env ]] || die "missing $runtime_dir/.env"
docker volume inspect "${compose_project}_postgres-data" >/dev/null
docker volume inspect "${compose_project}_private-media" >/dev/null

if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
passwd --lock "$deploy_user" >/dev/null

install -d -m 700 -o "$deploy_user" -g "$deploy_user" "/home/$deploy_user/.ssh"
public_key=$(tr -d '\r\n' <"$deploy_public_key_file")
[[ $public_key == ssh-ed25519\ * || $public_key == ecdsa-sha2-nistp256\ * ]] \
  || die "deployment key must be Ed25519 or ECDSA"
printf 'restrict,command="/usr/local/sbin/pkuba-deploy-gateway" %s\n' "$public_key" \
  >"/home/$deploy_user/.ssh/authorized_keys"
chown "$deploy_user:$deploy_user" "/home/$deploy_user/.ssh/authorized_keys"
chmod 600 "/home/$deploy_user/.ssh/authorized_keys"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
install -o root -g root -m 755 "$script_dir/deploy-gateway.sh" \
  /usr/local/sbin/pkuba-deploy-gateway
install -o root -g root -m 755 "$script_dir/deploy-release.sh" \
  /usr/local/sbin/pkuba-deploy-release
install -o root -g root -m 755 "$script_dir/deploy-blue-green.sh" \
  /usr/local/sbin/pkuba-deploy-blue-green
printf '%s\n' \
  "$deploy_user ALL=(root) NOPASSWD: /usr/local/sbin/pkuba-deploy-blue-green *" \
  >/etc/sudoers.d/pkuba-deploy
chmod 440 /etc/sudoers.d/pkuba-deploy
visudo --check --file=/etc/sudoers.d/pkuba-deploy >/dev/null

install -d -o root -g root -m 700 /root/.ssh
install -o root -g root -m 600 "$github_read_key_file" /root/.ssh/pkuba-github-readonly
ssh-keygen -F github.com -f /root/.ssh/known_hosts >/dev/null \
  || die "verify and add github.com to /root/.ssh/known_hosts before bootstrapping"

install -d -o root -g root -m 700 "$deploy_root" "$release_root" "$state_dir"
if [[ ! -d $repository_dir/.git ]]; then
  GIT_SSH_COMMAND='ssh -i /root/.ssh/pkuba-github-readonly -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes' \
    git clone --filter=blob:none git@github.com:jiumao2/PKUBA_Miniprogram.git \
    "$repository_dir"
fi
git -C "$repository_dir" config core.sshCommand \
  'ssh -i /root/.ssh/pkuba-github-readonly -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'
git -C "$repository_dir" fetch --force origin main \
  "+refs/tags/$current_tag:refs/tags/$current_tag"
[[ $(git -C "$repository_dir" rev-parse "$current_tag^{commit}") == "$current_commit" ]] \
  || die "current tag and commit do not match"

current_release_dir=$release_root/$current_tag
if [[ ! -e $current_release_dir ]]; then
  git -C "$repository_dir" worktree add --detach "$current_release_dir" "$current_commit"
fi

cat >/etc/pkuba-deploy.conf <<EOF
PKUBA_DEPLOY_ROOT=/opt/pkuba/deploy
PKUBA_REPOSITORY_DIR=/opt/pkuba/repository
PKUBA_RUNTIME_DIR=/opt/pkuba/ip-test
PKUBA_COMPOSE_PROJECT=pkuba-ip-test
PKUBA_ENV_FILE=/opt/pkuba/ip-test/.env
PKUBA_DEPLOY_PREFLIGHT_WAIT_SECONDS=900
PKUBA_DEPLOY_MIN_HEADROOM_BYTES=2147483648
PKUBA_ENFORCE_DATA_GATE=$((1 - allow_synthetic_test_data))
PKUBA_ENABLE_EMAIL_PROFILE=0
PKUBA_PRODUCTION_AUTOMATION_ARMED=0
EOF
chmod 600 /etc/pkuba-deploy.conf

cat >"$state_dir/current.env" <<EOF
ACTIVE_SLOT=uninitialized
CURRENT_TAG=$current_tag
CURRENT_COMMIT=$current_commit
CURRENT_API_IMAGE=$current_api_image
CURRENT_WEB_IMAGE=$current_web_image
CURRENT_RELEASE_DIR=$current_release_dir
BASELINE_CONVERSION_REQUIRED=1
EOF
chmod 600 "$state_dir/current.env"

cat <<EOF
Server bootstrap completed without changing running containers.

Next one-time checks:
1. docker login ghcr.io with a read:packages-only token.
2. Add the private half of $deploy_public_key_file to PROD_SSH_PRIVATE_KEY.
3. Pin this server's host key in PROD_SSH_KNOWN_HOSTS.
4. Do not run the forced deploy command yet: blue/green baseline conversion is still required.
5. Keep PKUBA_PRODUCTION_AUTOMATION_ARMED=0 until isolated rehearsals pass.
EOF
