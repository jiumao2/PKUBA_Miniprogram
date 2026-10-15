#!/usr/bin/env bash
set -euo pipefail

domain=${1:-}
mode=${2:---required}
[[ $# -ge 1 && $# -le 2 && $domain =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ \
  && ( $mode == --required || $mode == --if-advertised ) ]] || {
  echo 'usage: probe-gateway-redirects.sh DOMAIN [--required|--if-advertised]' >&2
  exit 2
}

# No -k or redirect following: validate the public certificate and first hop.
curl_options=(--fail --silent --show-error --proto '=https' \
  --connect-timeout 5 --max-time 10)
# Caddy's config healthcheck does not wait for initial ACME issuance. Share one
# wall-clock budget across readiness and both redirects, including curl retries.
deadline=$((SECONDS + 120))
request() {
  if [[ $mode == --if-advertised ]]; then
    curl -q "${curl_options[@]}" --retry 2 --retry-delay 1 \
      --retry-all-errors --retry-max-time 30 "$@"
    return
  fi
  local remaining=$((deadline - SECONDS)) code
  if ((remaining <= 0)); then
    echo 'Gateway HTTPS readiness exceeded the 120-second deployment budget.' >&2
    return 124
  fi
  if timeout --foreground "${remaining}s" curl -q "${curl_options[@]}" \
    --retry 60 --retry-delay 2 --retry-all-errors --retry-max-time 120 "$@"; then
    return 0
  else
    code=$?
    if [[ $code == 124 ]]; then
      echo 'Gateway HTTPS readiness exceeded the 120-second deployment budget.' >&2
    fi
    return "$code"
  fi
}
headers=$(request --dump-header - --output /dev/null \
  "https://admin.$domain/_deployment/ready")
status=$(awk '/^HTTP\// {code=$2; sub(/\r$/, "", code)} END {print code}' <<<"$headers")
[[ $status == 200 ]] || { echo 'Gateway readiness did not return HTTP 200.' >&2; exit 1; }
capability=$(awk -F ': *' 'tolower($1) == "x-pkuba-gateway-redirects" {sub(/\r$/, "", $2); print $2}' <<<"$headers")
if [[ -z $capability && $mode == --if-advertised ]]; then
  echo 'Legacy gateway: www redirect probe not enabled by the deployed version.'
  exit 0
fi
[[ $capability == 'root,www' ]] || {
  echo 'Gateway redirect capability missing or unsupported.' >&2
  exit 1
}

path='/_deployment/redirect-check?probe=1'
for host in "$domain" "www.$domain"; do
  result=$(request --output /dev/null \
    --write-out '%{http_code} %{redirect_url}' "https://$host$path")
  [[ $result == "301 https://admin.$domain$path" ]] || {
    echo "HTTPS redirect contract failed for $host (expected 301 preserving path/query)." >&2
    exit 1
  }
done
echo 'Public root and www HTTPS redirects verified.'
