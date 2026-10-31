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
    if command -v cn >/dev/null 2>&1; then
      cn --version
      exit 0
    fi
    if [ -n "${CHECKS_NEXTCLI_REF:-}" ]; then
      repo="${CHECKS_NEXTCLI_REPOSITORY:-https://github.com/nextcli-dev/nextcli.git}"
      rm -rf /tmp/nextcli-src
      git clone --depth 1 --branch "$CHECKS_NEXTCLI_REF" "$repo" /tmp/nextcli-src
      (
        cd /tmp/nextcli-src/extensions/cli
        npm run build:local-deps
        npm run build
      )
      install_dir="${HOME}/.local/bin"
      mkdir -p "$install_dir"
      ln -sfn /tmp/nextcli-src/extensions/cli/dist/cn.js "${install_dir}/cn"
      chmod +x /tmp/nextcli-src/extensions/cli/dist/cn.js
      if [ -n "${GITHUB_PATH:-}" ]; then
        echo "$install_dir" >> "$GITHUB_PATH"
      fi
      export PATH="${install_dir}:${PATH}"
      cn --version
    else
      if ! npm install -g @nextcli/cli; then
        echo "The NextCLI CLI is not published to npm yet. Pass nextcli-ref to build it from source, or set agent to opencode." >&2
        exit 1
      fi
      cn --version
    fi
    ;;
  *)
    echo "Unknown agent '${agent}'. Use opencode, nextcli, or claude-code." >&2
    exit 1
    ;;
esac
