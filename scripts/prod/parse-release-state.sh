#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: parse-release-state.sh STATE_FILE" >&2; exit 2; }
state_file=$1
[[ -f $state_file ]] || { echo "release state file is missing" >&2; exit 1; }

declare -A values=()
while IFS= read -r line || [[ -n $line ]]; do
  [[ $line =~ ^([A-Z_]+)=([^[:space:]]+)$ ]] \
    || { echo "release state contains an invalid line" >&2; exit 1; }
  key=${BASH_REMATCH[1]}
  value=${BASH_REMATCH[2]}
  case "$key" in
    ACTIVE_SLOT|CURRENT_TAG|CURRENT_COMMIT|CURRENT_API_IMAGE|CURRENT_WEB_IMAGE|CURRENT_RELEASE_DIR|SWITCHED_AT|DATA_RESTORED_AT) ;;
    *) echo "release state contains an unexpected key: $key" >&2; exit 1 ;;
  esac
  [[ -z ${values[$key]+present} ]] \
    || { echo "release state contains a duplicate key: $key" >&2; exit 1; }
  values[$key]=$value
done <"$state_file"

for required in \
  ACTIVE_SLOT CURRENT_TAG CURRENT_COMMIT CURRENT_API_IMAGE CURRENT_WEB_IMAGE CURRENT_RELEASE_DIR; do
  [[ -n ${values[$required]:-} ]] \
    || { echo "release state is missing $required" >&2; exit 1; }
done

[[ ${values[ACTIVE_SLOT]} == blue || ${values[ACTIVE_SLOT]} == green ]] \
  || { echo "release state has an invalid slot" >&2; exit 1; }
[[ ${values[CURRENT_TAG]} =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
  || { echo "release state has an invalid tag" >&2; exit 1; }
[[ ${values[CURRENT_COMMIT]} =~ ^[0-9a-f]{40}$ ]] \
  || { echo "release state has an invalid commit" >&2; exit 1; }
[[ ${values[CURRENT_API_IMAGE]} =~ ^ghcr\.io/jiumao2/pkuba-api@sha256:[0-9a-f]{64}$ ]] \
  || { echo "release state has an invalid API image" >&2; exit 1; }
[[ ${values[CURRENT_WEB_IMAGE]} =~ ^ghcr\.io/jiumao2/pkuba-web@sha256:[0-9a-f]{64}$ ]] \
  || { echo "release state has an invalid web image" >&2; exit 1; }
[[ ${values[CURRENT_RELEASE_DIR]} =~ ^/[A-Za-z0-9._/-]+$ ]] \
  || { echo "release state has an invalid release directory" >&2; exit 1; }
[[ ${values[CURRENT_RELEASE_DIR]} != *//* \
  && ${values[CURRENT_RELEASE_DIR]} != */../* \
  && ${values[CURRENT_RELEASE_DIR]} != */.. \
  && ${values[CURRENT_RELEASE_DIR]} != */./* \
  && ${values[CURRENT_RELEASE_DIR]} != */. ]] \
  || { echo "release state has an unsafe release directory" >&2; exit 1; }

printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
  "${values[ACTIVE_SLOT]}" \
  "${values[CURRENT_TAG]}" \
  "${values[CURRENT_COMMIT]}" \
  "${values[CURRENT_API_IMAGE]}" \
  "${values[CURRENT_WEB_IMAGE]}" \
  "${values[CURRENT_RELEASE_DIR]}"
