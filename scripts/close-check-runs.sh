#!/usr/bin/env bash
# Complete check runs that were started but never reported, for example
# because the job was cancelled or timed out. Runs as an always() step.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=retry-gh.sh
source "$script_dir/retry-gh.sh"

started=/tmp/check-runs-started.txt
done_file=/tmp/check-runs-done.txt
[ -f "$started" ] || exit 0
[ -n "${GITHUB_REPOSITORY:-}" ] || exit 0
touch "$done_file"

while IFS= read -r id; do
  [ -n "$id" ] || continue
  if grep -qx "$id" "$done_file"; then
    continue
  fi
  echo "Closing unfinished check run ${id}."
  retry_gh gh api -X PATCH "repos/${GITHUB_REPOSITORY}/check-runs/${id}" \
    -f status=completed \
    -f conclusion=neutral \
    -f "output[title]=Did not finish" \
    -f "output[summary]=The job was cancelled or timed out before this check reported a verdict." >/dev/null || true
done < "$started"
