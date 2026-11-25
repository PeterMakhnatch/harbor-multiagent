#!/bin/bash
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo "usage: claude.sh WORKDIR PROMPT MODEL" >&2
  exit 2
fi
if [[ -z ${PLAYTEST_SANDBOX:-} ]]; then
  echo "claude.sh: run it with playtest.py --sandbox-profile; it skips Claude's permission checks" >&2
  exit 2
fi
workdir=$1
prompt=$2
model=$3
root=$(dirname "$workdir")
binary=${CLAUDE_BIN:-$(command -v claude)}
mkdir -p "$root/tmp" "$root/zdot"
cd "$workdir"
export TMPDIR="$root/tmp" CLAUDE_CODE_TMPDIR="$root/tmp" SHELL=/bin/zsh ZDOTDIR="$root/zdot"
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
exec "$binary" -p --model "$model" --safe-mode --setting-sources "" --strict-mcp-config \
  --disable-slash-commands --no-session-persistence --dangerously-skip-permissions \
  --output-format stream-json --verbose < "$prompt"
