#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 1 ]] \
  || { echo "usage: derive-release-capability.sh RELEASE_DIR" >&2; exit 2; }
release_dir=$1
contract_file=$release_dir/infra/release-contract.env

# Releases created before application capability contracts are deliberately
# classified as legacy. A caller may never promote them by supplying a flag.
if [[ ! -f $contract_file ]]; then
  printf '%s\n' legacy-request-type-v0
  exit 0
fi

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if parsed=$(bash "$script_dir/parse-release-contract.sh" "$contract_file" 2>/dev/null); then
  IFS=$'\t' read -r _ capability _ <<<"$parsed"
  printf '%s\n' "$capability"
  exit 0
fi

# Legacy contracts may contain only the old compatibility flag. Parse the file
# as data and reject every additional/duplicate key or shell expression.
seen=0
while IFS= read -r line || [[ -n $line ]]; do
  [[ -z $line || $line == \#* ]] && continue
  [[ $line =~ ^PKUBA_PREVIOUS_APP_COMPATIBLE=([01])$ ]] \
    || { echo "legacy release contract is invalid" >&2; exit 1; }
  (( seen == 0 )) || { echo "legacy release contract has a duplicate key" >&2; exit 1; }
  seen=1
done <"$contract_file"
(( seen == 1 )) || { echo "legacy release contract has no compatibility flag" >&2; exit 1; }
printf '%s\n' legacy-request-type-v0
