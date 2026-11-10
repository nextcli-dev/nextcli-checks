#!/usr/bin/env bash
# Install the dependencies of the projects that hold the changed files, so the
# agent can read a library inside the repository instead of hunting for it in
# caches and system folders on the runner.
#
# Each project is restored in a throwaway container from its ecosystem's
# official image (see `checks.py deps-plan`). Nothing from the pull request
# runs, and nothing in it picks the program that does:
# - npm ci, and pnpm and Yarn 1 at versions this action pins, installed from
#   the public registry outside the project (no Corepack, no packageManager):
#   scripts off, no pnpmfile, no .yarnrc yarn-path or rc arguments, file:
#   directories linked, not packed. A lockfile with git dependencies is not
#   installed: packing one runs its prepare script.
# - pip from a requirements list checks.py rebuilds (no options, editable,
#   path, or URL lines), wheels only, run outside the project.
# - go mod download, and cargo fetch --locked run outside the project, so no
#   .cargo/config.toml or rust-toolchain.toml is read.
# NuGet, Maven, Gradle, and Yarn 2+ are not installed. The checkout is
# mounted read-only.
# Writable mounts are .checks-deps and, for JavaScript, the project's
# node_modules and those of its workspace packages. Each must be a plain
# directory of the checkout or not exist yet: a symlink committed in the pull
# request would otherwise make the mount, and the install, write outside it.
# No secrets, no runner files, no Docker socket.
#
# Writes /tmp/checks-deps.txt: ecosystem, location, status, reason per line,
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

plan="$(python3 "$script_dir/checks.py" deps-plan --root "$workspace" --changed /tmp/changed-files.txt)"
if [ -z "$plan" ]; then
  echo "No changed file belongs to a project with dependencies."
  exit 0
fi

# String or bool fields. argv and env are read separately so a value is never
# parsed by this shell.
json_get() {
  python3 -c 'import json,sys; value=json.loads(sys.argv[1]).get(sys.argv[2], ""); print("" if value is None else "true" if value is True else "false" if value is False else value)' "$1" "$2"
}

record() {
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$results"
}

# Reject a project directory the planner should already have dropped.
unsafe_dir() {
  case "$1" in
    "" ) return 1 ;;
    *[!A-Za-z0-9._/-]*|/*|*//*) return 0 ;;
    ..|../*|*/../*|*/..) return 0 ;;
  esac
  return 1
}

# A directory the install writes to, relative to the workspace, is unsafe when
# any part of the path is a symlink or not a directory, or when the pull
# request's tree has files in it. The path itself was checked by unsafe_dir.
unsafe_write_dir() {
  local rel="$1" path="$workspace" part
  local IFS=/
  for part in $rel; do
    path="$path/$part"
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
      return 0
    fi
  done
  if [ -n "$(git -C "$workspace" ls-files -- "$rel" 2>/dev/null | head -n 1)" ]; then
    return 0
  fi
  return 1
}

# Writes a .gitignore of "*" into a directory the container could write, so
# Search skips it. Run after the container exited: anything named .gitignore
# there is removed first, and the new file is created without following a
# symlink, so a link the container left can't redirect the write.
mark_ignored() {
  local rel="$1"
  if unsafe_write_dir "$rel" || [ ! -d "$workspace/$rel" ]; then
    return 0
  fi
  python3 -c '
import os, stat, sys
path = os.path.join(sys.argv[1], ".gitignore")
try:
    if stat.S_ISDIR(os.lstat(path).st_mode):
        sys.exit(0)
    os.unlink(path)
except FileNotFoundError:
    pass
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
os.write(fd, b"*\n")
os.close(fd)
' "$workspace/$rel" || true
}

prepared=""
prepare_workspace() {
  if [ -n "$prepared" ]; then
    [ "$prepared" = ok ]
    return
  fi
  if unsafe_write_dir "$DEPS_DIR"; then
    echo "::warning::${DEPS_DIR} in the pull request is not a plain directory. Dependencies are not installed."
    prepared=unsafe
    return 1
  fi
  mkdir -p "$workspace/$DEPS_DIR" "$cache"
  # Search (rg) skips ignored files, so installed packages don't flood results.
  mark_ignored "$DEPS_DIR"
  prepared=ok
}

DEPS_DIR=.checks-deps
docker_ok=true
if ! command -v "$docker" >/dev/null 2>&1; then
  docker_ok=false
fi
warned_docker=false
summary_lines=()

