# Checks

NextCLI Checks is a community continuation of [Continue's checks](https://github.com/continuedev/checks). This repository is [nextcli-dev/nextcli-checks](https://github.com/nextcli-dev/nextcli-checks).

A check is a markdown file describing a single code quality standard. Each check runs as a full AI agent on pull requests: reading files, running commands, and exercising judgment to catch issues that linters and tests can't express.

```markdown
---
name: Migration Safety
description: Flag destructive database migrations
---

If no migration files were added or changed, no action is needed.

When migrations are present, look for these issues:

- `DROP TABLE` or `DROP COLUMN` without a preceding data migration — add a backup step
- `NOT NULL` constraint added without a `DEFAULT` value — add a default or backfill
- A destructive and constructive change in the same migration — split for safe rollback
```

## Checks vs. tests vs. linting

**Linting** catches formatting and static patterns. If it can be a regex, it's a lint rule.

**Tests** verify behavior. If you can assert an input/output, write a test.

**Checks** handle judgment calls that require context, reading multiple files, understanding intent, applying conventions that aren't codified in a linter rule.

## Quick start

Install the skill:

```
npx skills add nextcli-dev/nextcli-checks
```

Then run the following skill in your preferred coding agent:

```
/setup-checks
```

The agent will analyze your project, create tailored check files in `.checks/`, and optionally set up CI.

## Example checks

Ready-to-use check files you can copy into your project:

| Check                                                           | Description                                                              |
| --------------------------------------------------------------- | ------------------------------------------------------------------------ |
| [Security Review](./.checks/security-review.md)                 | Catch hardcoded secrets, injection vectors, and missing input validation |
| [Test Coverage](./.checks/test-coverage.md)                     | Ensure new code has corresponding tests                                  |
| [Documentation Freshness](./.checks/documentation-freshness.md) | Keep docs in sync when public APIs or configuration changes              |
| [Dependency Audit](./.checks/dependency-audit.md)               | Review new or updated dependencies for risk                              |
| [Migration Safety](./.checks/migration-safety.md)               | Catch destructive database migrations                                    |

These are generic starting points. For best results, use the quickstart above to have your coding agent generate checks tailored to your project's specific frameworks, conventions, and pain points.

## Run locally

Just ask your coding agent to run the checks against your current diff.

Add this to your `AGENTS.md` (or `CLAUDE.md`, `COPILOT.md`) so your checks run automatically:

```markdown
Before opening a pull request, run all checks in `.checks/` against the current diff and fix any failures.
```

## Run in CI

Call the reusable workflow. It lists the checks, then runs each one as its own job, in parallel. Each check reports as a check run named `check/<name>`. Passing checks complete silently. Failing checks post committable suggestion comments. The agent is read-only: it can read files and run a short list of read-only commands, and it cannot edit the checkout.

```yaml
name: AI Checks
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  ai-checks:
    uses: nextcli-dev/nextcli-checks/.github/workflows/checks.yml@v2
    permissions:
      contents: read
      pull-requests: write
      checks: write
    with:
      provider: ${{ vars.CHECKS_PROVIDER }}
      model: ${{ vars.CHECKS_MODEL }}
      base-url: ${{ vars.CHECKS_BASE_URL }}
    secrets:
      api-key: ${{ secrets.PROVIDER_API_KEY }}
```

To run checks on demand instead, trigger on a label and pass the pull request number for manual runs:

```yaml
on:
  pull_request:
    types: [labeled]
  workflow_dispatch:
    inputs:
      pr:
        description: Pull request number
        required: true
jobs:
  ai-checks:
    if: github.event_name == 'workflow_dispatch' || github.event.label.name == 'ai-review'
    uses: nextcli-dev/nextcli-checks/.github/workflows/checks.yml@v2
    # permissions, with and secrets as above, plus:
    #   pr-number: ${{ inputs.pr }}
```

How it runs:

- Check definitions are read from the pull request's **base** commit. A pull request cannot change or delete the checks that review it, and a pull request that adds `.checks/` gets checked only after it merges.
- Pull requests from forks get no repository secrets, so the checks are skipped. `workflow_dispatch` with `pr-number` runs them on a fork's code with your secrets, so only do that for code you have read.
- The agent's environment has the provider key and no GitHub or Actions tokens. The key and the GitHub token are removed from everything posted to the pull request or uploaded as an artifact.
- A check run left unfinished by a cancelled or timed-out job is completed as `neutral`. Re-running a check dismisses its previous review, so a fixed failure stops requesting changes.

The workflow is not tied to one vendor. You pass a provider, a model, an API key, and an optional base URL. `agent` defaults to [opencode](https://opencode.ai). `nextcli` uses the NextCLI `cn` CLI. `claude-code` only supports Anthropic-compatible endpoints (`anthropic`, `openrouter`, and `deepseek`).

| Provider | `provider` | `base-url` |
| --- | --- | --- |
| Anthropic | `anthropic` | optional |
| OpenAI | `openai` | optional |
| DeepSeek | `deepseek` | optional |
| OpenRouter | `openrouter` | optional |
| oneprovider.dev, LiteLLM, vLLM, or any other OpenAI-compatible gateway | `openai-compatible` | required, for example `https://api.oneprovider.dev/v1` |

Set the non-secret values as repository variables and the key as a secret. `gh secret set` prompts for the key, so it is not stored in shell history:

```bash
gh variable set CHECKS_PROVIDER --repo owner/repo --body "openai-compatible"
gh variable set CHECKS_MODEL --repo owner/repo --body "deepseek-v4-pro"
gh variable set CHECKS_BASE_URL --repo owner/repo --body "https://api.oneprovider.dev/v1"
gh secret set PROVIDER_API_KEY --repo owner/repo
```

Leave `base-url` empty for a named provider unless you need a gateway. Set `CHECKS_AGENT` and pass `agent: ${{ vars.CHECKS_AGENT }}` only when you are not using the default `opencode`.

### Inputs

The reusable workflow takes `provider`, `model`, `agent`, `base-url`, `checks-dir`, `pr-number`, `timeout-per-check` and `on-error`, and the `api-key` secret. To use any other input below, call the action directly, `nextcli-dev/nextcli-checks@v2`, in your own job. The action runs every check in one job, or one check with `check`; with `mode: list` it only outputs the check names as a JSON array, for your own matrix.

| Input | Default | Description |
| --- | --- | --- |
| `agent` | `opencode` | Agent CLI that runs each check: `opencode`, `nextcli`, or `claude-code`. |
| `agent-version` | empty | Version of the agent CLI to install. Defaults to `opencode-ai` 1.19.5, `@anthropic-ai/claude-code` 2.1.312, or `@nextcli/cli` 0.1.1. An installed `cn` at another version is replaced. |
| `nextcli-ref` | empty | Branch, tag, or commit SHA of NextCLI to build from source when `agent` is `nextcli`. When set, `agent-version` is ignored for `nextcli`. |
| `nextcli-repository` | `https://github.com/nextcli-dev/nextcli.git` | Repository cloned when `nextcli-ref` is set. |
| `timeout-per-check` | `10` | Minutes allowed for each check before it is reported as an error. |
| `max-diff-bytes` | `100000` | Maximum number of PR diff bytes passed to the agent. |
| `on-error` | `neutral` | Check-run conclusion when the agent output cannot be parsed: `neutral` or `failure`. |
| `check` | empty | Run only this check (the file name without `.md`). |
| `checks-dir` | `.checks` | Directory of check markdown files, relative to the repository root, read from the base commit. |
| `pr-number` | event's PR | Pull request to check. Required when the workflow is not triggered by a pull request. |
| `mode` | `run` | `run` runs and reports checks. `list` only sets the `checks` output. |

The `nextcli` agent installs `@nextcli/cli` from npm, pinned to `agent-version`. To run unreleased NextCLI code instead, set `nextcli-ref` and the action builds `cn` from source. The build adds several minutes to each job:

```yaml
      - uses: nextcli-dev/nextcli-checks@v2
        with:
          agent: nextcli
          nextcli-ref: main   # optional: a branch, tag, or commit SHA
          provider: ${{ vars.CHECKS_PROVIDER }}
          model: ${{ vars.CHECKS_MODEL }}
          api-key: ${{ secrets.PROVIDER_API_KEY }}
```

A check can set `model: provider/model` to override the model, and `paths` so it is reported as success without calling the model when no changed file matches.

Add `PROVIDER_API_KEY` under GitHub Settings > Secrets and variables > Actions. The provider, model, and base URL belong under Variables, not Secrets.

## Learn more

[spec.md](./spec.md) — Full spec: check file format, best practices, CI workflow, design principles, and problems you'll run into.

## License

[Apache 2.0](./LICENSE)