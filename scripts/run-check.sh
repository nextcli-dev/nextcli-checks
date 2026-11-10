#!/usr/bin/env bash
# Run one check file. Path filters skip the agent and still publish a
# successful check run, so a required status check does not stay pending.
# A verdict that fails to parse is retried once. Configuration errors (exit 2)
# and timeouts are not retried, and neither is a run that stopped at a limit
# (exit 3) but still gave a verdict: that verdict is kept as a partial review.
# CHECKS_TIMEOUT covers the first run and its retry together, so a check fits
# in the job's time limit.
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
# NextCLI writes one trace line per model call here (NEXTCLI_TRACE=basic).
trace_dir="/tmp/check-${name}.traces"
: > "$err"
rm -rf "$trace_dir"
mkdir -p "$trace_dir"

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

# The first run and its retry share one time limit.
deadline=$((SECONDS + timeout_minutes * 60))

# A retry needs at least this much time left to be worth starting: a third of
# the limit, and never less than two minutes. A retry that starts with too
# little time is cut off by the timeout and ends with no result at all.
MIN_RETRY_SECONDS=$((timeout_minutes * 60 / 3))
if [ "$MIN_RETRY_SECONDS" -lt 120 ]; then
  MIN_RETRY_SECONDS=120
fi

time_for_retry() {
  if [ $((deadline - SECONDS)) -ge "$MIN_RETRY_SECONDS" ]; then
    return 0
  fi
  echo "::notice::check/${name} has too little of its ${timeout_minutes} minutes left for a retry."
  return 1
}

run_agent() {
  local seconds_left=$((deadline - SECONDS))
  if [ "$seconds_left" -lt 1 ]; then
    seconds_left=1
  fi
  local render_args=(python3 "$script_dir/checks.py" render-prompt --check "$check_file" --diff /tmp/pr-truncated.diff)
  if [ "${1:-}" = "retry" ]; then
    render_args+=(--retry)
  elif [ "${1:-}" = "retry-empty" ]; then
    render_args+=(--retry-empty)
  fi
  # install-deps.sh's results: which dependencies the agent can read.
  if [ -f /tmp/checks-deps.txt ]; then
    render_args+=(--deps /tmp/checks-deps.txt)
  fi
  "${render_args[@]}" > "$prompt"
  # The caller turns errexit off around this call. Don't touch it here: turning
  # it back on before the return below would exit the whole script on any
  # non-zero agent status (124 timeout, 3 limit, 2 bad configuration), before
  # a verdict is written.
  # The agent gets the provider key and nothing else secret: no GitHub token
  # and no Actions runtime tokens.
  CHECKS_PROVIDER="$provider" CHECKS_MODEL="$model" CHECKS_TRACE_DIR="$trace_dir" \
    timeout "${seconds_left}s" \
    env -u GH_TOKEN -u GITHUB_TOKEN \
      -u ACTIONS_RUNTIME_TOKEN -u ACTIONS_RUNTIME_URL -u ACTIONS_RESULTS_URL -u ACTIONS_CACHE_URL \
      -u ACTIONS_ID_TOKEN_REQUEST_TOKEN -u ACTIONS_ID_TOKEN_REQUEST_URL \
      bash "$script_dir/agents/${agent}.sh" "$prompt" > "$raw" 2> "$err"
  return $?
}

# Exit code of an agent that stopped at a limit (NextCLI --max-cost and
# friends). Its answer, if any, is still on stdout.
LIMIT_EXIT=3

# A second run starts a new cost budget. With a cost limit, only nextcli is
# retried, and only with what the first run left of the limit (see
# checks.py retry-budget), so both runs together stay within it.
retry_allowed=true
if [ -n "${CHECKS_MAX_COST:-}" ]; then
  retry_allowed=false
  if [ "$agent" != "nextcli" ]; then
    echo "::notice::max-cost only applies to the nextcli agent. check/${name} runs without a cost limit."
  fi
