#!/usr/bin/env bash
# Write /tmp/pr.diff, /tmp/pr-truncated.diff, and /tmp/changed-files.txt.
# If the base and head commits are not available, changed-files.txt is removed
# so path filters do not skip checks on an unknown diff.
set -euo pipefail

: "${PR_NUMBER:?PR_NUMBER is required}"
: "${BASE_SHA:?BASE_SHA is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"

max_bytes="${CHECKS_MAX_DIFF_BYTES:-100000}"

gh pr diff "$PR_NUMBER" > /tmp/pr.diff
head -c "$max_bytes" /tmp/pr.diff > /tmp/pr-truncated.diff

if ! git cat-file -e "${HEAD_SHA}^{commit}" 2>/dev/null; then
  git fetch --no-tags origin "pull/${PR_NUMBER}/head" || true
fi

if git cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null && git cat-file -e "${HEAD_SHA}^{commit}" 2>/dev/null; then
  git diff --name-only "$BASE_SHA" "$HEAD_SHA" > /tmp/changed-files.txt
else
  rm -f /tmp/changed-files.txt
  echo "Could not resolve $BASE_SHA..$HEAD_SHA; path filters will not skip checks." >&2
fi
