#!/bin/bash
set -euo pipefail
if [[ $# -ne 3 ]]; then
  echo "usage: codex.sh WORKDIR PROMPT MODEL" >&2
  exit 2
fi
if [[ -z ${PLAYTEST_SANDBOX:-} ]]; then
  echo "codex.sh: run it with playtest.py --sandbox-profile; it bypasses Codex's own sandbox" >&2
  exit 2
fi
workdir=$1
prompt=$2
model=$3
root=$(dirname "$workdir")
binary=${CODEX_BIN:-$(command -v codex)}
mkdir -p "$root/codex-home" "$root/tmp" "$root/zdot" "$root/home"
install -m 600 "$HOME/.codex/auth.json" "$root/codex-home/auth.json"
cd "$workdir"
# PATH comes from playtest.py: the seat's own bin (python3) first. zsh writes here-documents to TMPPREFIX.
export HOME="$root/home" CODEX_HOME="$root/codex-home" TMPDIR="$root/tmp" TMPPREFIX="$root/tmp/zsh" \
  ZDOTDIR="$root/zdot" SHELL=/bin/bash
exec "$binary" exec --model "$model" --ignore-user-config --ignore-rules --ephemeral \
  --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --json --color never \
  -c allow_login_shell=false --disable shell_snapshot \
  --disable daemon_auto_start --disable apps --disable plugins --disable remote_plugin \
  --disable browser_use --disable browser_use_external --disable in_app_browser \
  --disable computer_use --disable image_generation --disable multi_agent - < "$prompt"
