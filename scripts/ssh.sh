#!/usr/bin/env bash
set -euo pipefail
: "${INKDROP_PROJECT:?Set INKDROP_PROJECT}"
: "${INKDROP_ZONE:?Set INKDROP_ZONE}"
if (( $# > 1 )); then
  printf '%s\n' 'Usage: scripts/ssh.sh ["remote command"]' >&2
  exit 2
fi
args=(compute ssh "${INKDROP_VM:-inkdrop-couchdb}" --project="$INKDROP_PROJECT"
  --zone="$INKDROP_ZONE" --tunnel-through-iap --ssh-key-expire-after=30m)
if (( $# == 1 )); then args+=(--command="$1"); fi
exec gcloud "${args[@]}"
