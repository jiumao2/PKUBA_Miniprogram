#!/usr/bin/env bash
set -euo pipefail

original_command=${SSH_ORIGINAL_COMMAND:-}
if [[ $original_command == *$'\n'* || $original_command == *$'\r'* ]]; then
  echo "Only an exact PKUBA deploy or verification command is allowed for this key." >&2
  exit 64
fi
if [[ $original_command =~ ^verify\ ([0-9a-f]{64})$ ]]; then
  verification_nonce=${BASH_REMATCH[1]}
  if [[ -t 0 || -t 1 || -t 2 ]]; then
    echo "Deployment verification must not receive a PTY." >&2
    exit 65
  fi
  exec sudo -n /usr/local/sbin/pkuba-sync-release-tools verify \
    "$verification_nonce"
fi

read -r -a command_parts <<<"$original_command"

if [[ ${#command_parts[@]} -ne 5 || ${command_parts[0]} != deploy ]]; then
  echo "Only an exact PKUBA deploy or verification command is allowed for this key." >&2
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

exec sudo -n /usr/local/sbin/pkuba-sync-release-tools deploy \
  "$release_tag" "$release_commit" "$api_image" "$web_image"
