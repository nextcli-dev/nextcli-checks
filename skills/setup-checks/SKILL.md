---
name: setup-checks
description: Creates tailored pull-request review checks as markdown. Use when adding checks that need judgment. Do not use for lint rules or unit tests.
version: 1.0.0
metadata:
  author: NextCLI contributors <nextcli-dev@users.noreply.github.com>
  tags:
    - code-review
    - pull-requests
    - checks
---

# Set Up AI Checks

These checks come from [NextCLI Checks](https://github.com/nextcli-dev/nextcli-checks). Install them with `npx skills@1.7.4 add nextcli-dev/nextcli-checks`.

You are helping a developer set up **AI Checks**: markdown files that define code quality standards. Each check runs as an agent on every pull request. The agent can read files and run read-only commands. If it finds something, it fails the pull request with a suggested fix. Otherwise it passes silently.

## Purpose

Set up `.checks/` for one repository: write checks that match this codebase, try one locally, and optionally add the GitHub Actions workflow.

## Prerequisites

- A git repository, and permission to push a branch.
- The `gh` CLI, authenticated, when you want pull request history or the CI step. Skip those parts when `gh` is missing.
- For CI: a GitHub repository, and a provider API key the user will store as `PROVIDER_API_KEY`. You never see or type the key.

## Limitations

- Checks are not a linter or a test suite. Do not add a check for a rule ESLint, Ruff, a type checker, or an existing CI step already enforces.
- CI runs only on pull requests from the same repository. Fork pull requests cannot read the API key secret.
- The `claude-code` agent cannot call OpenAI or a generic OpenAI-compatible gateway. Use `opencode` or `nextcli` for those providers.
- A check can be wrong. The user should read the first results and edit the markdown.

The shell commands below are bash.

## Instructions

Complete the steps below in order:

- Run the git checks and stop when the directory is not a repository.
- Add only checks that need judgment.
- Set the provider, model, and base URL with the user before writing the workflow.
- Mark each task complete before starting the next one.

## Examples

A finished check file looks like this:

```markdown
---
name: Migration Safety
description: Catch destructive database migrations
paths:
  - "**/migrations/**"
  - "**/*.sql"
---

If no migration files changed, no action is needed.

When migrations are present, flag a `DROP COLUMN` that has no preceding data migration.
```

## Step 1: Orient

Welcome the user in one or two sentences:

> We're going to set up AI checks for your repo. I'll explore your codebase, write checks tailored to your project, and get you set up to run them.

Then create a todo list for the session:

1. Explore codebase and write checks
2. Try a check
3. Set up CI (optional)
4. Commit and open a pull request

Mark task 1 in progress.

## Step 2: Git setup

1. Verify this is a git repo: `git rev-parse --git-dir 2>/dev/null`. Stop if it is not.
2. Check for uncommitted changes: `git status --porcelain`

If the worktree is dirty, ask:

> You have uncommitted changes. I'd like to stash them and create a new branch. OK?

If approved:

```bash
git rev-parse --abbrev-ref HEAD
git stash push -m "Stashing before checks setup"
git checkout -b add-checks
```

Remember the original branch and whether you stashed. Restore both in the last step.

## Step 3: Explore and write checks

**Speed target: under 60 seconds. Stay shallow. Surface patterns, don't read entire files.**

Run these in parallel where possible.

### Codebase scan

- Project structure, languages, frameworks, and build system
- Linters, formatters, and type checkers already in use
- Testing framework and patterns
- CI workflows
- Contributing guidelines (CONTRIBUTING.md, AGENTS.md, CLAUDE.md)

### Pull request review history

Skip this if the `gh` CLI is not available.

```bash
gh pr list --state merged --limit 30 --json number,title
gh api repos/{owner}/{repo}/pulls/{number}/comments --jq '.[] | {body: .body, path: .path}'
```

Use 3 to 5 recent pull requests that have review comments. Look for what reviewers flag, what linters miss, and which areas get the most attention.

### Propose checks

Share 3 to 5 bullet findings, then propose exactly **3 checks** and let the user accept or reject each one (all selected by default). **Never create a check for something a deterministic tool already enforces** (linter, type checker, or CI step). Checks are for judgment: slop, drifting conventions, security anti-patterns, and poor taste. Each proposed check must be specific to this project.

### Write check files

Create `.checks/` and write one `.md` file per accepted check:

```markdown
---
name: Human-Readable Check Name
description: One-sentence description.
---

# Check Name

## Context

Why this check exists. Reference specific project patterns, configs, or review comments you found.

## What to Check

### 1. Category

Concrete instructions with file paths and GOOD/BAD examples from this codebase.

## Key Files

The files most relevant to this check.

## Exclusions

Cases where this check does not apply.
```

Keep file names lowercase with hyphens, for example error-handling.md.

Two frontmatter fields are optional:

- `model`: a `provider/model` override for this check only. The prefix must be `anthropic`, `openai`, `deepseek`, `openrouter`, or `openai-compatible`. Example: `openrouter/anthropic/claude-sonnet-4-5`.
- `paths`: a list of globs. When no changed file matches, the check is skipped and reported as success, so no model tokens are spent. Example: `paths: ["**/migrations/**", "**/*.sql"]`.

### Update agent instructions

Append this section to the project's agent instructions file. Prefer AGENTS.md. Use CLAUDE.md or COPILOT.md when that is the file this repo already uses. Create AGENTS.md when none of them exist.

```markdown
## Checks

Before opening a pull request, run all checks in `.checks/` against your current diff and fix any failures before requesting review.
```

Mark task 1 complete.

## Step 4: Try a check

Mark task 2 in progress. Ask which check to try, and present the checks as options. Once the user picks one:

1. Read the selected check file from `.checks/`.
2. Read the relevant source files, not only a diff.
3. For each finding, state the severity (Error, Warning, or Info), the file and line, what is wrong, and how to fix it.
4. Report PASS when nothing is wrong. Report FAIL and list the findings when something is wrong.
5. If it fails, offer to fix the issues.

Mark task 2 complete.

## Step 5: CI setup (optional)

Mark task 3 in progress. Ask:

> Would you like to run these checks on every pull request in GitHub Actions? I'll add a workflow that calls the NextCLI Checks action. You choose the provider and model, then add the API key as a repository secret.

If they say no, mark task 3 complete and go to Step 6.

If they say yes, ask which provider, model, and agent to use:

- **provider** (required): `anthropic`, `openai`, `deepseek`, `openrouter`, or `openai-compatible`
- **model** (required): the model id, such as `claude-sonnet-4-5` or `deepseek-v4-pro`
- **base URL**: required for `openai-compatible` (an OpenAI-compatible gateway root, for example `https://gateway.example.com/v1`). Optional for the named providers.
- **agent**: `opencode` (default), `nextcli`, or `claude-code`. `claude-code` only supports `anthropic`, `openrouter`, and `deepseek`. If they pick `claude-code` with another provider, say so and ask them to pick again.

Create the workflow file checks.yml in the repository's GitHub workflows directory:

```yaml
name: AI Checks

on:
  pull_request:
    types: [opened, synchronize, reopened]

concurrency:
  group: ai-checks-${{ github.event.pull_request.number }}
  cancel-in-progress: true

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

When the agent is not `opencode`, add `agent: ${{ vars.CHECKS_AGENT }}` under `with:`. For `nextcli`, also add `nextcli-ref: main` until `@nextcli/cli` is published to npm. The action then builds `cn` from source, which adds several minutes to each job.

The action installs the agent, runs each file in `.checks/` as a read-only agent, and posts one GitHub check run per file. A passing check is silent. A failing check opens a review with committable suggestion comments. Fork pull requests are skipped because they cannot read repository secrets.

To run one job per check instead of one job for all of them, copy the matrix workflow from [nextcli-dev/nextcli-checks](https://github.com/nextcli-dev/nextcli-checks/blob/master/.github/workflows/checks.yml). Pass `check` set to the matrix value.

Tell the user to run the following in their terminal. Fill in `<owner>/<repo>` and the values they chose. **Do not run the secret command yourself. Do not put the API key in the command, in the workflow, or in chat.**

```bash
gh variable set CHECKS_PROVIDER --repo <owner>/<repo> --body "<provider>"
gh variable set CHECKS_MODEL --repo <owner>/<repo> --body "<model>"
gh variable set CHECKS_BASE_URL --repo <owner>/<repo> --body "<base-url>"
gh variable set CHECKS_AGENT --repo <owner>/<repo> --body "<agent>"
gh secret set PROVIDER_API_KEY --repo <owner>/<repo>
```

Omit `CHECKS_BASE_URL` when they did not set a base URL. Omit `CHECKS_AGENT` when they kept the default `opencode`. `gh secret set` prompts for the key, so the key is not written to shell history.

Get `<owner>/<repo>` from `gh repo view --json nameWithOwner --jq .nameWithOwner`.

Mark task 3 complete.

## Step 6: Commit and open a pull request

Mark task 4 in progress.

```bash
git add .checks/
find .github/workflows -name checks.yml -exec git add {} +
git add AGENTS.md CLAUDE.md COPILOT.md 2>/dev/null || true
git commit -m "feat: add checks for automated pull request review

Checks:
- [list each check name]"
git push origin HEAD
gh pr create --title "Add checks for automated pull request review" --body "Adds AI-powered code review checks that run on every pull request.

## Checks added

[list each check with a one-line description]

## How it works

Each check is a markdown file in \`.checks/\`. In CI they run through the NextCLI Checks action, which calls a read-only agent and posts a GitHub status check per file."
```

If you stashed changes earlier, restore them:

```bash
git checkout <original-branch>
git stash pop
```

Mark task 4 complete. Share the pull request URL.

## Troubleshooting

| Error | Cause | What to do |
| --- | --- | --- |
| `git rev-parse` fails | This directory is not a git repository | Stop. Ask the user to run this inside a clone. |
| `gh` is not found or not logged in | The GitHub CLI is missing or has no token | Skip review history and print the CI commands for the user to run later. |
| The user picks `claude-code` with `openai` or `openai-compatible` | That agent only speaks the Anthropic API | Ask them to pick `opencode` or `nextcli`, or to switch the provider to `anthropic`, `openrouter`, or `deepseek`. |
| `The NextCLI CLI is not published to npm yet` or `cn is not on PATH` | The `nextcli` agent installs `@nextcli/cli` from npm, which is not published yet | Add `nextcli-ref: main` under `with:` so the action builds `cn` from source, or switch the agent to `opencode`. |
| CI does not run after merge | `CHECKS_PROVIDER` or `PROVIDER_API_KEY` was never set | Have the user run the `gh variable set` and `gh secret set` commands from Step 5. |
| A check fails on code the pull request did not touch | The check has no `paths` filter, or the prompt is too broad | Add `paths` or an early-exit line to the check file. |
