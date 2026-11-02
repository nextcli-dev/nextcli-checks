#!/usr/bin/env bash
# Install the selected agent CLI. Empty CHECKS_AGENT means opencode.
set -euo pipefail

agent="${CHECKS_AGENT:-opencode}"
version="${CHECKS_AGENT_VERSION:-}"

case "$agent" in
  opencode)
    version="${version:-1.19.5}"
    npm install -g "opencode-ai@${version}"
    opencode --version
    ;;
  claude-code)
    version="${version:-2.1.312}"
    npm install -g "@anthropic-ai/claude-code@${version}"
    claude --version
    ;;
  nextcli)
    if [ -n "${CHECKS_NEXTCLI_REF:-}" ]; then
      # Build from source. git fetch (unlike git clone --branch) also accepts a commit SHA.
      ref="$CHECKS_NEXTCLI_REF"
      repo="${CHECKS_NEXTCLI_REPOSITORY:-https://github.com/nextcli-dev/nextcli.git}"
      src_dir="${CHECKS_NEXTCLI_SRC_DIR:-/tmp/nextcli-src}"
      rm -rf "$src_dir"
      mkdir -p "$src_dir"
      git -C "$src_dir" init --quiet
      git -C "$src_dir" remote add origin "$repo"
      git -C "$src_dir" fetch --depth 1 origin "$ref"
      git -C "$src_dir" checkout --quiet FETCH_HEAD
      (
        cd "$src_dir/extensions/cli"
        npm run build:local-deps
        npm run build
      )
      install_dir="${HOME}/.local/bin"
      mkdir -p "$install_dir"
      ln -sfn "$src_dir/extensions/cli/dist/cn.js" "${install_dir}/cn"
      chmod +x "$src_dir/extensions/cli/dist/cn.js"
      if [ -n "${GITHUB_PATH:-}" ]; then
        echo "$install_dir" >> "$GITHUB_PATH"
      fi
      export PATH="${install_dir}:${PATH}"
      cn --version
      exit 0
    fi
    # Bump this default with each @nextcli/cli release. 0.1.0 is deprecated (interactive mode exits at startup).
    version="${version:-0.1.1}"
    if command -v cn >/dev/null 2>&1; then
      installed="$(cn --version 2>/dev/null | tr -d '[:space:]' || true)"
      if [ "$installed" = "$version" ]; then
        echo "$installed"
        exit 0
      fi
      echo "cn ${installed:-unknown} is installed; installing @nextcli/cli@${version}." >&2
    fi
    if ! npm install -g "@nextcli/cli@${version}"; then
      echo "Could not install @nextcli/cli@${version}. Check that the version exists (npm view @nextcli/cli versions), pass nextcli-ref to build from source, or set agent to opencode." >&2
      exit 1
    fi
    cn --version
    ;;
  *)
    echo "Unknown agent '${agent}'. Use opencode, nextcli, or claude-code." >&2
    exit 1
    ;;
esac
