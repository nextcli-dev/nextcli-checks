# Retry a gh API call. GitHub can answer 404 for a few seconds after a check
# run is created; a later update of the same run succeeds.
retry_gh() {
  local attempt=1
  local delay="${CHECKS_GH_RETRY_DELAY:-1}"
  local err
  while true; do
    err="$(mktemp)"
    if "$@" 2>"$err"; then
      rm -f "$err"
      return 0
    fi
    cat "$err" >&2
    if [ "$attempt" -ge 5 ] || ! grep -q 'HTTP 404' "$err"; then
      rm -f "$err"
      return 1
    fi
    rm -f "$err"
    echo "GitHub returned 404 (attempt ${attempt}); retrying in ${delay}s." >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    if [ "$delay" != 0 ]; then
      delay=$((delay * 2))
    fi
  done
}
