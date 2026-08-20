#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker.io docker-compose-v2 ca-certificates curl
fi

if ! docker buildx version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker-buildx
fi

if [[ -n "${PKUBA_DOCKER_PROXY_PORT:-}" ]]; then
  windows_gateway="$(ip route show default | awk 'NR == 1 {print $3}')"
  if [[ -z "$windows_gateway" ]]; then
    echo "Unable to determine the Windows gateway for Docker proxying." >&2
    exit 1
  fi
  install -d -m 0755 /etc/systemd/system/docker.service.d
  printf '%s\n' \
    '[Service]' \
    "Environment=\"HTTP_PROXY=http://${windows_gateway}:${PKUBA_DOCKER_PROXY_PORT}\"" \
    "Environment=\"HTTPS_PROXY=http://${windows_gateway}:${PKUBA_DOCKER_PROXY_PORT}\"" \
    'Environment="NO_PROXY=localhost,127.0.0.1,::1,db,api,web,mailpit"' \
    > /etc/systemd/system/docker.service.d/pkuba-proxy.conf
  systemctl daemon-reload
fi

systemctl enable --now docker
systemctl restart docker
docker version >/dev/null
docker compose version
