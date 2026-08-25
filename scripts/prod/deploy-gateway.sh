#!/usr/bin/env bash
set -euo pipefail

original_command=${SSH_ORIGINAL_COMMAND:-}
read -r -a command_parts <<<"$original_command"

if [[ ${#command_parts[@]} -ne 5 || ${command_parts[0]} != deploy ]]; then
  echo "Only the PKUBA deploy command is allowed for this key." >&2
  exit 64
fi

release_tag=${command_parts[1]}
release_commit=${command_parts[2]}
api_image=${command_parts[3]}
web_image=${command_parts[4]}

[[ $release_tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 64
[[ $release_commit =~ ^[0-9a-f]{40}$ ]] || exit 64
[[ $api_image =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] || exit 64
[[ $web_image =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] || exit 64

exec sudo -n /usr/local/sbin/pkuba-deploy-blue-green \
  "$release_tag" "$release_commit" "$api_image" "$web_image"
