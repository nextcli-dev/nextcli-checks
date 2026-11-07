#!/usr/bin/env bash
# Post every check's result as one pull request comment, and update that
# comment on later runs instead of adding another.
#
# Env: GH_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, HEAD_SHA, CHECKS_RESULTS_DIR
# (the downloaded check-*.verdict.json files), CHECKS_MAX_COST (optional).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
pr="${PR_NUMBER:?PR_NUMBER is required}"
marker="<!-- nextcli-checks -->"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
python3 "$script_dir/checks.py" render-comment \
  --dir "${CHECKS_RESULTS_DIR:?CHECKS_RESULTS_DIR is required}" \
  --repo "$repo" \
  --sha "${HEAD_SHA:-}" \
  --max-cost "${CHECKS_MAX_COST:-}" > "$body"

# head closes the pipe early. Without pausing pipefail, that SIGPIPE (141)
# aborts the script before the comment is created or updated.
comment_id="$(
  set +o pipefail
  gh api --paginate "repos/${repo}/issues/${pr}/comments" \
    --jq ".[] | select(.user.login == \"github-actions[bot]\") | select((.body // \"\") | startswith(\"${marker}\")) | .id" \
    | head -n 1 || true
)"

if [ -n "$comment_id" ]; then
  url="$(gh api -X PATCH "repos/${repo}/issues/comments/${comment_id}" -F "body=@${body}" --jq .html_url)"
else
  url="$(gh api "repos/${repo}/issues/${pr}/comments" -F "body=@${body}" --jq .html_url)"
fi
echo "AI review comment: ${url}"

# v2 posted a "changes requested" review per check. Dismiss any that are left,
# so they don't keep blocking the pull request.
review_ids="$(
  set +o pipefail
  gh api --paginate "repos/${repo}/pulls/${pr}/reviews" \
    --jq '.[] | select(.user.login == "github-actions[bot]") | select(.state == "CHANGES_REQUESTED") | select((.body // "") | startswith("## check/")) | .id' \
    | cat || true
)"
while IFS= read -r review_id; do
  [ -n "$review_id" ] || continue
  gh api -X PUT "repos/${repo}/pulls/${pr}/reviews/${review_id}/dismissals" \
    -f message="Replaced by the combined AI review comment" \
    -f event="DISMISS" >/dev/null 2>&1 || true
done <<< "$review_ids"
