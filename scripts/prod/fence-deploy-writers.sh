#!/usr/bin/env bash
set -Eeuo pipefail

die() {
  echo "writer fence error: $*" >&2
  exit 1
}

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "this command must run as root"
[[ $# -eq 0 ]] || die "usage: fence-deploy-writers.sh"
command -v docker >/dev/null 2>&1 || die "missing command: docker"

services=(api expiry scoresheet-worker archive-worker outbox)

list_running_containers() {
  local slot=$1 service=$2 output
  if ! output=$(docker ps -q \
    --filter "label=com.docker.compose.project=pkuba-$slot" \
    --filter "label=com.docker.compose.service=$service"); then
    die "could not enumerate writers: pkuba-$slot/$service"
  fi
  printf '%s\n' "$output"
}

for slot in blue green; do
  for service in "${services[@]}"; do
    containers=$(list_running_containers "$slot" "$service")
    while IFS= read -r container; do
      [[ -n $container ]] || continue
      docker stop --time 60 "$container" >/dev/null \
        || die "could not stop writer: $container"
      if ! running=$(docker inspect --format '{{.State.Running}}' "$container"); then
        die "could not inspect stopped writer: $container"
      fi
      [[ $running == false ]] || die "writer is still running after stop: $container"
    done <<<"$containers"
  done
done

for slot in blue green; do
  for service in "${services[@]}"; do
    containers=$(list_running_containers "$slot" "$service")
    [[ -z $containers ]] || die "writer remains active: pkuba-$slot/$service"
  done
done
