#!/usr/bin/env bash
# Run one check with opencode. The prompt file is $1. Stdout is the raw agent output.
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

export OPENCODE_DISABLE_AUTOUPDATE=1
OPENCODE_CONFIG_CONTENT="$(python3 "$script_dir/../checks.py" opencode-config)"
export OPENCODE_CONFIG_CONTENT

exec opencode run --format json --model "${CHECKS_PROVIDER}/${CHECKS_MODEL}" "$(cat "$prompt_file")"
