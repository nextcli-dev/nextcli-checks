#!/usr/bin/env bash
# Run one check file. Path filters skip the agent and still publish a
# successful check run, so a required status check does not stay pending.
# A verdict that fails to parse is retried once. Configuration errors (exit 2)
# and timeouts are not retried.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
check_file="${1:?check file is required}"

if [ ! -f "$check_file" ]; then
  echo "Check file not found: $check_file" >&2
  exit 1
fi

name="$(basename "$check_file" .md)"
agent="${CHECKS_AGENT:-opencode}"
provider="${CHECKS_PROVIDER:-}"
model="${CHECKS_MODEL:-}"
timeout_minutes="${CHECKS_TIMEOUT:-10}"

front_args=(python3 "$script_dir/checks.py" frontmatter --check "$check_file" --provider "$provider")
if [ -f /tmp/changed-files.txt ]; then
  front_args+=(--changed /tmp/changed-files.txt)
fi

eval "$("${front_args[@]}")"
provider="${fm_provider:-$provider}"
model="${fm_model:-$model}"

raw="/tmp/check-${name}.json"
err="/tmp/check-${name}.err"
prompt="/tmp/check-${name}.prompt"
verdict_file="/tmp/check-${name}.verdict.json"
: > "$err"

check_run_id=""
if [ -n "${GITHUB_REPOSITORY:-}" ] && [ -n "${HEAD_SHA:-}" ]; then
  check_run_id="$(gh api "repos/${GITHUB_REPOSITORY}/check-runs" \
    -f "name=check/${name}" \
    -f "head_sha=${HEAD_SHA}" \
    -f status=in_progress \
    --jq '.id')"
  # close-check-runs.sh completes any run left in progress by a cancelled or
  # timed-out job.
  echo "$check_run_id" >> /tmp/check-runs-started.txt
fi

# Writes a verdict that isn't the agent's own: ERROR, SKIP or INCOMPLETE.
write_verdict() {
  VERDICT="$1" SUMMARY="$2" python3 -c 'import json,os,sys; v=os.environ["VERDICT"]; json.dump({"verdict":v,"summary":os.environ["SUMMARY"],"complete":v!="INCOMPLETE","findings":[]}, sys.stdout)' > "$verdict_file"
}

write_error_verdict() {
  write_verdict ERROR "$1"
}

if [ "${fm_skip:-0}" = "1" ]; then
  write_verdict SKIP "No changed files match this check's paths."
  echo '{"skipped":true}' > "$raw"
  bash "$script_dir/report.sh" "$name" "$verdict_file" "$check_run_id"
  exit 0
fi

if [ -z "$provider" ] || [ -z "$model" ]; then
  write_error_verdict "provider and model are required."
  echo "missing provider or model" > "$raw"
  bash "$script_dir/report.sh" "$name" "$verdict_file" "$check_run_id"
  exit 0
fi

run_agent() {
  local render_args=(python3 "$script_dir/checks.py" render-prompt --check "$check_file" --diff /tmp/pr-truncated.diff)
  if [ "${1:-}" = "retry" ]; then
    render_args+=(--retry)
  elif [ "${1:-}" = "retry-empty" ]; then
    render_args+=(--retry-empty)
  fi
  "${render_args[@]}" > "$prompt"
  # The caller turns errexit off around this call. Don't touch it here: turning
  # it back on before the return below would exit the whole script on any
  # non-zero agent status (124 timeout, 3 limit, 2 bad configuration), before
  # a verdict is written.
  # The agent gets the provider key and nothing else secret: no GitHub token
  # and no Actions runtime tokens.
  CHECKS_PROVIDER="$provider" CHECKS_MODEL="$model" \
    timeout "${timeout_minutes}m" \
    env -u GH_TOKEN -u GITHUB_TOKEN \
      -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_RUNTIME_URL -u ACTIONS_RESULTS_URL -u ACTIONS_CACHE_URL \
      -u ACTIONS_ID_TOKEN_REQUEST_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_URL \
      bash "$script_dir/agents/${agent}.sh" "$prompt" > "$raw" 2> "$err"
  return $?
}

