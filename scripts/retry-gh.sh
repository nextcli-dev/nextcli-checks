# Retry a gh API call that failed for a reason that passes:
# - 404: GitHub can answer 404 for a few seconds after a check run is created;
#   a later update of the same run succeeds.
# - 5xx: a server error.
# - 403/429 with "rate limit": a secondary rate limit.
# Any other failure (401, 422, ...) is returned at once.
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
    if [ "$attempt" -ge 5 ] || ! grep -qiE 'HTTP (404|5[0-9][0-9])|rate limit' "$err"; then
      rm -f "$err"
      return 1
    fi
    rm -f "$err"
    echo "GitHub call failed (attempt ${attempt}); retrying in ${delay}s." >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    if [ "$delay" != 0 ]; then
      delay=$((delay * 2))
    fi
  done
}
