#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "This command must run as root." >&2
  exit 1
}

for command_name in docker sha256sum tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing command: $command_name" >&2
    exit 1
  }
done
docker compose version >/dev/null 2>&1 || {
  echo "Docker Compose is unavailable." >&2
  exit 1
}

compose_project=${PKUBA_COMPOSE_PROJECT:-pkuba-ip-test}
backup_root=${PKUBA_BOOTSTRAP_BACKUP_ROOT:-/opt/pkuba/backups}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=$backup_root/$timestamp-pre-automation
media_volume=${PKUBA_MEDIA_VOLUME:-${compose_project}_private-media}

mkdir -p "$backup_dir"
docker volume inspect "$media_volume" >/dev/null

container_for_service() {
  docker ps -aq \
    --filter "label=com.docker.compose.project=$compose_project" \
    --filter "label=com.docker.compose.service=$1" \
    | head -n 1
}

db_container=$(container_for_service db)
[[ -n $db_container ]] || {
  echo "Could not find the PostgreSQL container for $compose_project." >&2
  exit 1
}
[[ $(docker inspect --format '{{.State.Running}}' "$db_container") == true ]] || {
  echo "The PostgreSQL container is not running: $db_container" >&2
  exit 1
}

writer_services=(api expiry scoresheet-worker archive-worker outbox)
running_writer_containers=()
for service in "${writer_services[@]}"; do
  container=$(container_for_service "$service")
  if [[ -n $container && $(docker inspect --format '{{.State.Running}}' "$container") == true ]]; then
    running_writer_containers+=("$container")
  fi
done

restart_writers() {
  if [[ ${#running_writer_containers[@]} -gt 0 ]]; then
    docker start "${running_writer_containers[@]}" >/dev/null
  fi
}
trap restart_writers EXIT

if [[ ${#running_writer_containers[@]} -gt 0 ]]; then
  docker stop --time 45 "${running_writer_containers[@]}" >/dev/null
fi

docker exec "$db_container" sh -ec \
  'pg_dump -Fc --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$backup_dir/database.dump"
docker exec -i "$db_container" sh -ec 'pg_restore --list >/dev/null' \
  <"$backup_dir/database.dump"

docker run --rm --entrypoint sh \
  -v "$media_volume:/source:ro" \
  -v "$backup_dir:/backup" \
  postgres:17-alpine \
  -ec 'tar -C /source -czf /backup/private-media.tar.gz .'
tar -tzf "$backup_dir/private-media.tar.gz" >/dev/null

{
  printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'compose_project=%s\n' "$compose_project"
  printf 'database_container=%s\n' "$db_container"
  printf 'media_volume=%s\n' "$media_volume"
  docker ps -a \
    --filter "label=com.docker.compose.project=$compose_project" \
    --format 'container={{.Names}} image={{.Image}} status={{.Status}}'
} >"$backup_dir/MANIFEST.txt"

(
  cd "$backup_dir"
  sha256sum database.dump private-media.tar.gz MANIFEST.txt >SHA256SUMS
  sha256sum --check SHA256SUMS
)

restart_writers
trap - EXIT

echo "Consistent bootstrap backup created: $backup_dir"
