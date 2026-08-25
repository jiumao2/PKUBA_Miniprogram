#!/usr/bin/env bash
set -euo pipefail

[[ $# -eq 2 ]] \
  || { echo "usage: check-app-capability.sh CURRENT_CAPABILITY CONTRACT_FILE" >&2; exit 2; }
current_capability=$1
contract_file=$2
[[ $current_capability =~ ^[a-z0-9][a-z0-9._-]*$ ]] \
  || { echo "active application capability is invalid" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
parsed=$(bash "$script_dir/parse-release-contract.sh" "$contract_file")
IFS=$'\t' read -r compatible candidate_capability required_previous <<<"$parsed"
[[ $compatible == 1 ]] \
  || { echo "release is not approved for old/new application coexistence" >&2; exit 1; }
[[ $current_capability == "$required_previous" ]] \
  || { echo "active application lacks the rollback-safe capability required by this release" >&2; exit 1; }

printf '%s\n' "$candidate_capability"
