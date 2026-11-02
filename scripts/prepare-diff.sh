#!/usr/bin/env bash
# Write /tmp/pr.diff, /tmp/pr-truncated.diff, and /tmp/changed-files.txt from
# the GitHub API, so they don't depend on how much history is checked out.
# If the file list can't be fetched, changed-files.txt is removed so path
# filters do not skip checks on an unknown diff.
set -euo pipefail

: "${PR_NUMBER:?PR_NUMBER is required}"

max_bytes="${CHECKS_MAX_DIFF_BYTES:-100000}"

gh pr diff "$PR_NUMBER" > /tmp/pr.diff
head -c "$max_bytes" /tmp/pr.diff > /tmp/pr-truncated.diff

if ! gh pr diff "$PR_NUMBER" --name-only > /tmp/changed-files.txt; then
  rm -f /tmp/changed-files.txt
  echo "Could not list the changed files; path filters will not skip checks." >&2
fi