while IFS= read -r line || [ -n "$line" ]; do
  [ -z "$line" ] && continue
  action="$(json_get "$line" action)"
  ecosystem="$(json_get "$line" ecosystem)"
  dir="$(json_get "$line" dir)"
  deps="$(json_get "$line" deps)"
  project="."
  if [ -n "$dir" ]; then
    project="$dir"
  fi

  if [ "$action" = "skip" ]; then
    status="$(json_get "$line" status)"
    reason="$(json_get "$line" reason)"
    record "$ecosystem" "$deps" "$status" "$reason"
    if [ "$status" = "unsupported" ]; then
      summary_lines+=("| ${ecosystem} | — | not installed, would run project code |")
    elif [ "$reason" = "cap" ]; then
      summary_lines+=("| ${ecosystem} | \`${project}\` | skipped, past the project cap |")
    elif [ "$reason" = "no-time" ]; then
      summary_lines+=("| ${ecosystem} | \`${project}\` | skipped, no time left |")
    elif [ "$reason" = "layout" ]; then
      summary_lines+=("| ${ecosystem} | \`${project}\` | skipped, the install would write outside node_modules |")
    elif [ -n "$dir" ]; then
      summary_lines+=("| ${ecosystem} | \`${project}\` | skipped (${reason}) |")
    else
      summary_lines+=("| ${ecosystem} | — | skipped (${reason}) |")
    fi
    continue
  fi

  if [ "$dir" != "" ] && unsafe_dir "$dir"; then
    echo "::warning::Skipping ${ecosystem}: project path cannot be installed safely."
    record "$ecosystem" "" skipped unsafe-path
    summary_lines+=("| ${ecosystem} | — | skipped (unsafe-path) |")
    continue
  fi

  if [ "$docker_ok" != true ]; then
    if [ "$warned_docker" != true ]; then
      echo "::warning::Docker is not available. Dependencies are not installed."
      warned_docker=true
    fi
    record "$ecosystem" "$deps" failed no-docker
    summary_lines+=("| ${ecosystem} | \`${project}\` | failed, docker is not available |")
    continue
  fi

  left=$((deadline - SECONDS))
  if [ "$left" -lt 10 ]; then
    echo "::warning::No time left to install ${ecosystem} dependencies in ${project}."
    record "$ecosystem" "$deps" skipped no-time
    summary_lines+=("| ${ecosystem} | \`${project}\` | skipped, no time left |")
    continue
  fi

  image="$(json_get "$line" image)"
  prelude="$(json_get "$line" prelude)"
  mount_nm="$(json_get "$line" mount_node_modules)"
  tool="$(json_get "$line" tool)"
  cwd="$(json_get "$line" cwd)"
  note="$(json_get "$line" note)"
  # Fixed preludes. None takes text from the project; the command follows
  # as "$@".
  case "$prelude" in
    "")
      script='mkdir -p /tmp/home && exec "$@"'
      ;;
    tool)
      # The package manager, at the version in CHECKS_TOOL, from the public
      # registry. Installed from /tmp, so the project's .npmrc is not read.
      script='mkdir -p /tmp/home && (cd /tmp && npm install --global --prefix /tmp/home/tools --userconfig /dev/null --ignore-scripts --no-audit --no-fund --loglevel=error --registry https://registry.npmjs.org/ "$CHECKS_TOOL") && export PATH=/tmp/home/tools/bin:$PATH && exec "$@"'
      ;;
    go-cache)
      # Downloads go back to the action's cache, in GOPROXY layout.
      script='mkdir -p /tmp/home && "$@" && if [ -d "$GOMODCACHE/cache/download" ]; then mkdir -p /cache/go && cp -R "$GOMODCACHE/cache/download/." /cache/go/; fi'
      ;;
    cargo-cache)
      # Crates and the index come from the action's cache, and go back to it.
      script='mkdir -p /tmp/home "$CARGO_HOME" && if [ -d /cache/cargo ]; then cp -R /cache/cargo/. "$CARGO_HOME"/; fi && "$@" && for d in registry/cache registry/index git/db; do if [ -d "$CARGO_HOME/$d" ]; then mkdir -p "/cache/cargo/$d" && cp -R "$CARGO_HOME/$d/." "/cache/cargo/$d/"; fi; done'
      ;;
    *)
      echo "::warning::Unknown install prelude for ${ecosystem}. Dependencies in ${project} were not installed."
      record "$ecosystem" "$deps" failed prelude
      summary_lines+=("| ${ecosystem} | \`${project}\` | failed (unknown prelude) |")
      continue
      ;;
  esac

  argv=()
  while IFS= read -r -d '' arg; do
    argv+=("$arg")
  done < <(python3 -c 'import json,sys; data=json.loads(sys.argv[1]); items=[item.encode() for item in data.get("argv") or []]; sys.stdout.buffer.write(b"\0".join(items) + (b"\0" if items else b""))' "$line")
  env_args=(-e HOME=/tmp/home -e CI=1)
  while IFS= read -r -d '' pair; do
    [ -n "$pair" ] && env_args+=(-e "$pair")
  done < <(python3 -c 'import json,sys; data=json.loads(sys.argv[1]); items=[f"{key}={value}".encode() for key, value in (data.get("env") or {}).items()]; sys.stdout.buffer.write(b"\0".join(items) + (b"\0" if items else b""))' "$line")
  if [ "$prelude" = tool ]; then
    if ! [[ "$tool" =~ ^(pnpm@[0-9]+\.[0-9]+\.[0-9]+|yarn@1\.22\.22)$ ]]; then
      echo "::warning::Unknown package manager for ${ecosystem}. Dependencies in ${project} were not installed."
      record "$ecosystem" "$deps" failed tool
      summary_lines+=("| ${ecosystem} | \`${project}\` | failed (unknown package manager) |")
      continue
    fi
    env_args+=(-e "CHECKS_TOOL=${tool}")
  fi

  # Every directory the container may write, relative to the workspace.
  writable=()
  if [ "$mount_nm" = "true" ]; then
    if [ -n "$dir" ]; then
      writable+=("$dir/node_modules")
    else
      writable+=("node_modules")
    fi
    while IFS= read -r -d '' extra; do
      [ -n "$extra" ] && writable+=("$extra")
    done < <(python3 -c 'import json,sys; data=json.loads(sys.argv[1]); items=[item.encode() for item in data.get("extra_node_modules") or []]; sys.stdout.buffer.write(b"\0".join(items) + (b"\0" if items else b""))' "$line")
  fi
  unsafe=false
  for rel in ${writable[@]+"${writable[@]}"}; do
    if unsafe_dir "$rel" || unsafe_write_dir "$rel"; then
      unsafe=true
      break
    fi
  done
  if [ "$unsafe" = true ] || ! prepare_workspace; then
    echo "::warning::Skipping ${ecosystem} in ${project}: a directory it writes to is a symlink, a file, or part of the pull request."
    record "$ecosystem" "$deps" skipped unsafe-path
    summary_lines+=("| ${ecosystem} | \`${project}\` | skipped (unsafe-path) |")
    continue
  fi

  if [ "$cwd" = tmp ]; then
    # Outside the project: no project config file is found from here.
    workdir="/tmp"
  elif [ -n "$dir" ]; then
    workdir="/work/${dir}"
  else
    workdir="/work"
  fi
  mounts=(-v "$workspace:/work:ro" -v "$cache:/cache" -v "$workspace/$DEPS_DIR:/work/$DEPS_DIR")
  for rel in ${writable[@]+"${writable[@]}"}; do
    mkdir -p "$workspace/$rel"
    mounts+=(-v "$workspace/$rel:/work/$rel")
  done
  # Files the planner wrote for this step (pip's rebuilt requirements),
  # mounted read-only at /reqs.
  files_dir=""
  if python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("files") else 1)' "$line"; then
    files_dir="$(mktemp -d)"
    python3 -c '
