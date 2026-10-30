#!/usr/bin/env bash
# Run one check with Claude Code. Only Anthropic-compatible endpoints work:
# anthropic, openrouter, and deepseek. Other providers exit 2.
set -euo pipefail

prompt_file="${1:?prompt file is required}"

if [ -z "${PROVIDER_API_KEY:-}" ]; then
  echo "PROVIDER_API_KEY is empty." >&2
  exit 2
fi
if [ -z "${CHECKS_PROVIDER:-}" ] || [ -z "${CHECKS_MODEL:-}" ]; then
  echo "provider and model are required." >&2
  exit 2
fi

unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL || true

case "$CHECKS_PROVIDER" in
  anthropic)
    export ANTHROPIC_API_KEY="$PROVIDER_API_KEY"
    if [ -n "${CHECKS_BASE_URL:-}" ]; then
      export ANTHROPIC_BASE_URL="$CHECKS_BASE_URL"
    fi
    ;;
  openrouter)
    export ANTHROPIC_AUTH_TOKEN="$PROVIDER_API_KEY"
    export ANTHROPIC_BASE_URL="${CHECKS_BASE_URL:-https://openrouter.ai/api}"
    ;;
  deepseek)
    export ANTHROPIC_AUTH_TOKEN="$PROVIDER_API_KEY"
    export ANTHROPIC_BASE_URL="${CHECKS_BASE_URL:-https://api.deepseek.com/anthropic}"
    ;;
  *)
    echo "claude-code only supports the anthropic, openrouter, and deepseek providers. '${CHECKS_PROVIDER}' does not speak the Anthropic messages API. Use agent opencode or nextcli for this provider." >&2
    exit 2
    ;;
esac

exec claude -p "$(cat "$prompt_file")" \
  --output-format json \
  --permission-mode plan \
  --model "$CHECKS_MODEL"
