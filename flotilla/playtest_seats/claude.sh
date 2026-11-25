#!/bin/bash
set -euo pipefail
workdir=$1
prompt=$2
model=$3
root=$(dirname "$workdir")
binary=${CLAUDE_BIN:-$(command -v claude)}
mkdir -p "$root/tmp" "$root/home" "$root/claude-home"
cd "$workdir"
export HOME="$root/home" TMPDIR="$root/tmp" CLAUDE_CODE_TMPDIR="$root/tmp" CLAUDE_CONFIG_DIR="$root/claude-home" SHELL=/bin/bash
exec "$binary" -p --model "$model" --safe-mode --setting-sources "" --strict-mcp-config \
  --disable-slash-commands --no-session-persistence --output-format stream-json --verbose < "$prompt"
