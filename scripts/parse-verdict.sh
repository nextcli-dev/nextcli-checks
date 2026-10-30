#!/usr/bin/env bash
# Read agent output on stdin. Write a normalized verdict JSON object to stdout.
# Exit 1 when the output does not contain a valid verdict.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/checks.py" parse-verdict
