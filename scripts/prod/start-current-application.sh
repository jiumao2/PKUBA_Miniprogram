#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
die() { echo "application start error: $*" >&2; exit 1; }

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 0 ]] || die "usage: start-current-application.sh"
config_file=${PKUBA_DEPLOY_CONFIG:-/etc/pkuba-deploy.conf}
if [[ -r $config_file ]]; then
  # shellcheck disable=SC1090
  source "$config_file"
fi
deploy_root=${PKUBA_DEPLOY_ROOT:-/opt/pkuba/production/deploy}
repository_dir=${PKUBA_REPOSITORY_DIR:-/opt/pkuba/production/repository}
env_file=${PKUBA_ENV_FILE:-/opt/pkuba/production/.env}
state_dir=${PKUBA_DEPLOY_STATE_DIR:-$deploy_root/state}
release_root=$deploy_root/releases
current_state=$state_dir/current.env
runtime_network=${PKUBA_RUNTIME_NETWORK:-pkuba-prod-runtime}
media_volume=${PKUBA_MEDIA_VOLUME:-pkuba-prod-media}
archive_volume=${PKUBA_ARCHIVE_VOLUME:-pkuba-prod-archives}
blue_api_port=${PKUBA_BLUE_API_PORT:-18000}
blue_web_port=${PKUBA_BLUE_WEB_PORT:-18080}
green_api_port=${PKUBA_GREEN_API_PORT:-18001}
green_web_port=${PKUBA_GREEN_WEB_PORT:-18081}
email_profile=${PKUBA_ENABLE_EMAIL_PROFILE:-0}
stability_seconds=${PKUBA_START_STABILITY_SECONDS:-15}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

[[ -d $state_dir ]] || die "missing pre-created deployment state directory"
lock_helper=${PKUBA_DEPLOY_LOCK_HELPER:-/usr/local/libexec/pkuba/acquire-deploy-lock.py}
[[ -f $lock_helper ]] || lock_helper=$script_dir/acquire-deploy-lock.py
if [[ ${PKUBA_DEPLOY_LOCK_HELD:-0} != 1 ]]; then
  exec env PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR=${PKUBA_TEST_ALLOW_NON_ROOT_STATE_DIR:-0} \
    python3 "$lock_helper" --state-dir "$state_dir" --timeout 1800 -- bash "$0"
fi
[[ ! -e $state_dir/release-transaction \
  && ! -e $state_dir/release-transaction-completed \
  && ! -e $state_dir/paired-restore-transaction \
  && ! -e $state_dir/paired-restore-completed ]] \
  || die "an unfinished transaction must recover before writers start"
[[ ! -e $state_dir/maintenance.enabled \
  || ${PKUBA_START_UNDER_MAINTENANCE:-0} == 1 ]] \
  || die "maintenance is enabled; writers remain fenced"
[[ -f $current_state ]] || die "missing current release state"

identity_validator=${PKUBA_RELEASE_IDENTITY_VALIDATOR:-/usr/local/libexec/pkuba/validate-release-identity.sh}
[[ -x $identity_validator ]] || identity_validator=$script_dir/validate-release-identity.sh
parsed=$(bash "$identity_validator" "$current_state" "$release_root" "$repository_dir") \
  || die "current release identity is invalid"
IFS=$'\t' read -r SLOT TAG COMMIT API_IMAGE WEB_IMAGE RELEASE_DIR _ _ <<<"$parsed"

writer_fence=${PKUBA_WRITER_FENCE_COMMAND:-/usr/local/libexec/pkuba/fence-deploy-writers.sh}
[[ -x $writer_fence ]] || writer_fence=$script_dir/fence-deploy-writers.sh
bash "$writer_fence" || die "could not establish the two-slot writer fence"
on_failure() {
  local status=$?
  trap - EXIT
  set +e
  bash "$writer_fence" >/dev/null 2>&1
  echo "application start failed; both slots' writers were fenced again" >&2
  (( status != 0 )) || status=1
  exit "$status"
}
trap on_failure EXIT

api_port=$blue_api_port
web_port=$blue_web_port
[[ $SLOT == green ]] && { api_port=$green_api_port; web_port=$green_web_port; }
profiles=()
[[ $email_profile == 1 ]] && profiles=(--profile email)
compose=(
  env PKUBA_SLOT_NAME="pkuba-$SLOT" PKUBA_SLOT_API_PORT="$api_port"
  PKUBA_SLOT_WEB_PORT="$web_port" PKUBA_API_IMAGE="$API_IMAGE"
  PKUBA_WEB_IMAGE="$WEB_IMAGE" PKUBA_RELEASE_TAG="$TAG" PKUBA_GIT_COMMIT="$COMMIT"
  PKUBA_ENV_FILE="$env_file" PKUBA_MEDIA_VOLUME="$media_volume"
  PKUBA_ARCHIVE_VOLUME="$archive_volume" PKUBA_RUNTIME_NETWORK="$runtime_network"
  docker compose --project-name "pkuba-$SLOT" --project-directory "$RELEASE_DIR"
  --env-file "$env_file" -f "$RELEASE_DIR/infra/compose.prod.slot.yml"
  "${profiles[@]}"
)
services=(web expiry scoresheet-worker archive-worker)
[[ $email_profile == 1 ]] && services+=(outbox)
"${compose[@]}" up -d --no-deps "${services[@]}" api

wait_internal_ready() {
  local api_body= web_body=
  for _ in $(seq 1 60); do
    api_body=$(curl --silent --show-error \
      -H 'Host: api' -H 'X-Forwarded-Proto: https' \
      "http://127.0.0.1:$api_port/api/v1/health/ready" || true)
    web_body=$(curl --silent --show-error \
      "http://127.0.0.1:$web_port/_deployment/ready" || true)
    if [[ $api_body == *"$TAG"* && $api_body == *"$COMMIT"* \
      && $web_body == *"$TAG"* && $web_body == *"$COMMIT"* \
      && ( $api_body == *'"status":"ok"'* || $api_body == *'"status": "ok"'* ) \
      && ( $web_body == *'"status":"ok"'* || $web_body == *'"status": "ok"'* ) ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

assert_services_stable() {
  local service container before after
  local required=(api web "${services[@]:1}")
  declare -A restart_counts=()
  for service in "${required[@]}"; do
    container=$("${compose[@]}" ps -q "$service")
    [[ -n $container ]] || die "started service has no container: $service"
    [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]] \
      || die "started service is not running: $service"
    before=$(docker inspect --format '{{.RestartCount}}' "$container")
    restart_counts[$service]=$before
  done
  sleep "$stability_seconds"
  for service in "${required[@]}"; do
    container=$("${compose[@]}" ps -q "$service")
    [[ -n $container ]] || die "started service disappeared: $service"
    after=$(docker inspect --format '{{.RestartCount}}' "$container")
    [[ $after -eq ${restart_counts[$service]} ]] \
      || die "started service restarted during stability check: $service"
  done
}

wait_internal_ready || die "current application did not become ready"
assert_services_stable
trap - EXIT
echo "PKUBA_APPLICATION_START_RESULT=success"
echo "PKUBA_ACTIVE_TAG=$TAG"
