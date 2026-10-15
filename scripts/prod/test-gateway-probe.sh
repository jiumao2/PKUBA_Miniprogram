#!/usr/bin/env bash
set -euo pipefail

# Local-only contract tests. The fake curl never opens a network connection.
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir "$fixture/bin"
cat >"$fixture/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == -q && " $* " == *' --proto =https '* \
  && " $* " == *' --max-time 10 '* && " $* " != *' --location '* \
  && " $* " != *' --insecure '* && " $* " != *' -k '* ]]
url=${!#}
printf '%s\n' "$url" >>"$PROBE_TEST_LOG"
if [[ $url == */_deployment/ready ]]; then
  case "$PROBE_TEST_CASE" in
    legacy) printf 'HTTP/2 200\r\n\r\n' ;;
    unknown) printf 'HTTP/2 200\r\nX-PKUBA-Gateway-Redirects: future\r\n\r\n' ;;
    ready-error) exit 22 ;;
    ready-redirect) printf 'HTTP/2 301\r\nLocation: https://other.example.test/\r\n\r\n' ;;
    missing-status) printf '\r\n' ;;
    *) printf 'HTTP/2 200\r\nx-pkuba-gateway-redirects: root,www\r\n\r\n' ;;
  esac
  exit 0
fi
if [[ $url == https://www.* ]]; then
  case "$PROBE_TEST_CASE" in
    dns) exit 6 ;;
    tls) exit 60 ;;
    wrong-code) printf '302 https://admin.example.test/_deployment/redirect-check?probe=1'; exit 0 ;;
    wrong-target) printf '301 https://other.example.test/_deployment/redirect-check?probe=1'; exit 0 ;;
    missing-query) printf '301 https://admin.example.test/_deployment/redirect-check'; exit 0 ;;
    downgrade) printf '301 http://admin.example.test/_deployment/redirect-check?probe=1'; exit 0 ;;
  esac
fi
printf '301 https://admin.example.test/_deployment/redirect-check?probe=1'
EOF
chmod +x "$fixture/bin/curl"
export PATH="$fixture/bin:$PATH" PROBE_TEST_LOG="$fixture/requests"

for mode in --required --if-advertised; do
  PROBE_TEST_CASE=valid bash "$script_dir/probe-gateway-redirects.sh" example.test "$mode"
  for scenario in unknown ready-error ready-redirect missing-status dns tls wrong-code wrong-target missing-query downgrade; do
    if PROBE_TEST_CASE=$scenario bash "$script_dir/probe-gateway-redirects.sh" \
      example.test "$mode" >"$fixture/result" 2>&1; then
      echo "probe unexpectedly accepted $scenario ($mode)" >&2
      exit 1
    fi
  done
done
: >"$PROBE_TEST_LOG"
PROBE_TEST_CASE=legacy bash "$script_dir/probe-gateway-redirects.sh" example.test --if-advertised
[[ $(wc -l <"$PROBE_TEST_LOG") -eq 1 ]]
if PROBE_TEST_CASE=legacy bash "$script_dir/probe-gateway-redirects.sh" example.test \
  >"$fixture/result" 2>&1; then
  echo 'deployment incorrectly skipped the required capability' >&2
  exit 1
fi
echo '24 gateway probe scenarios passed (including legacy no-www-request).'
