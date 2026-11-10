#!/usr/bin/env bash
# Install the dependencies of the projects that hold the changed files, so the
# agent can read a library inside the repository instead of hunting for it in
# caches and system folders on the runner.
#
# Each project is restored in a throwaway container from its ecosystem's
# official image (see `checks.py deps-plan`). A restore can run code from the
# pull request: dotnet restore targets, Gradle and setup.py scripts, and so on.
# So the container gets no secrets, no runner files and no Docker socket. It
# sees the workspace, with .git read-only, and a download cache. The checks
# themselves are read from a copy outside the workspace (see action.yml).
#
# Writes /tmp/checks-deps.txt: ecosystem, dependency location, status per line,
# which run-check.sh turns into the prompt's Dependencies section.
# A failed install is a warning, never a failed check.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="${CHECKS_INSTALL:-auto}"
results=/tmp/checks-deps.txt
: > "$results"

case "$mode" in
  none)
    echo "Dependency install is off (install: none)."
    exit 0
    ;;
  auto) ;;
  *)
    echo "::warning::install must be auto or none (got '${mode}'). Dependencies are not installed."
    exit 0
    ;;
esac

workspace="${GITHUB_WORKSPACE:-$PWD}"
docker="${CHECKS_DOCKER:-docker}"
cache="${CHECKS_DEPS_CACHE:-${RUNNER_TEMP:-/tmp}/checks-deps-cache}"
deadline=$((SECONDS + ${CHECKS_INSTALL_MINUTES:-6} * 60))

if [ ! -f /tmp/changed-files.txt ]; then
  echo "No list of changed files. Dependencies are not installed."
  exit 0
fi
if ! command -v "$docker" >/dev/null 2>&1; then
  echo "::warning::Docker is not available. Dependencies are not installed."
  exit 0
fi

plan="$(python3 "$script_dir/checks.py" deps-plan --root "$workspace" --changed /tmp/changed-files.txt)"
if [ -z "$plan" ]; then
  echo "No changed file belongs to a project with dependencies."
  exit 0
fi

mkdir -p "$workspace/.checks-deps" "$cache"
# Search (rg) skips ignored files, so installed packages don't flood results.
printf '*\n' > "$workspace/.checks-deps/.gitignore"

mounts=(-v "$workspace:/work" -v "$cache:/cache")
if [ -d "$workspace/.git" ]; then
  mounts+=(-v "$workspace/.git:/work/.git:ro")
fi

summary_lines=()
while IFS=$'\t' read -r ecosystem dir image deps env_list command; do
  left=$((deadline - SECONDS))
  if [ "$left" -lt 10 ]; then
    echo "::warning::No time left to install ${ecosystem} dependencies in ${dir}."
    printf '%s\t%s\t%s\n' "$ecosystem" "$deps" skipped >> "$results"
    summary_lines+=("| ${ecosystem} | \`${dir}\` | skipped, no time left |")
    continue
  fi

  env_args=(-e HOME=/tmp/home -e CI=1)
  for pair in $env_list; do
    env_args+=(-e "$pair")
  done
  container="checks-deps-$$-${RANDOM}"
  started=$SECONDS

  echo "::group::Install ${ecosystem} dependencies in ${dir} (${image})"
  set +e
  timeout --kill-after=10 "${left}s" \
    "$docker" run --rm --name "$container" \
      --user "$(id -u):$(id -g)" \
      "${env_args[@]}" \
      "${mounts[@]}" \
      -w "/work/${dir}" \
      --entrypoint sh \
      "$image" -c "mkdir -p /tmp/home && ${command}"
  code=$?
  set -e
  # timeout stops the client, not the container.
  "$docker" rm -f "$container" >/dev/null 2>&1 || true
  echo "::endgroup::"

  took=$((SECONDS - started))
  if [ "$code" -eq 0 ]; then
    status=ok
    echo "Installed ${ecosystem} dependencies in ${dir} in ${took} s."
    summary_lines+=("| ${ecosystem} | \`${dir}\` | installed in ${took} s |")
  else
    status=failed
    echo "::warning::Could not install ${ecosystem} dependencies in ${dir} (exit ${code}). The checks run without them."
    summary_lines+=("| ${ecosystem} | \`${dir}\` | failed (exit ${code}) after ${took} s |")
  fi
  printf '%s\t%s\t%s\n' "$ecosystem" "$deps" "$status" >> "$results"
done <<< "$plan"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ] && [ "${#summary_lines[@]}" -gt 0 ]; then
  {
    echo "### Dependencies"
    echo
    echo "| Ecosystem | Project | Result |"
    echo "|---|---|---|"
    printf '%s\n' "${summary_lines[@]}"
    echo
  } >> "$GITHUB_STEP_SUMMARY"
fi
