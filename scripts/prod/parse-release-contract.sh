#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] || { echo "usage: parse-release-contract.sh CONTRACT_FILE" >&2; exit 2; }
contract_file=$1
[[ -f $contract_file ]] || { echo "release contract file is missing" >&2; exit 1; }

declare -A values=()
while IFS= read -r line || [[ -n $line ]]; do
  [[ -z $line || $line == \#* ]] && continue
  [[ $line =~ ^([A-Z_]+)=([^[:space:]]+)$ ]] \
    || { echo "release contract contains an invalid line" >&2; exit 1; }
  key=${BASH_REMATCH[1]}
  value=${BASH_REMATCH[2]}
  case "$key" in
    PKUBA_PREVIOUS_APP_COMPATIBLE|PKUBA_APP_CAPABILITY|PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY) ;;
    *) echo "release contract contains an unexpected key: $key" >&2; exit 1 ;;
  esac
  [[ -z ${values[$key]+present} ]] \
    || { echo "release contract contains a duplicate key: $key" >&2; exit 1; }
  values[$key]=$value
done <"$contract_file"

for required in \
  PKUBA_PREVIOUS_APP_COMPATIBLE PKUBA_APP_CAPABILITY PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY; do
  [[ -n ${values[$required]:-} ]] \
    || { echo "release contract is missing $required" >&2; exit 1; }
done
[[ ${values[PKUBA_PREVIOUS_APP_COMPATIBLE]} == 0 \
  || ${values[PKUBA_PREVIOUS_APP_COMPATIBLE]} == 1 ]] \
  || { echo "release contract has an invalid compatibility flag" >&2; exit 1; }
for key in PKUBA_APP_CAPABILITY PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY; do
  [[ ${values[$key]} =~ ^[a-z0-9][a-z0-9._-]*$ ]] \
    || { echo "release contract has an invalid capability" >&2; exit 1; }
done

printf '%s\t%s\t%s\n' \
  "${values[PKUBA_PREVIOUS_APP_COMPATIBLE]}" \
  "${values[PKUBA_APP_CAPABILITY]}" \
  "${values[PKUBA_REQUIRED_PREVIOUS_APP_CAPABILITY]}"
