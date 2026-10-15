#!/usr/bin/env bash
set -euo pipefail

# Local-only HTTPS regression: no production names, volumes, credentials or ACME.
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
fixture=$(mktemp -d)
project="pkuba-gateway-test-$(basename "$fixture" | tr '[:upper:].' '[:lower:]-')"
export PKUBA_DOMAIN=example.test ACME_EMAIL=admin@example.test
export PKUBA_CADDY_IMAGE=caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
export PKUBA_DEPLOY_STATE_DIR=$fixture/state
export PKUBA_RUNTIME_NETWORK=$project
export PKUBA_CADDY_DATA_VOLUME=$project-data
export PKUBA_CADDY_CONFIG_VOLUME=$project-config

compose() {
  local release=$1
  shift
  docker compose --project-name "$project" --project-directory "$fixture/$release" \
    -f "$fixture/$release/infra/compose.prod.gateway.yml" "$@"
}

cleanup() {
  docker rm -f "$project" >/dev/null 2>&1 || true
  docker network rm "$project" >/dev/null 2>&1 || true
  docker volume rm "$PKUBA_CADDY_DATA_VOLUME" "$PKUBA_CADDY_CONFIG_VOLUME" \
    >/dev/null 2>&1 || true
  rm -rf -- "$fixture"
}
trap cleanup EXIT
docker image inspect "$PKUBA_CADDY_IMAGE" >/dev/null
mkdir -p "$fixture/state"
cat >"$fixture/state/upstreams.caddy" <<'EOF'
(active_api) {
  respond "API" 200
}
(active_web) {
  respond "WEB" 200
}
EOF
for release in old new invalid; do
  mkdir -p "$fixture/$release/infra"
  # Retain the production mount/healthcheck contract; isolate only names/ports.
  sed -e "s/container_name: pkuba-gateway/container_name: $project/" \
    -e 's/"80:80"/"127.0.0.1::80"/' \
    -e 's/"443:443"/"127.0.0.1::443"/' \
    -e '\|"443:443/udp"|d' \
    -e 's/interval: 15s/interval: 1s/' \
    "$repo_root/infra/compose.prod.gateway.yml" \
    >"$fixture/$release/infra/compose.prod.gateway.yml"
  sed '1a\
  local_certs\
  skip_install_trust' "$repo_root/infra/Caddyfile.gateway" \
    >"$fixture/$release/infra/Caddyfile.gateway"
done
sed -i 's/, www\.{$PKUBA_DOMAIN}//' "$fixture/old/infra/Caddyfile.gateway"
sed -i 's/encode zstd gzip/invalid_directive/' "$fixture/invalid/infra/Caddyfile.gateway"
docker network create "$project" >/dev/null

activate() {
  compose "$1" run --rm --no-deps gateway caddy validate --config /etc/caddy/Caddyfile
  compose "$1" up -d --no-deps --wait --wait-timeout 60 gateway
  compose "$1" exec -T gateway caddy reload --config /etc/caddy/Caddyfile
  local mounted
  mounted=$(docker inspect --format \
    '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}' "$project")
  [[ $mounted == "$fixture/$1/infra/Caddyfile.gateway" ]]
}

request() {
  local host=$1 path=${2:-/} port
  port=$(docker port "$project" 443/tcp | awk -F: 'NR == 1 {print $NF}')
  curl --silent --show-error --noproxy '*' --max-time 10 \
    --cacert "$fixture/root.crt" --connect-to "$host:443:127.0.0.1:$port" \
    --dump-header "$fixture/headers" --output "$fixture/body" \
    --write-out '%{http_code}' "https://$host$path"
}

assert_redirects() {
  local host
  for host in example.test www.example.test; do
    [[ $(request "$host" '/check/path?range=all') == 301 ]]
    tr -d '\r' <"$fixture/headers" \
      | grep -Fxqi 'Location: https://admin.example.test/check/path?range=all'
    grep -Eiq '^strict-transport-security: max-age=31536000; includeSubDomains' "$fixture/headers"
  done
  [[ $(request admin.example.test) == 200 ]]
  [[ $(request api.example.test /api/v1/health/live) == 200 ]]
  [[ $(request api.example.test /not-api) == 404 ]]
}

assert_www_unavailable() {
  if request www.example.test >/dev/null 2>&1; then
    echo 'old gateway unexpectedly accepted www HTTPS' >&2
    exit 1
  fi
}

activate old
docker cp "$project:/data/caddy/pki/authorities/local/root.crt" "$fixture/root.crt"
[[ $(request example.test) == 301 ]]
assert_www_unavailable
activate new
assert_redirects

# Invalid candidate validation leaves the running, valid gateway untouched.
if compose invalid run --rm --no-deps gateway caddy validate \
  --config /etc/caddy/Caddyfile >"$fixture/invalid.log" 2>&1; then
  cat "$fixture/invalid.log" >&2
  echo 'invalid gateway configuration unexpectedly passed validation' >&2
  exit 1
fi
assert_redirects

# Recreating from the previous release restores its config, not just upstreams.
activate old
[[ $(request example.test) == 301 ]]
assert_www_unavailable
activate new
docker restart "$project" >/dev/null
compose new up -d --no-deps --wait --wait-timeout 60 gateway
assert_redirects
echo 'Gateway HTTPS, www redirect, config replacement, rollback and restart checks passed.'
