#!/usr/bin/env bash
# Move agent configuration a pull request could have added out of the checkout
# before any agent runs. The checkout is the pull request's head, so these
# files are written by whoever opened it, and each agent reads them from its
# working directory: a .env that sets environment variables (NODE_OPTIONS,
# GIT_CONFIG_*, a proxy), Claude Code hooks and MCP servers, OpenCode plugins
# and MCP commands. The diff the agents review still shows them.
#
# Usage: neutralize-pr-config.sh [checkout] [destination]
set -euo pipefail

checkout="${1:-.}"
dest="${2:-${RUNNER_TEMP:-/tmp}/pr-agent-config}"

paths=(
  .env
  .claude
  .mcp.json
  opencode.json
  opencode.jsonc
  .opencode
  .continue/.env
  .continue/environment.json
)

moved=()
move() {
  local rel="$1"
  local src="$checkout/$rel"
  # -L too: a symlink is moved, never followed
  if [ -e "$src" ] || [ -L "$src" ]; then
    mkdir -p "$dest/$(dirname "$rel")"
    mv "$src" "$dest/$rel"
    moved+=("$rel")
  fi
}

for rel in "${paths[@]}"; do
  move "$rel"
done
# .env.local, .env.production and the like; .continue/settings*.json
shopt -s nullglob dotglob
for src in "$checkout"/.env.* "$checkout"/.continue/settings*.json; do
  move "${src#"$checkout"/}"
done

if [ "${#moved[@]}" -eq 0 ]; then
  exit 0
fi

echo "Moved agent configuration from the pull request out of the checkout: ${moved[*]}"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "Agent configuration from the pull request was not used (the diff still shows it):"
    for rel in "${moved[@]}"; do
      echo "- \`$rel\`"
    done
  } >> "$GITHUB_STEP_SUMMARY"
fi
