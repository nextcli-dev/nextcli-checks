#!/usr/bin/env bash
# Run one check with the NextCLI `cn` binary. Writes a temporary config.yaml
# that holds PROVIDER_API_KEY and removes it on exit.
# Exit 2 for configuration errors that should not be retried.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
prompt_file="${1:?prompt file is required}"

if [ -z "${PROVIDER_API_KEY:-}" ]; then
  echo "PROVIDER_API_KEY is empty." >&2
  exit 2
fi
if [ -z "${CHECKS_PROVIDER:-}" ] || [ -z "${CHECKS_MODEL:-}" ]; then
  echo "provider and model are required." >&2
  exit 2
fi
if [ "$CHECKS_PROVIDER" = "openai-compatible" ] && [ -z "${CHECKS_BASE_URL:-}" ]; then
  echo "base-url is required when provider is openai-compatible." >&2
  exit 2
fi
if ! command -v cn >/dev/null 2>&1; then
  echo "cn is not on PATH. Install NextCLI or pass nextcli-ref." >&2
  exit 2
fi

config="$(mktemp)"
chmod 600 "$config"
trap 'rm -f "$config"' EXIT
python3 "$script_dir/../checks.py" nextcli-config > "$config"

# The key is in the config file now. Keep it out of the agent's environment.
unset PROVIDER_API_KEY

# One trace line per model call: tokens, cost, timings, finish reason, answer
# and reasoning length, tool names, and the provider's error message when a
# call fails. No prompt or answer text. A cn without tracing ignores these.
if [ -n "${CHECKS_TRACE_DIR:-}" ]; then
  export NEXTCLI_TRACE=basic NEXTCLI_TRACE_DIR="$CHECKS_TRACE_DIR"
fi

# --readonly in headless mode: read, search and list only. No shell, no MCP
# tools and no Fetch. At --max-cost the agent gives a final answer and exits 3.
args=(-p --format json --readonly --config "$config")
# --format json tells the model to answer with "properties relevant to the
# question" in the system message, on every turn. The verdict schema goes in
# the system message too, as a rule, so a long review doesn't drift to keys of
# its own. The prompt holds the schema as well.
answer_format="$(cd "$script_dir/.." && pwd)/answer-format.md"
if [ -f "$answer_format" ]; then
  args+=(--rule "$answer_format")
fi
# A repair: continue the session of a run that answered without a verdict.
# --fork copies it into a new session, so the first one stays as it was.
if [ -n "${CHECKS_FORK_SESSION:-}" ]; then
  args+=(--fork "$CHECKS_FORK_SESSION")
fi
if [ -n "${CHECKS_MAX_COST:-}" ]; then
  args+=(--max-cost "$CHECKS_MAX_COST")
fi
# At --max-turns the agent also gives a final answer and exits 3. 0 is off.
if [ -n "${CHECKS_MAX_TURNS:-}" ] && [ "$CHECKS_MAX_TURNS" != "0" ]; then
  args+=(--max-turns "$CHECKS_MAX_TURNS")
fi
# At --max-time the agent gives a final answer and exits 3 too. cn before
# 0.13.0 has no --max-time and would reject it, so look for it in the help
# first. (Not `cn --help | grep -q`: with pipefail, grep closing the pipe early
# fails the test.)
if [ -n "${CHECKS_MAX_TIME:-}" ]; then
  cn_help="$(cn --help 2>/dev/null || true)"
  case "$cn_help" in
    *"--max-time <"*) args+=(--max-time "$CHECKS_MAX_TIME") ;;
  esac
fi

# Not exec: the trap must remove the config file when cn exits.
set +e
cn "${args[@]}" "$(cat "$prompt_file")"
code=$?
set -e
exit "$code"
