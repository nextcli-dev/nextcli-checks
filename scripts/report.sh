#!/usr/bin/env bash
# Report the verdict for one check: complete its check run and append a row
# to the job summary. The pull request comment that combines every check is
# posted separately, by comment.sh.
# Usage: report.sh <check-name> <verdict-json> <check-run-id>
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=retry-gh.sh
source "$script_dir/retry-gh.sh"
name="${1:?check name is required}"
verdict_file="${2:?verdict file is required}"
check_run_id="${3:-}"

verdict="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$verdict_file")"
summary="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("summary") or "")' "$verdict_file")"
complete="$(python3 -c 'import json,sys; print("false" if json.load(open(sys.argv[1])).get("complete") is False else "true")' "$verdict_file")"
on_error="${CHECKS_ON_ERROR:-neutral}"
case "$on_error" in
  neutral|failure) ;;
  *) on_error="neutral" ;;
esac
conclusion="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import checks; print(checks.conclusion_for(sys.argv[2], sys.argv[3], sys.argv[4] != "false"))' "$script_dir" "$verdict" "$on_error" "$complete")"

details_url=""
if [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${PR_NUMBER:-}" ]; then
  details_url="https://github.com/${GITHUB_REPOSITORY}/pull/${PR_NUMBER}"
fi

# With a check run id, complete that run. Without one (a check that ended
# before its agent ran: skipped by its paths, or misconfigured), create the run
# already completed, in one call, so no update follows right after the create.
if [ -n "${GITHUB_REPOSITORY:-}" ] && { [ -n "$check_run_id" ] || [ -n "${HEAD_SHA:-}" ]; }; then
  body="$(mktemp)"
  body_args=(python3 "$script_dir/checks.py" check-run-body
    --conclusion "$conclusion"
    --details-url "$details_url"
    --repo "$GITHUB_REPOSITORY"
    --sha "${HEAD_SHA:-}"
    --verdict "$verdict_file")
  if [ -z "$check_run_id" ]; then
    body_args+=(--name "check/${name}")
  fi
  "${body_args[@]}" > "$body"
  if [ -n "$check_run_id" ]; then
    retry_gh gh api -X PATCH "repos/${GITHUB_REPOSITORY}/check-runs/${check_run_id}" --input "$body" >/dev/null
    echo "$check_run_id" >> /tmp/check-runs-done.txt
  else
    retry_gh gh api "repos/${GITHUB_REPOSITORY}/check-runs" --input "$body" >/dev/null
  fi
  rm -f "$body"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  if [ ! -f /tmp/checks-summary-started ]; then
    {
      echo "### AI checks"
      echo "| Check | Result | Summary |"
      echo "| --- | --- | --- |"
    } >> "$GITHUB_STEP_SUMMARY"
    touch /tmp/checks-summary-started
  fi
  safe_summary="${summary//|/\\|}"
  safe_summary="${safe_summary//$'\n'/ }"
  echo "| \`${name}\` | ${verdict} | ${safe_summary} |" >> "$GITHUB_STEP_SUMMARY"
fi

echo "check/${name}: ${verdict} (${conclusion}) ${summary}"
