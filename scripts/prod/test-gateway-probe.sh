#!/usr/bin/env bash
set -euo pipefail

# Local-only contract and loopback TLS tests; no public network or ACME.
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
mkdir "$fixture/bin"
cat >"$fixture/bin/timeout" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == --foreground && $2 =~ ^[0-9]+s$ ]]
remaining=${2%s}
((remaining > 0 && remaining <= 120))
printf '%s\n' "$remaining" >>"$PROBE_TEST_BUDGETS"
shift 2
if [[ $PROBE_TEST_CASE == deadline && ${!#} == https://www.* ]]; then
  exit 124
fi
exec "$@"
EOF
cat >"$fixture/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == -q && " $* " == *' --proto =https '* \
  && " $* " == *' --max-time 10 '* && " $* " != *' --location '* \
  && " $* " != *' --insecure '* && " $* " != *' -k '* ]]
if [[ $PROBE_TEST_MODE == --required ]]; then
  [[ " $* " == *' --retry 60 --retry-delay 2 --retry-all-errors --retry-max-time 120 '* ]]
else
  [[ " $* " == *' --retry 2 --retry-delay 1 --retry-all-errors --retry-max-time 30 '* ]]
fi
url=${!#}
printf '%s\n' "$url" >>"$PROBE_TEST_LOG"
if [[ $url == */_deployment/ready ]]; then
  if [[ $PROBE_TEST_CASE == shared-budget ]]; then sleep 1; fi
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
chmod +x "$fixture/bin/curl" "$fixture/bin/timeout"
real_curl=$(command -v curl)
real_timeout=$(command -v timeout)
export PATH="$fixture/bin:$PATH" PROBE_TEST_LOG="$fixture/requests" \
  PROBE_TEST_BUDGETS="$fixture/budgets"

for mode in --required --if-advertised; do
  export PROBE_TEST_MODE=$mode
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
if PROBE_TEST_MODE=--required PROBE_TEST_CASE=legacy bash "$script_dir/probe-gateway-redirects.sh" example.test \
  >"$fixture/result" 2>&1; then
  echo 'deployment incorrectly skipped the required capability' >&2
  exit 1
fi
export PROBE_TEST_MODE=--required
if PROBE_TEST_CASE=deadline bash "$script_dir/probe-gateway-redirects.sh" example.test \
  >"$fixture/result" 2>&1; then
  echo 'deployment incorrectly accepted an exhausted TLS budget' >&2
  exit 1
else
  [[ $? == 124 ]]
  grep -q '120-second deployment budget' "$fixture/result"
fi
: >"$PROBE_TEST_BUDGETS"
PROBE_TEST_CASE=shared-budget bash "$script_dir/probe-gateway-redirects.sh" example.test
mapfile -t budgets <"$PROBE_TEST_BUDGETS"
[[ ${#budgets[@]} == 3 ]]
((budgets[1] < budgets[0] && budgets[2] <= budgets[1]))

# Real curl against an isolated TLS server: reproduce three internal-error
# alerts before a certificate becomes available. No ACME or public network.
PROBE_REAL_CURL=$real_curl PROBE_REAL_TIMEOUT=$real_timeout \
python3 - "$script_dir" "$fixture" <<'PY'
import http.server
import os
import pathlib
import ssl
import subprocess
import sys
import threading

script_dir, fixture = map(pathlib.Path, sys.argv[1:])
cert, key = fixture / "cert.pem", fixture / "key.pem"
subprocess.run([
    "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=example.test", "-addext",
    "subjectAltName=DNS:example.test,DNS:admin.example.test,DNS:www.example.test",
    "-keyout", str(key), "-out", str(cert),
], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/_deployment/ready":
            self.send_response(200)
            self.send_header("X-PKUBA-Gateway-Redirects", "root,www")
        else:
            self.send_response(301)
            self.send_header("Location", "https://admin.example.test" + self.path)
        self.end_headers()

    def log_message(self, *_):
        pass


attempts = 0


def delayed_certificate(sock, name, context):
    global attempts
    if name == "www.example.test":
        attempts += 1
        if attempts <= 3:
            return ssl.ALERT_DESCRIPTION_INTERNAL_ERROR


context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(cert, key)
context.set_servername_callback(delayed_certificate)
server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
server.socket = context.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
(fixture / "bin/curl").write_text(
    '#!/usr/bin/env bash\nexec "$PROBE_REAL_CURL" -q --noproxy "*" '
    '--cacert "$PROBE_TLS_CERT" --connect-to "::$PROBE_TLS_ADDRESS" "$@"\n'
)
(fixture / "bin/timeout").write_text(
    '#!/usr/bin/env bash\nexec "$PROBE_REAL_TIMEOUT" "$@"\n'
)
env = dict(os.environ, PROBE_TLS_CERT=str(cert),
           PROBE_TLS_ADDRESS=f"127.0.0.1:{server.server_port}")
try:
    for mode, expected_attempts, succeeds in [
        ("--required", 4, True), ("--if-advertised", 3, False),
    ]:
        attempts = 0
        result = subprocess.run(
            ["bash", str(script_dir / "probe-gateway-redirects.sh"), "example.test", mode],
            env=env, capture_output=True, text=True, timeout=20,
        )
        assert (result.returncode == 0) == succeeds, result.stderr
        assert attempts == expected_attempts, (mode, attempts, result.stderr)
finally:
    server.shutdown()
    server.server_close()
print("Real TLS delayed-readiness regression passed; monitoring retries unchanged.")
PY

echo '28 gateway probe scenarios passed (including shared budget and real delayed TLS).'
