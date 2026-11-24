#!/bin/bash
# Oracle for one seat. Harbor's oracle runs this in every seat, so it applies
# only this seat's feature's gold patch (solution/<seat>.patch) in this seat's
# own workspace. On the way it follows the cooperation protocol: it tells the
# teammate which files it changes and reads the teammate's plan, waiting at
# most COOP_ORACLE_WAIT_SEC seconds (the teammate may not be an oracle).
set -euo pipefail

# Ensure language toolchains are in PATH (DinD exec may not inherit image ENV)
export PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"

SEAT="${COOP_AGENT_ID:?COOP_AGENT_ID is not set: run this in a seat}"
SOLUTION_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH="$SOLUTION_DIR/$SEAT.patch"
FILES=$({ grep '^diff --git ' "$PATCH" || true; } | sed 's|^diff --git a/.* b/||' | paste -sd, -)

echo "$SEAT plans to modify: $FILES"
coop-broadcast "I plan to modify: $FILES" || echo "$SEAT: coop-broadcast failed" >&2

cd "${COOPERBENCH_REPO:-/workspace/repo}"  # the override is for the unit tests
git apply --ignore-whitespace --ignore-space-change "$PATCH" \
  || git apply --3way "$PATCH"
echo "$SEAT applied its gold patch"

deadline=$((SECONDS + ${COOP_ORACLE_WAIT_SEC:-30}))
while [ "$SECONDS" -lt "$deadline" ]; do
  inbox=$(coop-recv) || { echo "$SEAT: coop-recv failed" >&2; break; }
  if [ "$inbox" != "[]" ]; then
    echo "$SEAT received: $inbox"
    break
  fi
  sleep 1
done

coop-broadcast "Done. I changed: $FILES" || echo "$SEAT: coop-broadcast failed" >&2
exit 0
