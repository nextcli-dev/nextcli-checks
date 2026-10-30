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

The [composite action](./action.yml) runs every check on a pull request. Results appear as individual GitHub status checks. Passing checks complete silently. Failing checks post committable suggestion comments. The agent is read-only: it can read files and run a short list of read-only commands, and it cannot edit the checkout.

The action is not tied to one vendor. You pass a provider, a model, an API key, and an optional base URL. `agent` defaults to [opencode](https://opencode.ai). `nextcli` uses the NextCLI `cn` CLI. `claude-code` only supports Anthropic-compatible endpoints (`anthropic`, `openrouter`, and `deepseek`).

```yaml
name: AI Checks
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  checks:
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    timeout-minutes: 45
    permissions:
      contents: read
      pull-requests: write
      checks: write
    steps:
      - uses: nextcli-dev/nextcli-checks@v1
        with:
          provider: ${{ vars.CHECKS_PROVIDER }}
          model: ${{ vars.CHECKS_MODEL }}
          api-key: ${{ secrets.PROVIDER_API_KEY }}
          base-url: ${{ vars.CHECKS_BASE_URL }}
```

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

The [workflow in this repository](./.github/workflows/checks.yml) runs one job per check, with the agent install cached. Fork pull requests are skipped because they cannot read repository secrets. A check can set `model: provider/model` to override the model, and `paths` so it is reported as success without calling the model when no changed file matches.

Add `PROVIDER_API_KEY` under GitHub Settings > Secrets and variables > Actions. The provider, model, and base URL belong under Variables, not Secrets.

## Learn more

[spec.md](./spec.md) — Full spec: check file format, best practices, CI workflow, design principles, and problems you'll run into.

## License

[Apache 2.0](./LICENSE)