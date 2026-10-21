#!/usr/bin/env bash
# Usage: bash docs/handoff/examples/call.sh POST /v1/plans /tmp/create.json
set -euo pipefail
method=${1:?method}; path=${2:?path}
args=(--silent --show-error --fail-with-body --max-time 30 -X "$method")
if [[ -n ${STORY_API_KEY:-} ]]; then args+=(-H "Authorization: Bearer $STORY_API_KEY"); fi
if [[ $# -ge 3 ]]; then args+=(-H 'Content-Type: application/json' --data-binary "@$3"); fi
curl "${args[@]}" "${STORY_API_URL:-http://127.0.0.1:8000}$path"
