#!/bin/bash
# Oracle for the one seat of a solo task: it applies both features' gold
# patches (solution/feature1.patch, then solution/feature2.patch) in its own
# workspace. It succeeds only where the two gold patches fit together.
set -euo pipefail

# Ensure language toolchains are in PATH (DinD exec may not inherit image ENV)
export PATH="/usr/local/go/bin:/go/bin:/usr/local/cargo/bin:/root/.cargo/bin:${PATH}"

SOLUTION_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${COOPERBENCH_REPO:-/workspace/repo}"  # the override is for the unit tests
for patch in "$SOLUTION_DIR/feature1.patch" "$SOLUTION_DIR/feature2.patch"; do
  git apply --ignore-whitespace --ignore-space-change "$patch" \
    || git apply --3way "$patch"
  echo "applied $(basename "$patch")"
done
exit 0