# Exit code of an agent that stopped at a limit (NextCLI --max-cost and
# friends). Its answer, if any, is still on stdout.
LIMIT_EXIT=3

# A second run would start a new cost budget, so a run with a cost limit is
# not retried, whichever agent is selected. Only nextcli enforces the limit.
retry_allowed=true
if [ -n "${CHECKS_MAX_COST:-}" ]; then
  retry_allowed=false
  if [ "$agent" != "nextcli" ]; then
    echo "::notice::max-cost only applies to the nextcli agent. check/${name} runs without a cost limit."
  fi
fi

# Set when the parsed verdict has no findings and did not finish, while the
# diff is non-empty. That is not a review of the change.
review_was_empty=false

# Turns one agent run into a verdict file. Returns 1 when the output has no
# verdict and the run may be retried.
judge_run() {
  local code="$1"
  review_was_empty=false
  if [ "$code" -eq 124 ]; then
    write_error_verdict "The check timed out after ${timeout_minutes} minutes."
  elif [ "$code" -eq 2 ]; then
    local message
    message="$(tail -n 1 "$err" 2>/dev/null || true)"
    write_error_verdict "${message:-The agent rejected this provider configuration.}"
  elif bash "$script_dir/parse-verdict.sh" < "$raw" > "$verdict_file"; then
    if python3 "$script_dir/checks.py" empty-review --verdict "$verdict_file" --diff /tmp/pr-truncated.diff; then
      review_was_empty=true
      return 1
    fi
    return 0
  elif [ "$code" -eq "$LIMIT_EXIT" ]; then
    write_verdict INCOMPLETE "The agent stopped at its cost limit (\$${CHECKS_MAX_COST:-?}) before it returned a result."
  else
    return 1
  fi
  return 0
}

set +e
run_agent
code=$?
set -e

if ! judge_run "$code"; then
  if [ "$review_was_empty" = true ]; then
    # A cost limit does not block this retry: the first result reviewed nothing.
    set +e
    run_agent retry-empty
    code=$?
    set -e
    if ! judge_run "$code"; then
      if [ "$review_was_empty" = true ]; then
        write_verdict INCOMPLETE "The agent did not review the diff."
      else
        write_error_verdict "The agent did not return a valid verdict."
      fi
    fi
  elif [ "$retry_allowed" = true ]; then
    set +e
    run_agent retry
    code=$?
    set -e
    judge_run "$code" || write_error_verdict "The agent did not return a valid verdict."
  else
    write_error_verdict "The agent did not return a valid verdict."
  fi
fi

limit_args=()
if [ "$code" -eq "$LIMIT_EXIT" ]; then
  limit_args+=(--limit-exit)
fi
python3 "$script_dir/checks.py" annotate --verdict "$verdict_file" --stderr "$err" ${limit_args[@]+"${limit_args[@]}"}

# NextCLI logs every model call next to its session. Keep that log and the
# session (which tool calls returned what) with the results.
calls="/tmp/check-${name}.calls.jsonl"
session_copy="/tmp/check-${name}.session.json"
if [ "$agent" = "nextcli" ]; then
  sessions_dir="${CONTINUE_GLOBAL_DIR:-$HOME/.continue}/sessions"
  cat "$sessions_dir"/*.calls.jsonl > "$calls" 2>/dev/null || rm -f "$calls"
  newest="$(ls -t "$sessions_dir"/*.json 2>/dev/null | grep -v '/sessions\.json$' | head -n 1 || true)"
  if [ -n "$newest" ]; then
    cp "$newest" "$session_copy"
  fi
fi

# Nothing the agent wrote is posted or uploaded until secrets are removed.
python3 "$script_dir/checks.py" redact "$verdict_file" "$raw" "$err" "$prompt" "$calls" "$session_copy"
bash "$script_dir/report.sh" "$name" "$verdict_file" "$check_run_id"

if [ -s "$calls" ]; then
  echo "::group::check/${name}: model calls"
  python3 "$script_dir/checks.py" session-report --calls "$calls" --session "$session_copy" || true
  echo "::endgroup::"
fi
