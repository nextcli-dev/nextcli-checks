#!/usr/bin/env bash
# Post the verdict for one check: dismiss the previous review, complete the
# check run, and append a row to the job summary.
# Usage: report.sh <check-name> <verdict-json> <check-run-id>
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
name="${1:?check name is required}"
verdict_file="${2:?verdict file is required}"
check_run_id="${3:-}"

verdict="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$verdict_file")"
reason="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["reason"])' "$verdict_file")"
on_error="${CHECKS_ON_ERROR:-neutral}"
case "$on_error" in
  neutral|failure) ;;
  *) on_error="neutral" ;;
esac
conclusion="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import checks; print(checks.conclusion_for(sys.argv[2], sys.argv[3]))' "$script_dir" "$verdict" "$on_error")"

suggestion_count="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])).get("suggestions") or []))' "$verdict_file")"

details_url=""

if [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${PR_NUMBER:-}" ] && [ "$verdict" != "PASS" ] && [ "$verdict" != "SKIP" ]; then
  # Drop the review this check posted on the previous run so suggestion
  # threads do not pile up. Dismiss fails closed when the token cannot.
  review_ids="$(gh api --paginate "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/reviews" \
    --jq ".[] | select(.body != null) | select(.body | startswith(\"## check/${name}\")) | .id" 2>/dev/null || true)"
  if [ -n "$review_ids" ]; then
    while IFS= read -r review_id; do
      [ -n "$review_id" ] || continue
      gh api -X PUT "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/reviews/${review_id}/dismissals" \
        -f message="Superseded by a new run of check/${name}" \
        -f event="DISMISS" >/dev/null 2>&1 || true
    done <<< "$review_ids"
  fi

  if [ "$suggestion_count" -gt 0 ] && [ -n "${HEAD_SHA:-}" ]; then
    payload="$(mktemp)"
    python3 "$script_dir/checks.py" review-payload --name "$name" --sha "$HEAD_SHA" --verdict "$verdict_file" > "$payload"
    review_result="$(gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/reviews" --input "$payload" 2>&1)" || true
    rm -f "$payload"
    review_id="$(printf '%s' "$review_result" | jq -r '.id // empty' 2>/dev/null || true)"
    if [ -n "$review_id" ]; then
      details_url="https://github.com/${GITHUB_REPOSITORY}/pull/${PR_NUMBER}#pullrequestreview-${review_id}"
    fi
  fi

  if [ -z "$details_url" ]; then
    comment_body="$(printf '## check/%s\n\n**Verdict:** %s\n\n%s\n' "$name" "$verdict" "$reason")"
    comment_result="$(gh pr comment "$PR_NUMBER" --body "$comment_body" 2>&1)" || true
    comment_url="$(printf '%s\n' "$comment_result" | grep -oE 'https://github.com/[^ ]+' | head -1 || true)"
    if [ -n "$comment_url" ]; then
      details_url="$comment_url"
    fi
  fi
fi

if [ -n "$check_run_id" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then
  patch="$(mktemp)"
  python3 "$script_dir/checks.py" check-run-body \
    --conclusion "$conclusion" \
    --details-url "$details_url" \
    --verdict "$verdict_file" > "$patch"
  gh api -X PATCH "repos/${GITHUB_REPOSITORY}/check-runs/${check_run_id}" --input "$patch" >/dev/null
  rm -f "$patch"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  if [ ! -f /tmp/checks-summary-started ]; then
    {
      echo "### AI checks"
      echo "| Check | Result | Reason |"
      echo "| --- | --- | --- |"
    } >> "$GITHUB_STEP_SUMMARY"
    touch /tmp/checks-summary-started
  fi
  safe_reason="${reason//|/\\|}"
  safe_reason="${safe_reason//$'\n'/ }"
  echo "| \`${name}\` | ${verdict} | ${safe_reason} |" >> "$GITHUB_STEP_SUMMARY"
fi

echo "check/${name}: ${verdict} (${conclusion}) ${reason}"