fi

# What an earlier run of this check cost, added to the reported usage.
spent_before=0

# Set when the agent finished normally (not at a limit), but its verdict has
# no findings and did not finish, while the diff is non-empty. That is not a
# review of the change. At a limit, the same verdict is an honest partial
# review: the agent worked until the limit and found nothing.
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
    if [ "$code" -ne "$LIMIT_EXIT" ] &&
      python3 "$script_dir/checks.py" empty-review --verdict "$verdict_file" --diff /tmp/pr-truncated.diff; then
      review_was_empty=true
      return 1
    fi
    return 0
  elif [ "$code" -eq "$LIMIT_EXIT" ]; then
    write_verdict INCOMPLETE "$(python3 "$script_dir/checks.py" limit-message --stderr "$err" --max-cost "${CHECKS_MAX_COST:-}")"
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
    # With a cost limit, the retry gets only what the first run left of it,
    # so both runs together stay within the limit.
    retry_max_cost=""
    retry_ok=true
    if [ -n "${CHECKS_MAX_COST:-}" ] && [ "$agent" = "nextcli" ]; then
      if budget="$(python3 "$script_dir/checks.py" retry-budget --stderr "$err" --max-cost "$CHECKS_MAX_COST")"; then
        read -r retry_max_cost spent_before <<< "$budget"
      else
        retry_ok=false
        echo "::notice::check/${name} did not review the diff and has too little of its \$${CHECKS_MAX_COST} cost limit left for a retry."
      fi
    fi
    if [ "$retry_ok" != true ] || ! time_for_retry; then
      write_verdict INCOMPLETE "The agent did not review the diff."
    else
      set +e
      if [ -n "$retry_max_cost" ]; then
        CHECKS_MAX_COST="$retry_max_cost" run_agent retry-empty
      else
        run_agent retry-empty
      fi
      code=$?
      set -e
      if [ "$code" -eq 124 ]; then
        # Never trade the first result for a timeout.
        write_verdict INCOMPLETE "The agent did not review the diff, and its retry ran out of time."
      elif ! judge_run "$code"; then
        if [ "$review_was_empty" = true ]; then
          write_verdict INCOMPLETE "The agent did not review the diff."
        else
          write_error_verdict "The agent did not return a valid verdict."
        fi
      fi
    fi
  elif ! time_for_retry; then
    write_error_verdict "The agent did not return a valid verdict."
  elif [ "$retry_allowed" = true ]; then
    set +e
    run_agent retry
    code=$?
    set -e
    judge_run "$code" || write_error_verdict "The agent did not return a valid verdict."
  elif [ "$agent" = "nextcli" ] && [ "$code" -ne "$LIMIT_EXIT" ] &&
    budget="$(python3 "$script_dir/checks.py" retry-budget --stderr "$err" --max-cost "$CHECKS_MAX_COST")"; then
    read -r retry_max_cost spent_before <<< "$budget"
    echo "::notice::check/${name} gave no verdict; retrying with the \$${retry_max_cost} left of its \$${CHECKS_MAX_COST} cost limit."
    set +e
    CHECKS_MAX_COST="$retry_max_cost" run_agent retry
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
python3 "$script_dir/checks.py" annotate --verdict "$verdict_file" --stderr "$err" --spent-before "$spent_before" ${limit_args[@]+"${limit_args[@]}"}

# NextCLI traces every model call (NEXTCLI_TRACE=basic). Keep that log and the
# session (which tool calls returned what) with the results.
calls="/tmp/check-${name}.calls.jsonl"
session_copy="/tmp/check-${name}.session.json"
if [ "$agent" = "nextcli" ]; then
  sessions_dir="${CONTINUE_GLOBAL_DIR:-$HOME/.continue}/sessions"
  cat "$trace_dir"/*.jsonl > "$calls" 2>/dev/null || rm -f "$calls"
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
