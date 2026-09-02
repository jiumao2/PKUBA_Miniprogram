#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/../.." && pwd)
caddy_image=caddy@sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
postgres_image=postgres:17-alpine
fixture=$(mktemp -d)
suffix=$$
network="pkuba-slot-proto-$suffix"
db="pkuba-slot-proto-db-$suffix"
api="pkuba-slot-proto-api-$suffix"
slot="pkuba-slot-proto-web-$suffix"
api_image="pkuba-slot-proto-api:$suffix"
database_url="postgresql://pkuba:pkuba@$db:5432/pkuba"

cleanup() {
  docker rm -f "$slot" "$api" "$db" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm -f "$api_image" >/dev/null 2>&1 || true
  rm -rf -- "$fixture"
}
trap cleanup EXIT

if ! docker build --quiet --target production --tag "$api_image" \
  "$repo_root/apps/api" >"$fixture/api-build.log"; then
  cat "$fixture/api-build.log" >&2
  exit 1
fi

docker network create "$network" >/dev/null
docker run --rm -d \
  --name "$db" \
  --network "$network" \
  --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_DB=pkuba \
  -e POSTGRES_USER=pkuba \
  -e POSTGRES_PASSWORD=pkuba \
  "$postgres_image" >/dev/null

db_ready=0
for _ in $(seq 1 45); do
  if docker exec "$db" pg_isready -U pkuba -d pkuba >/dev/null 2>&1; then
    db_ready=1
    break
  fi
  sleep 1
done
[[ $db_ready == 1 ]] || {
  echo "slot forwarded-proto test database did not become ready" >&2
  docker logs "$db" >&2 2>/dev/null || true
  exit 1
}

docker run --rm \
  --network "$network" \
  -e DATABASE_URL="$database_url" \
  -e DJANGO_SECRET_KEY=slot-forwarded-proto-regression-only \
  "$api_image" python manage.py migrate --noinput >/dev/null

docker run --rm -d \
  --name "$api" \
  --network "$network" \
  -e DATABASE_URL="$database_url" \
  -e DJANGO_SECRET_KEY=slot-forwarded-proto-regression-only \
  -e DJANGO_DEBUG=0 \
  -e DJANGO_ALLOWED_HOSTS=admin.example.test \
  -e DJANGO_SECURE_COOKIES=1 \
  -e DJANGO_SECURE_SSL_REDIRECT=1 \
  -e DJANGO_HSTS_SECONDS=3600 \
  -e PKUBA_RELEASE_TAG=v1.0.0 \
  -e PKUBA_GIT_COMMIT=0000000000000000000000000000000000000000 \
  "$api_image" >/dev/null
docker run --rm -d \
  --name "$slot" \
  --network "$network" \
  -p 127.0.0.1::8080 \
  -e "PKUBA_SLOT_API_UPSTREAM=$api:8000" \
  -e PKUBA_RELEASE_TAG=v1.0.0 \
  -e PKUBA_GIT_COMMIT=0000000000000000000000000000000000000000 \
  -v "$repo_root/infra/Caddyfile.slot:/etc/caddy/Caddyfile:ro" \
  "$caddy_image" >/dev/null

port=$(docker port "$slot" 8080/tcp | awk -F: 'NR == 1 {print $NF}')
[[ $port =~ ^[0-9]+$ ]] || {
  echo "slot forwarded-proto test could not resolve the published port" >&2
  exit 1
}

status=000
for _ in $(seq 1 45); do
  status=$(curl --silent --show-error --max-time 5 --max-redirs 0 \
    --header 'Host: admin.example.test' \
    --dump-header "$fixture/api.headers" \
    --output "$fixture/api.body" \
    --write-out '%{http_code}' \
    "http://127.0.0.1:$port/api/v1/health/live" || true)
  [[ $status == 200 ]] && break
  sleep 1
done

[[ $status == 200 ]] || {
  echo "slot-to-Django HTTPS trust regression returned HTTP $status" >&2
  cat "$fixture/api.headers" >&2 2>/dev/null || true
  docker logs "$api" >&2 2>/dev/null || true
  docker logs "$slot" >&2 2>/dev/null || true
  exit 1
}
grep -Fq '"status": "ok"' "$fixture/api.body" \
  || grep -Fq '"status":"ok"' "$fixture/api.body"
! grep -Eiq '^location: https://admin\.example\.test/api/v1/health/live' \
  "$fixture/api.headers"

challenge_status=$(curl --silent --show-error --max-time 5 --max-redirs 0 \
  --request POST \
  --header 'Host: admin.example.test' \
  --cookie-jar "$fixture/challenge.cookies" \
  --dump-header "$fixture/challenge.headers" \
  --output "$fixture/challenge.json" \
  --write-out '%{http_code}' \
  "http://127.0.0.1:$port/api/v1/auth/admin/web-login/challenge")
[[ $challenge_status == 200 ]] || {
  echo "slot web-login challenge returned HTTP $challenge_status" >&2
  cat "$fixture/challenge.headers" >&2 2>/dev/null || true
  exit 1
}
! grep -Eiq '^location:' "$fixture/challenge.headers"
grep -Eiq '^set-cookie: pkuba_sessionid=[^;]+' "$fixture/challenge.headers"
grep -Eiq '^set-cookie: .*;[[:space:]]*Secure([;[:space:]]|$)' \
  "$fixture/challenge.headers"
docker run --rm --network none \
  -v "$fixture:/fixture:ro" \
  "$api_image" \
  python -c 'import json; from pathlib import Path; payload=json.loads(Path("/fixture/challenge.json").read_text()); assert set(payload)=={"scan_payload","browser_token","expires_at","expires_in"}; assert payload["scan_payload"].startswith("PKUBA_ADMIN_WEB_LOGIN:1:"); assert payload["browser_token"]; assert payload["expires_at"]; assert payload["expires_in"] > 0'

echo "Slot Caddy to real Django forwarded-proto and web-login challenge regression passed."
