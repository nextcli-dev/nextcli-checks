#!/usr/bin/env bash
# Skip check runs whose head commit was authored by github-actions[bot].
# Accepting a suggestion comment creates that commit and would otherwise loop.
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${HEAD_SHA:?HEAD_SHA is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

meta="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}")"
login="$(printf '%s' "$meta" | jq -r '.author.login // empty')"
name="$(printf '%s' "$meta" | jq -r '.commit.author.name // empty')"

if [ "$login" = "github-actions[bot]" ] || [ "$name" = "github-actions[bot]" ]; then
  echo "skip=true" >> "$GITHUB_OUTPUT"
  echo "Head commit ${HEAD_SHA} is from github-actions[bot]; skipping checks."
else
  echo "skip=false" >> "$GITHUB_OUTPUT"
fi
