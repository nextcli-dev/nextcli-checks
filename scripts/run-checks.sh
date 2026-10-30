#!/usr/bin/env bash
# Run every check, or the single check named by CHECKS_ONLY.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
checks_dir="${CHECKS_DIR:-.checks}"
agent="${CHECKS_AGENT:-opencode}"

case "$agent" in
  opencode|nextcli|claude-code) ;;
  *)
    echo "Unknown agent '${agent}'. Use opencode, nextcli, or claude-code." >&2
    exit 1
    ;;
esac
export CHECKS_AGENT="$agent"

if [ -n "${PROVIDER_API_KEY:-}" ]; then
  echo "::add-mask::${PROVIDER_API_KEY}"
fi

if [ ! -d "$checks_dir" ]; then
  echo "No ${checks_dir}/ directory; nothing to run."
  exit 0
fi

if [ -n "${CHECKS_ONLY:-}" ]; then
  names=("$CHECKS_ONLY")
else
  names=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    names+=("$line")
  done < <(python3 "$script_dir/checks.py" list --dir "$checks_dir" --lines)
fi

if [ "${#names[@]}" -eq 0 ]; then
  echo "No check files in ${checks_dir}/."
  exit 0
fi

for name in "${names[@]}"; do
  bash "$script_dir/run-check.sh" "${checks_dir}/${name}.md"
done
