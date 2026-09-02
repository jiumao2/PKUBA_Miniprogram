#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

usage() {
  cat <<'EOF'
Usage: sudo /usr/bin/bash /root/pkuba-prod-tools/bootstrap-server.sh \
  --deploy-public-key-file /root/pkuba-actions.pub \
  --github-read-key-file /root/pkuba-github-readonly \
  --release-tag v1.0.2 \
  --release-commit 40_HEX_COMMIT \
  --api-image ghcr.io/jiumao2/pkuba-api@sha256:64_HEX_DIGEST \
  --web-image ghcr.io/jiumao2/pkuba-web@sha256:64_HEX_DIGEST

This command initializes a fresh production database and the permanent
/opt/pkuba/production namespace. It never imports QA, demo or legacy data.
It interactively invokes bootstrap_first_superadmin and then
bootstrap_admin_registration_policy exactly once while the public gateway
remains in maintenance mode. Secrets are read from this terminal without echo.
It intentionally leaves password authentication unchanged until the external
deployment-key probe and the separate root-only SSH finalizer have succeeded.
EOF
}

die() { echo "bootstrap error: $*" >&2; exit 1; }
[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"

deploy_public_key_file=
github_read_key_file=
release_tag=
release_commit=
api_image=
web_image=
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy-public-key-file) deploy_public_key_file=$2; shift 2 ;;
    --github-read-key-file) github_read_key_file=$2; shift 2 ;;
    --release-tag) release_tag=$2; shift 2 ;;
    --release-commit) release_commit=$2; shift 2 ;;
    --api-image) api_image=$2; shift 2 ;;
    --web-image) web_image=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[[ -f $deploy_public_key_file ]] || die "missing GitHub Actions public key"