import json, os, re, sys
files = json.loads(sys.argv[1]).get("files") or {}
for name, text in files.items():
    if not re.fullmatch(r"(requirements|constraints)\.txt", name):
        sys.exit("unexpected file name")
    with open(os.path.join(sys.argv[2], name), "w") as handle:
        handle.write(text)
' "$line" "$files_dir"
    chmod -R a+rX "$files_dir"
    mounts+=(-v "$files_dir:/reqs:ro")
  fi

  container="checks-deps-$$-${RANDOM}"
  started=$SECONDS
  echo "::group::Install ${ecosystem} dependencies in ${project} (${image})"
  set +e
  timeout --kill-after=10 "${left}s" \
    "$docker" run --rm --name "$container" \
      --user "$(id -u):$(id -g)" \
      --read-only \
      --cap-drop=ALL \
      --security-opt=no-new-privileges \
      --pids-limit=256 \
      --tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
      "${env_args[@]}" \
      "${mounts[@]}" \
      -w "$workdir" \
      --entrypoint sh \
      "$image" -c "$script" -- "${argv[@]}"
  code=$?
  set -e
  # timeout stops the client, not the container.
  "$docker" rm -f "$container" >/dev/null 2>&1 || true
  echo "::endgroup::"
  if [ -n "$files_dir" ]; then
    rm -rf "$files_dir"
  fi
  # Search skips what the container wrote.
  mark_ignored "$DEPS_DIR"
  for rel in ${writable[@]+"${writable[@]}"}; do
    mark_ignored "$rel"
  done

  took=$((SECONDS - started))
  if [ "$code" -eq 0 ]; then
    echo "Installed ${ecosystem} dependencies in ${project} in ${took} s."
    record "$ecosystem" "$deps" ok "$note"
    if [ "$note" = partial ]; then
      summary_lines+=("| ${ecosystem} | \`${project}\` | installed in ${took} s; editable, path, and URL requirements skipped |")
    else
      summary_lines+=("| ${ecosystem} | \`${project}\` | installed in ${took} s |")
    fi
  else
    echo "::warning::Could not install ${ecosystem} dependencies in ${project} (exit ${code}). The checks run without them."
    record "$ecosystem" "$deps" failed ""
    summary_lines+=("| ${ecosystem} | \`${project}\` | failed (exit ${code}) after ${took} s |")
  fi
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
