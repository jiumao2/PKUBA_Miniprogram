#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 3 ]] \
  || { echo "usage: validate-release-identity.sh STATE_FILE RELEASE_ROOT REPOSITORY_DIR" >&2; exit 2; }
state_file=$1
release_root=$2
repository_dir=$3
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

parsed=$(bash "$script_dir/parse-release-state.sh" "$state_file")
IFS=$'\t' read -r \
  slot tag commit api_image web_image release_dir capability rollback_allowed_from <<<"$parsed"

expected_release_dir=$(realpath -m "$release_root/$tag")
actual_release_dir=$(realpath "$release_dir")
[[ $actual_release_dir == "$expected_release_dir" ]] \
  || { echo "release directory does not match its tag" >&2; exit 1; }
[[ -f $actual_release_dir/infra/compose.prod.slot.yml ]] \
  || { echo "release has no slot Compose file" >&2; exit 1; }
[[ $(git -C "$actual_release_dir" rev-parse HEAD) == "$commit" ]] \
  || { echo "release worktree HEAD does not match state" >&2; exit 1; }
[[ $(git -C "$repository_dir" rev-parse "$tag^{commit}") == "$commit" ]] \
  || { echo "release tag does not match state" >&2; exit 1; }
derived_capability=$(bash "$script_dir/derive-release-capability.sh" "$actual_release_dir")
[[ $derived_capability == "$capability" ]] \
  || { echo "release capability does not match its contract" >&2; exit 1; }

for image in "$api_image" "$web_image"; do
  docker image inspect "$image" >/dev/null \
    || { echo "release image is unavailable" >&2; exit 1; }
  [[ $(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image") == "$commit" ]] \
    || { echo "release image revision does not match state" >&2; exit 1; }
done

printf '%s\n' "$parsed"