[[ -f $github_read_key_file ]] || die "missing GitHub repository read key"
[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "release tag must be a stable vMAJOR.MINOR.PATCH"
[[ $release_commit =~ ^[0-9a-f]{40}$ ]] || die "invalid release commit"
[[ $api_image =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] \
  || die "API image must use the approved immutable digest"
[[ $web_image =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] \
  || die "web image must use the approved immutable digest"

production_root=/opt/pkuba/production
runtime_dir=$production_root/runtime
deploy_root=$production_root/deploy
repository_dir=$production_root/repository
env_file=$production_root/.env
release_root=$deploy_root/releases
state_dir=$deploy_root/state
slot_state_dir=$state_dir/slots
backup_root=$production_root/backups
deploy_user=pkuba-deploy
runtime_network=pkuba-prod-runtime
data_project=pkuba-data
gateway_project=pkuba-gateway
postgres_volume=pkuba-prod-postgres
media_volume=pkuba-prod-media
archive_volume=pkuba-prod-archives
caddy_data_volume=pkuba-prod-caddy-data
caddy_config_volume=pkuba-prod-caddy-config
postgres_source_digest=sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
caddy_source_digest=sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
postgres_image=ghcr.io/jiumao2/pkuba-postgres@$postgres_source_digest
caddy_image=ghcr.io/jiumao2/pkuba-caddy@$caddy_source_digest
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for command_name in awk curl df docker git grep id install passwd python3 rm sed seq \
  sha256sum sleep sshd ssh-keygen stat sync systemctl tr ufw useradd visudo; do
  command -v "$command_name" >/dev/null 2>&1 || die "missing command: $command_name"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose is unavailable"
[[ -f $env_file ]] || die "create the root-owned 0600 server environment first: $env_file"
[[ $(stat -c '%U:%G:%a' "$env_file") == root:root:600 ]] \
  || die "$env_file must be root:root mode 600"
read -r _ available_bytes < <(df -PB1 "$production_root" | awk 'NR == 2 {print $2, $4}')
[[ $available_bytes =~ ^[0-9]+$ ]] || die "could not determine free disk space"
(( available_bytes >= 16106127360 )) \
  || die "fresh production startup requires at least 15 GiB free"

# Fail before mutating production if a baseline or persistent production data
# already exists. Re-running bootstrap is never an upgrade or reset path.
[[ ! -e $state_dir/current.env ]] || die "production is already initialized"
for volume in "$postgres_volume" "$media_volume" "$archive_volume"; do
  if docker volume inspect "$volume" >/dev/null 2>&1; then
    die "refusing an existing production data volume: $volume"
  fi
done

if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
passwd --lock "$deploy_user" >/dev/null
install -d -m 755 -o root -g root "/home/$deploy_user"
install -d -m 755 -o root -g root "/home/$deploy_user/.ssh"
public_key=$(tr -d '\r\n' <"$deploy_public_key_file")
[[ $public_key == ssh-ed25519\ * || $public_key == ecdsa-sha2-nistp256\ * ]] \
  || die "deployment key must be Ed25519 or ECDSA"
printf 'restrict,command="/usr/local/sbin/pkuba-deploy-gateway" %s\n' "$public_key" \
  >"/home/$deploy_user/.ssh/authorized_keys"
chown root:root "/home/$deploy_user/.ssh/authorized_keys"
chmod 644 "/home/$deploy_user/.ssh/authorized_keys"
sync -f "/home/$deploy_user/.ssh/authorized_keys" "/home/$deploy_user/.ssh" "/home/$deploy_user"

install -d -o root -g root -m 700 /root/.ssh
install -o root -g root -m 600 "$github_read_key_file" /root/.ssh/pkuba-github-readonly
ssh-keygen -F github.com -f /root/.ssh/known_hosts >/dev/null \
  || die "verify and add github.com to /root/.ssh/known_hosts before bootstrapping"

install -d -o root -g root -m 700 "$production_root" "$runtime_dir" "$deploy_root" \
  "$release_root" "$state_dir" "$slot_state_dir" "$backup_root" \
  "$backup_root/daily" "$backup_root/weekly"
install -d -o root -g root -m 755 /usr/local/libexec/pkuba
install -d -o root -g root -m 700 /var/lib/pkuba/deploy-ssh
if [[ ! -d $repository_dir/.git ]]; then
  GIT_SSH_COMMAND='ssh -i /root/.ssh/pkuba-github-readonly -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes' \
    git clone --filter=blob:none git@github.com:jiumao2/PKUBA_Miniprogram.git "$repository_dir"
fi
git -C "$repository_dir" config core.sshCommand \
  'ssh -i /root/.ssh/pkuba-github-readonly -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes'
git -C "$repository_dir" fetch --force origin main "+refs/tags/$release_tag:refs/tags/$release_tag"
[[ $(git -C "$repository_dir" rev-parse "$release_tag^{commit}") == "$release_commit" ]] \
  || die "release tag and commit do not match"
release_dir=$release_root/$release_tag
git -C "$repository_dir" worktree add --detach "$release_dir" "$release_commit"
[[ $(git -C "$release_dir" rev-parse HEAD) == "$release_commit" ]] \
  || die "release worktree identity mismatch"

# Install the complete verified toolset and expose every stable host path only
# as a symlink through one atomic current pointer.
env \
  PKUBA_DEPLOY_ROOT="$deploy_root" \
  PKUBA_REPOSITORY_DIR="$repository_dir" \
  PKUBA_DEPLOY_STATE_DIR="$state_dir" \
  PKUBA_RELEASE_TOOLSET_ROOT=/usr/local/libexec/pkuba/toolsets \
  PKUBA_DEPLOY_LOCK_HELPER="$release_dir/scripts/prod/acquire-deploy-lock.py" \
  python3 "$release_dir/scripts/prod/acquire-deploy-lock.py" \
    --state-dir "$state_dir" --timeout 1800 -- \
    bash "$release_dir/scripts/prod/sync-release-tools.sh" \
      activate-source "$release_commit" "$release_dir"
printf '%s\n' \
  "$deploy_user ALL=(root) NOPASSWD: /usr/local/sbin/pkuba-sync-release-tools verify *" \
  "$deploy_user ALL=(root) NOPASSWD: /usr/local/sbin/pkuba-sync-release-tools deploy *" \
  >/etc/sudoers.d/pkuba-deploy
chmod 440 /etc/sudoers.d/pkuba-deploy
visudo --check --file=/etc/sudoers.d/pkuba-deploy >/dev/null

for image in "$api_image" "$web_image" "$postgres_image" "$caddy_image"; do
  docker pull "$image"
done
for image in "$api_image" "$web_image"; do
  [[ $(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image") == "$release_commit" ]] \
    || die "application image revision does not match release commit"
done

docker network create --label io.pkuba.environment=production "$runtime_network" >/dev/null
for volume in "$postgres_volume" "$media_volume" "$archive_volume" \
  "$caddy_data_volume" "$caddy_config_volume"; do
  docker volume create --label io.pkuba.environment=production "$volume" >/dev/null
done

cat >/etc/pkuba-deploy.conf <<EOF
PKUBA_DEPLOY_ROOT=$deploy_root
PKUBA_DEPLOY_STATE_DIR=$state_dir
PKUBA_REPOSITORY_DIR=$repository_dir
PKUBA_RELEASE_TOOLSET_ROOT=/usr/local/libexec/pkuba/toolsets
PKUBA_PRODUCTION_TOOLSET_CURRENT=/usr/local/libexec/pkuba/toolsets/current
PKUBA_DEPLOY_LOCK_HELPER=/usr/local/libexec/pkuba/toolsets/current/libexec/acquire-deploy-lock.py
PKUBA_RELEASE_RECOVERY_COMMAND=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-recover-release-transaction
PKUBA_PAIRED_RESTORE_COMMAND=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-restore-paired-data
PKUBA_RELEASE_IDENTITY_VALIDATOR=/usr/local/libexec/pkuba/toolsets/current/libexec/validate-release-identity.sh
PKUBA_PAIRED_BACKUP_VERIFIER=/usr/local/libexec/pkuba/toolsets/current/libexec/verify-paired-backup.py
PKUBA_WRITER_FENCE_COMMAND=/usr/local/libexec/pkuba/toolsets/current/libexec/fence-deploy-writers.sh
PKUBA_APPLICATION_START_COMMAND=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-start-current-application
PKUBA_RUNTIME_DIR=$runtime_dir
PKUBA_ENV_FILE=$env_file
PKUBA_RUNTIME_NETWORK=$runtime_network
PKUBA_DATA_PROJECT=$data_project
PKUBA_GATEWAY_PROJECT=$gateway_project
PKUBA_POSTGRES_VOLUME=$postgres_volume
PKUBA_MEDIA_VOLUME=$media_volume
PKUBA_ARCHIVE_VOLUME=$archive_volume
PKUBA_CADDY_DATA_VOLUME=$caddy_data_volume
PKUBA_CADDY_CONFIG_VOLUME=$caddy_config_volume
PKUBA_POSTGRES_IMAGE=$postgres_image
PKUBA_CADDY_IMAGE=$caddy_image
PKUBA_DEPLOY_PREFLIGHT_WAIT_SECONDS=900
PKUBA_DEPLOY_STARTUP_HEADROOM_BYTES=16106127360
PKUBA_DEPLOY_HARD_FLOOR_BYTES=10737418240
PKUBA_ENFORCE_DATA_GATE=1
PKUBA_ENABLE_EMAIL_PROFILE=0
PKUBA_PRODUCTION_AUTOMATION_ARMED=0
EOF
chmod 600 /etc/pkuba-deploy.conf

maintenance_file=$state_dir/maintenance.enabled
bootstrap_complete=0
on_exit() {
  local status=$?
  trap - EXIT
  if [[ $bootstrap_complete != 1 ]]; then
    touch "$maintenance_file" 2>/dev/null || true
    sync -f "$state_dir" 2>/dev/null || true
    PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=0 \
      bash /usr/local/libexec/pkuba/toolsets/current/libexec/fence-deploy-writers.sh >/dev/null 2>&1 || true
    echo "Fresh production bootstrap failed; maintenance remains enabled and application writers are fenced." >&2
    (( status != 0 )) || status=1
  fi
  exit "$status"
}
trap on_exit EXIT
touch "$maintenance_file"
sync -f "$state_dir"

compose_data=(env PKUBA_ENV_FILE="$env_file" PKUBA_POSTGRES_IMAGE="$postgres_image"
  PKUBA_POSTGRES_VOLUME="$postgres_volume" PKUBA_RUNTIME_NETWORK="$runtime_network"
  docker compose --project-name "$data_project" --project-directory "$release_dir"
  --env-file "$env_file" -f "$release_dir/infra/compose.prod.data.yml")
"${compose_data[@]}" up -d db
db_container=$("${compose_data[@]}" ps -q db)
for _ in $(seq 1 60); do
  [[ $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$db_container") == healthy ]] && break
  sleep 2
done
[[ $(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$db_container") == healthy ]] \
  || die "fresh PostgreSQL did not become healthy"

slot_env=(PKUBA_SLOT_NAME=pkuba-blue PKUBA_SLOT_API_PORT=18000 PKUBA_SLOT_WEB_PORT=18080
  PKUBA_API_IMAGE="$api_image" PKUBA_WEB_IMAGE="$web_image" PKUBA_RELEASE_TAG="$release_tag"
  PKUBA_GIT_COMMIT="$release_commit" PKUBA_ENV_FILE="$env_file"
  PKUBA_MEDIA_VOLUME="$media_volume" PKUBA_ARCHIVE_VOLUME="$archive_volume"
  PKUBA_RUNTIME_NETWORK="$runtime_network")
compose_slot=(env "${slot_env[@]}" docker compose --project-name pkuba-blue
  --project-directory "$release_dir" --env-file "$env_file"
  -f "$release_dir/infra/compose.prod.slot.yml")
"${compose_slot[@]}" run --rm --no-deps api python manage.py migrate --noinput
"${compose_slot[@]}" run --rm --no-deps api python manage.py check --deploy
"${compose_slot[@]}" run --rm --no-deps api python manage.py check_no_synthetic_public_data
echo "Create the one initial SUPERADMIN. Input is read from this terminal and is not logged."
"${compose_slot[@]}" run --rm --no-deps api python manage.py bootstrap_first_superadmin
echo "Initialize the global administrator registration invite. Input is not echoed or logged."
"${compose_slot[@]}" run --rm --no-deps api python manage.py bootstrap_admin_registration_policy
"${compose_slot[@]}" run --rm --no-deps api python manage.py shell -c \
  'from core.models import Account,AdminAuditLog,AdminRegistrationPolicy,Season; assert Season.objects.count()==0; assert Account.objects.filter(role="SUPERADMIN",is_active=True).count()==1; assert AdminRegistrationPolicy.objects.filter(singleton_key=1).exclude(invite_code_hash="").count()==1; assert AdminAuditLog.objects.filter(action="FIRST_SUPERADMIN_BOOTSTRAPPED").count()==1; assert AdminAuditLog.objects.filter(action="ADMIN_REGISTRATION_POLICY_BOOTSTRAPPED").count()==1'

app_capability=$(bash "$release_dir/scripts/prod/derive-release-capability.sh" "$release_dir")
switched_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat >"$state_dir/current.env" <<EOF
ACTIVE_SLOT=blue
CURRENT_TAG=$release_tag
CURRENT_COMMIT=$release_commit
CURRENT_API_IMAGE=$api_image
CURRENT_WEB_IMAGE=$web_image
CURRENT_RELEASE_DIR=$release_dir
CURRENT_APP_CAPABILITY=$app_capability
SWITCHED_AT=$switched_at
EOF
cat >"$state_dir/upstreams.caddy" <<'EOF'
(active_api) {
	reverse_proxy pkuba-blue-api:8000
}

(active_web) {
	reverse_proxy pkuba-blue-web:8080
}
EOF
chmod 600 "$state_dir/current.env"
chmod 644 "$state_dir/upstreams.caddy"
sync -f "$state_dir/current.env" "$state_dir/upstreams.caddy" "$state_dir"

compose_gateway=(env PKUBA_DEPLOY_STATE_DIR="$state_dir" PKUBA_CADDY_IMAGE="$caddy_image"
  PKUBA_RUNTIME_NETWORK="$runtime_network" PKUBA_CADDY_DATA_VOLUME="$caddy_data_volume"
  PKUBA_CADDY_CONFIG_VOLUME="$caddy_config_volume" docker compose
  --project-name "$gateway_project" --project-directory "$release_dir" --env-file "$env_file"
  -f "$release_dir/infra/compose.prod.gateway.yml")
"${compose_gateway[@]}" up -d gateway

ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

cat >/etc/systemd/system/pkuba-release-recovery.service <<'EOF'
[Unit]
Description=Recover an interrupted PKUBA application release transaction
After=docker.service network-online.target
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-recover-release-transaction

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/pkuba-application-start.service <<'EOF'
[Unit]
Description=Start the authoritative PKUBA application slot after recovery
After=pkuba-release-recovery.service
Requires=pkuba-release-recovery.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-start-current-application
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/pkuba-backup@.service <<'EOF'
[Unit]
Description=Create a consistent PKUBA %i backup
After=pkuba-application-start.service
Requires=pkuba-application-start.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-backup-current %i
EOF
cat >/etc/systemd/system/pkuba-backup-daily.timer <<'EOF'
[Unit]
Description=Daily PKUBA consistent backup

[Timer]
OnCalendar=*-*-* 03:15:00 UTC
Persistent=true
Unit=pkuba-backup@daily.service

[Install]
WantedBy=timers.target
EOF
cat >/etc/systemd/system/pkuba-backup-weekly.timer <<'EOF'
[Unit]
Description=Weekly PKUBA consistent backup

[Timer]
OnCalendar=Sun *-*-* 04:15:00 UTC
Persistent=true
Unit=pkuba-backup@weekly.service

[Install]
WantedBy=timers.target
EOF
chmod 644 /etc/systemd/system/pkuba-*.service /etc/systemd/system/pkuba-*.timer
systemctl daemon-reload
systemctl enable pkuba-release-recovery.service pkuba-application-start.service \
  pkuba-backup-daily.timer pkuba-backup-weekly.timer >/dev/null

PKUBA_DEPLOY_LOCK_HELD=1 PKUBA_START_UNDER_MAINTENANCE=1 \
  bash /usr/local/libexec/pkuba/toolsets/current/sbin/pkuba-start-current-application
curl --fail --silent --show-error -H 'Host: api' -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:18000/api/v1/health/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:18080/_deployment/ready >/dev/null
domain=$(sed -n 's/^PKUBA_DOMAIN=//p' "$env_file")
[[ $domain =~ ^([a-z0-9-]+\.)+[a-z]{2,}$ ]] || die "invalid PKUBA_DOMAIN in server environment"
api_headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null \
  --connect-timeout 10 --max-time 30 "https://api.$domain/api/v1/health/ready")
admin_headers=$(curl --fail --silent --show-error --dump-header - --output /dev/null \
  --connect-timeout 10 --max-time 30 "https://admin.$domain/_deployment/ready")
grep -iq '^strict-transport-security:.*max-age=31536000' <<<"$api_headers" \
  || die "public API does not expose the required HSTS header"
grep -iq '^strict-transport-security:.*max-age=31536000' <<<"$admin_headers" \
  || die "public admin entry does not expose the required HSTS header"
root_headers=$(curl --silent --show-error --dump-header - --output /dev/null \
  --connect-timeout 10 --max-time 30 "https://$domain/")
grep -Eq '^HTTP/[0-9.]+ 301' <<<"$root_headers" \
  || die "root domain does not return a permanent redirect"
grep -iq "^location: https://admin\.$domain/" <<<"$root_headers" \
  || die "root domain does not redirect to the admin entry"
rm -f "$maintenance_file"
sync -f "$state_dir"
bootstrap_complete=1
trap - EXIT

cat <<EOF
Fresh PKUBA production baseline is ready under $production_root.
Release: $release_tag ($release_commit), active slot: blue.
Only ports 22, 80 and 443 are allowed. The deployment account is restricted to
its forced command, but password authentication is intentionally unchanged.
From the trusted management machine, run verify-deploy-ssh.sh with the Actions
private key, then run the exact root-only finalizer command that it prints while
keeping this root recovery session open. Daily/weekly backups keep 5/4 consistent
restore points. Keep PKUBA_PRODUCTION_AUTOMATION_ARMED=0 until SSH finalization,
separate deployment authorization and the blue/green rehearsal are complete.
EOF
