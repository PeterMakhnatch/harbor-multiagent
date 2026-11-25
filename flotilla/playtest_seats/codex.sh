#!/bin/bash
set -euo pipefail
workdir=$1
prompt=$2
model=$3
root=$(dirname "$workdir")
binary=${CODEX_BIN:-$(command -v codex)}
mkdir -p "$root/codex-home" "$root/tmp" "$root/zdot" "$root/home"
cd "$workdir"
export HOME="$root/home" CODEX_HOME="$root/codex-home" TMPDIR="$root/tmp" ZDOTDIR="$root/zdot" SHELL=/bin/bash
exec "$binary" exec --model "$model" --ignore-user-config --ignore-rules --ephemeral \
  --skip-git-repo-check --sandbox workspace-write --json --color never \
  -c allow_login_shell=false -c sandbox_workspace_write.network_access=true --disable shell_snapshot \
  --disable daemon_auto_start --disable apps --disable plugins --disable remote_plugin \
  --disable browser_use --disable browser_use_external --disable in_app_browser \
  --disable computer_use --disable image_generation --disable multi_agent - < "$prompt"
