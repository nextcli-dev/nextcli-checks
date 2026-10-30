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
fi

write_error_verdict() {
  local message="$1"
  if [ -n "${PROVIDER_API_KEY:-}" ]; then
    message="${message//${PROVIDER_API_KEY}/[REDACTED]}"
  fi
  REASON="$message" python3 -c 'import json,os,sys; json.dump({"verdict":"ERROR","reason":os.environ["REASON"],"suggestions":[]}, sys.stdout)' > "$verdict_file"
}

if [ "${fm_skip:-0}" = "1" ]; then
  python3 -c 'import json,sys; json.dump({"verdict":"SKIP","reason":"No changed files match this check'\''s paths.","suggestions":[]}, sys.stdout)' > "$verdict_file"
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
  fi
  "${render_args[@]}" > "$prompt"
  set +e
  CHECKS_PROVIDER="$provider" CHECKS_MODEL="$model" \
    timeout "${timeout_minutes}m" \
    bash "$script_dir/agents/${agent}.sh" "$prompt" > "$raw" 2> "$err"
  local code=$?
  set -e
  return "$code"
}

set +e
run_agent
code=$?
set -e

if [ "$code" -eq 124 ]; then
  write_error_verdict "The check timed out after ${timeout_minutes} minutes."
elif [ "$code" -eq 2 ]; then
  message="$(tail -n 1 "$err" 2>/dev/null || true)"
  write_error_verdict "${message:-The agent rejected this provider configuration.}"
elif ! bash "$script_dir/parse-verdict.sh" < "$raw" > "$verdict_file"; then
  set +e
  run_agent retry
  retry_code=$?
  set -e
  if [ "$retry_code" -eq 124 ]; then
    write_error_verdict "The check timed out after ${timeout_minutes} minutes."
  elif [ "$retry_code" -eq 2 ]; then
    message="$(tail -n 1 "$err" 2>/dev/null || true)"
    write_error_verdict "${message:-The agent rejected this provider configuration.}"
  elif ! bash "$script_dir/parse-verdict.sh" < "$raw" > "$verdict_file"; then
    write_error_verdict "The agent did not return a valid verdict."
  fi
fi

bash "$script_dir/report.sh" "$name" "$verdict_file" "$check_run_id"
