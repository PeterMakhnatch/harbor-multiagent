#!/bin/bash
set -euo pipefail

reward=$(python3 /tests/score.py /logs/world/timing.json /logs/world/timing-journal.json /tests/scenario.json)
mkdir -p /logs/verifier
staged_reward=$(mktemp /logs/verifier/.reward.XXXXXX)
trap 'rm -f -- "$staged_reward"' EXIT
printf '%s\n' "$reward" > "$staged_reward"
mv -- "$staged_reward" /logs/verifier/reward.json
