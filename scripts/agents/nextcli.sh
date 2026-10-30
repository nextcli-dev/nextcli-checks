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

exec cn -p --format json --readonly --config "$config" "$(cat "$prompt_file")"
