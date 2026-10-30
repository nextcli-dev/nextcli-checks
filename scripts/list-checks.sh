#!/usr/bin/env bash
# Print the check file stems in CHECKS_DIR as a JSON array.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/checks.py" list --dir "${CHECKS_DIR:-.checks}"
