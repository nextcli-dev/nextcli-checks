# AI Checks for Code Quality

This spec is maintained with [NextCLI Checks](https://github.com/nextcli-dev/nextcli-checks), a community continuation of the checks format from Continue. The check file format below is unchanged.

## The Problem

AI coding agents produce more code, faster. But velocity without quality control creates a compounding problem: bad patterns slip in, the next agent session reads them as the standard, and the codebase degrades from the inside.

Teams feel this. [Carnegie Mellon researchers found](https://arxiv.org/abs/2511.04427) that AI coding tools increase velocity but also increase static analysis warnings and code complexity. Open-source maintainers are [creating AI policies](https://redmonk.com/kholterhoff/2026/02/26/generative-ai-policy-landscape-in-open-source/) in response to quality issues with AI-generated submissions. A Brown CS professor published a [teardown of AI-generated code](https://gist.github.com/shriram/064756c61ca98774c2a509aa3893d941) for a bookstore app: floating-point arithmetic for money, custom CSV parsers instead of standard libraries, functions named `filterByTitle` that actually implement search. Each one minor. Together, a codebase that teaches the next agent session to write worse code.

Existing AI code review tools help, but teams don't trust them to catch the things that matter and ignore the things that don't. The tools produce general opinions — what teams need is enforcement of specific standards they've already decided matter.

## What Checks Are

A **check** is a markdown file describing a single standard. Each check runs as a full AI agent — not just scanning a diff, but reading files, running commands, and exercising judgment. If the check finds an issue, it fails with a suggested fix. If everything's clean, it passes silently.

Checks are semantic tests for things linters can't express:

- "Is this database migration safe to run on a 500M-row table?"
- "Does this PR update the API docs to reflect the endpoint changes it makes?"
- "Are there security issues in this auth flow that a linter wouldn't catch?"
- "Does this error handling follow the patterns established elsewhere in the codebase?"
- "Every new endpoint needs auth middleware."
- "Don't add dependencies without justification."

Because checks are plain English, anyone with a standard can write them, not just engineers.

## Checks vs. Tests vs. Linting

**Linting** handles formatting, style, and static patterns. Use ESLint, Prettier, Ruff, or equivalent. If a rule can be expressed as a pattern match on syntax, it belongs in a linter.

**Tests** verify correctness, behavior, and regressions. Use your test framework. If the question is "does this function return the right output for these inputs," write a test.

**Checks** handle judgment calls that require understanding context. They fill the gap between mechanical linting and human review. They're most valuable for questions that require reading multiple files, understanding intent, or applying project-specific conventions that aren't codified in a linter rule.

## What Checks Can Do

The agent running a check is not limited to reading the diff. It can:

- **Read files** in the repository beyond the diff, to understand surrounding context
- **Run commands** like `grep`, `find`, or custom scripts
- **Use a browser** to visit URLs, take screenshots, and verify rendered output
- **Access PR metadata** including file names, additions, deletions, and linked issues
- **Cross-reference** the diff against your database schema, API docs, or other artifacts

This means a check can verify that a new page renders correctly in a browser, or that a config change is reflected in the documentation.

## Check File Format

A check is a markdown file with YAML frontmatter and a markdown body. The frontmatter configures metadata. The body is the prompt the agent follows when evaluating a pull request.

```markdown
---
name: Migration Safety
description: Flag destructive database migrations
---

Your prompt here. This is the instruction the agent uses
when evaluating the pull request.
```

Check files live in a directory at the repository root, such as `.checks/` or `.agents/checks/`. The examples in this repo use `.checks/`. See the [`.checks/`](./.checks/) directory for ready-to-use check files you can copy into your project.

### Frontmatter Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Display name shown in status checks |
| `description` | Yes | string | Short description of what the check verifies |
| `model` | No | string | Model override as `provider/model`. The prefix must be `anthropic`, `openai`, `deepseek`, `openrouter`, or `openai-compatible`. The remainder is the model id, so `openrouter/anthropic/claude-sonnet-4-5` keeps the OpenRouter model id `anthropic/claude-sonnet-4-5`. |
| `paths` | No | list of globs | When set, the check runs only if a changed file matches. Otherwise it is reported as success and the model is not called. |

### Body

The markdown body after the frontmatter is the agent's prompt. It runs in the context of the PR being reviewed. The agent reads the prompt, examines the pull request diff, and produces a pass or fail verdict with reasoning.

Write concrete pass/fail criteria so the check produces consistent results.

### What You Don't Need to Write

The CI workflow automatically provides context to the agent:

- The full diff and list of changed files
- Instructions to only review changed lines
- Restrictions to not touch pre-existing issues in unchanged code

You don't need to include instructions like "review the changed files" or "only look at the diff" — that's handled by the wrapper. Focus your check on **what to look for and how to fix it**.

## Best Practices

### Scope Narrowly

Write one check per concern. A single check that tries to cover security, test coverage, and documentation will produce muddled results.

```markdown
---
name: Input Validation
description: Verify new API endpoints validate their inputs
---

Look for new API endpoints that accept user input without validation
(missing type checks, no schema validation, no length limits).
If found, add appropriate input validation.
```

### Be Specific About What to Look For

The prompt must describe exactly what the agent should look for and what it should do about it. Vague instructions produce vague results.

**Good:**

```
Look for new API endpoints that don't validate their inputs. Add schema validation using zod where missing.
```

**Bad:**

```
Check that the code is secure.
```

Structure criteria as a concrete list:

```markdown
Look for these issues and fix them:

- New REST endpoints missing request body validation
- Database queries using string interpolation instead of parameterized queries
- Error responses that expose stack traces or internal paths
```

### Add Early-Exit Conditions

If a check only applies to certain file types, say so upfront. This saves tokens and reduces false positives:

```markdown
If no migration files were added or changed, no action is needed.
```

### Use Prompts That Generate Checks

The fastest way to create checks tailored to your project is to have your coding agent write them. Describe what you want to enforce and the agent writes the check file using your actual frameworks, file structure, and conventions.

Some prompts to try:

**Security:**
```
Look at our codebase and write checks for the most likely security issues — auth bypasses, injection vectors, secrets in source, missing input validation. Tailor the checks to the frameworks and patterns we actually use.
```

**Recurring review feedback:**
```
Look through our recent PR review comments and identify things reviewers say repeatedly. Write checks that automate those recurring review comments.
```

**Best practices:**
```
Analyze our codebase and write checks for the patterns that matter most — error handling conventions, logging standards, naming conventions, or whatever you see us being inconsistent about.
```

**Test coverage:**
```
Write a check that verifies new code has corresponding tests, following the test file naming and structure conventions already in our project.
```

## Example Checks

Complete check files you can use directly. These are generic starting points — checks generated by your coding agent will be more specific to your project.

Each example below is also available as a standalone file in the [`.checks/`](./.checks/) directory, ready to copy into your repo:

- [`.checks/security-review.md`](./.checks/security-review.md) — Catch hardcoded secrets, injection vectors, and missing input validation
- [`.checks/test-coverage.md`](./.checks/test-coverage.md) — Ensure new code has corresponding tests
- [`.checks/documentation-freshness.md`](./.checks/documentation-freshness.md) — Keep docs in sync when public APIs or configuration changes
- [`.checks/dependency-audit.md`](./.checks/dependency-audit.md) — Review new or updated dependencies for risk
- [`.checks/migration-safety.md`](./.checks/migration-safety.md) — Catch destructive database migrations

### Security Review

```markdown
---
name: Security Review
description: Catch hardcoded secrets, injection vectors, and missing input validation
---

Look for these security issues and fix them:

- Hardcoded API keys, tokens, passwords, or connection strings — move them to environment variables
- New API endpoints or request handlers without input validation — add appropriate validation
- SQL queries built with string concatenation or template literals — convert to parameterized queries
- Sensitive data (passwords, tokens, PII) written to logs or stdout — remove or redact the sensitive fields
- New cryptographic code using weak algorithms (MD5, SHA1 for security purposes, ECB mode) — replace with stronger alternatives
- Secrets added to files that are not in `.gitignore` — remove them and add the files to `.gitignore`
```

### Test Coverage

```markdown
---
name: Test Coverage
description: Ensure new code has corresponding tests
---

Look for new code that's missing tests:

- A new source file was added without a corresponding test file (e.g., `utils.ts` added but no `utils.test.ts`) — create a test file with basic coverage for the exported functions
- New exported functions or public methods with no tests exercising them — add tests covering the main code paths
- An existing test file was deleted without the corresponding source file also being deleted — flag this for review

No changes needed if:

- All new source files have corresponding test files
- New functions/methods are already covered by tests
- Only test files, docs, or config files were changed
```

### Documentation Freshness

```markdown
---
name: Documentation Freshness
description: Keep docs in sync when public APIs or configuration changes
---

Look for changes to public-facing APIs or configuration where the documentation wasn't updated:

- A public function, class, or type signature was added or changed. Update the corresponding docs (README, docs/, or inline JSDoc/docstrings)
- Configuration options were added or renamed (config files, environment variables, CLI flags). Update the relevant documentation to reflect the new options
- API route paths or request/response shapes changed. Update the API documentation

No changes needed if the PR doesn't touch public APIs or configuration, or if docs were already updated.
```

### Dependency Audit

```markdown
---
name: Dependency Audit
description: Review new or updated dependencies for risk
---

If no dependency files (`package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, or similar) were changed, no action is needed.

When dependencies were added or updated, look for these issues:

- A new dependency that appears unmaintained (no commits in the last 12 months) — flag for review and suggest well-maintained alternatives
- A dependency was bumped by more than one major version (e.g., v2.x to v4.x) without a note in the PR description — add a comment explaining the jump
- A known deprecated or security-flagged package was added — replace with the recommended alternative
- A dependency that duplicates functionality from an existing dependency — remove and use the existing one instead
- Dev dependencies in production dependency sections (e.g., a test framework in `dependencies` instead of `devDependencies`) — move to the correct section
```

### Migration Safety

```markdown
---
name: Migration Safety
description: Catch destructive database migrations
paths:
  - "**/migrations/**"
  - "**/migration/**"
  - "**/*migration*"
  - "**/*.sql"
---

If no migration files were added or changed, no action is needed.

When migrations are present, look for these issues:

- `DROP TABLE` or `DROP COLUMN` without a preceding migration that backs up or migrates the data — add a data migration step or split into separate migrations
- Column type narrowing (e.g., `TEXT` to `VARCHAR(50)`, `BIGINT` to `INT`) without a backfill step — add a backfill or guard against data truncation
- `NOT NULL` constraint added to an existing column without a `DEFAULT` value — add a default or a data backfill migration
- Renaming a column or table that is referenced by application code without updating that code in the same PR — update the references
- A destructive and a constructive change in the same migration file — split into separate migrations for safe rollback
```

## Running Checks Locally

Checks start local. You write a check, run it against your current branch with your coding agent, read the output, refine. The feedback loop is: edit the markdown, run the check, see the result, iterate. No infrastructure required. If your agent can read your repo, it can run your checks.

Add check-running to your agent instructions (e.g. `AGENTS.md`) so checks run as part of the standard workflow:

```markdown
After creating or updating a pull request, run all checks in `.checks/` and fix any failures before requesting review.
```

### Iterating on a Check

The local feedback loop is the fastest way to improve checks:

1. Write or edit the check markdown file
2. Run the check against your current branch
3. Read the output. Did it catch what it should? Did it flag things it shouldn't?
4. Refine the prompt and repeat

Because checks run inside your coding agent, you can iterate in conversation:

```
The migration safety check is flagging additive-only migrations as failures. Update the check to only flag destructive operations.
```

## Running Checks in CI

Tests started local too. Developers ran them on their machines and trusted each other to do the same. That stopped working for every reason that applies to checks now: "works on my machine" divergence, the honor system (people forget, skip, rush), and silent drift.

Martin Fowler named this in his original CI article: the result has to be visible to the whole team, immediately. A red build means everyone knows. Checks need the same visibility on the PR, before merge, before the problem compounds.

The goal: run every check file as a full AI agent on every PR. Report results as native GitHub status checks. Suggest fixes when checks fail.

### The Architecture

You need five things:

1. **Trigger**: PR opened, updated, or reopened
2. **Discovery**: find all check files in the checks directory
3. **Agent runtime**: run each check as a read-only AI agent with the PR diff as context. The runtime is pluggable (`opencode` by default, `nextcli`, or `claude-code`) and is configured with a provider, model, API key, and optional base URL rather than a single vendor's environment variable.
4. **Verdict parser**: extract pass/fail and reasoning from agent output
5. **Status reporter**: post results as GitHub status checks

### Workflow

The runner is the composite action in [`action.yml`](./action.yml). A caller passes `provider`, `model`, `api-key` (the `PROVIDER_API_KEY` secret), and an optional `base-url`. Named providers are `anthropic`, `openai`, `deepseek`, and `openrouter`. `openai-compatible` is any OpenAI-compatible gateway (oneprovider.dev, LiteLLM, vLLM) and requires `base-url`.

`agent` defaults to opencode. `nextcli` writes a temporary NextCLI `config.yaml` (see `scripts/agents/nextcli.sh`) and runs `cn -p --readonly --config <file>`. `claude-code` only supports Anthropic-compatible endpoints (`anthropic`, `openrouter`, and `deepseek`) and fails that check with a clear error for any other provider.

The workflow in [`.github/workflows/checks.yml`](./.github/workflows/checks.yml) is the parallel form: one job per check file, with the agent install cached. A one-step workflow that runs every check in a single job is in the README. Both post individual status checks and committable suggestion comments when a check fails.

When a check passes, it completes silently — no comment noise. When a check fails with actionable suggestions, it posts a pull request review with GitHub suggestion comments that can be committed directly from the PR UI. Each suggestion comment is labeled with the check name that produced it. If a check fails without suggestions (or the review can't be posted), it falls back to a plain PR comment.

> **Note:** Because check runs are created by the GitHub Actions app, the "Details" link on each check always points to the Actions workflow log — not the review comment. GitHub locks `details_url` for its own app's check runs. To control where the "Details" link points, you'd need to register a custom GitHub App that creates the check runs under its own identity, which allows setting `details_url` to any URL.

### What This Gets You

- Individual GitHub status checks per check file (green/red on the PR)
- Committable suggestion comments on failures (applied directly from the PR UI)
- Silent passes. No comment noise when checks pass
- Artifact storage for debugging agent reasoning
- Concurrency control to cancel stale runs
- A provider-neutral configuration: `PROVIDER_API_KEY` plus provider, model, and base URL variables
- Parallel runs (one job per check) or a single job that runs every check
- Path filters that skip the model call when a check cannot apply, while still reporting success so a required check does not stay pending

### What This Doesn't Solve

- **Sequential execution in the one-step workflow**: the README workflow runs checks one after another. The workflow in this repository runs one job per check. The agent install is cached, so the extra jobs are cheap, but each job still pays for its own model call.
- **Non-determinism**: the same check can produce different results on the same diff. No fix, only mitigation.
- **No session continuity**: you can't follow up with the agent that made a judgment call. Save artifacts and use local runs for iteration.
- **No feedback loop**: no built-in mechanism to track which checks produce accepted vs. rejected suggestions. Track manually.
- **Cost**: each check is a full agent session. Start with few checks and monitor spend.
- **Check detail links**: clicking a check name links to the Actions workflow log, not the review comment. This is a GitHub platform limitation for check runs created by the GitHub Actions app. A custom GitHub App would allow controlling the `details_url`.

## Problems You'll Run Into

Building the v1 is straightforward. Here's what happens after that.

### Non-Determinism

The same check on the same diff can produce different results across runs. Temperature 0 doesn't eliminate it because tool call ordering and reasoning paths vary. GitHub's own [Agentic Workflows documentation](https://github.blog/ai-and-ml/automate-repository-tasks-with-github-agentic-workflows/) warns: "CI/CD needs to be deterministic, whereas agentic workflows are not."

**Mitigation:** Accept that checks are probabilistic, not deterministic. Track flakiness rates per check. If a check is flaky more than ~5% of the time, the prompt needs to be more specific. A verification step (running the check twice and comparing results) can reduce false positives at the cost of doubled latency and tokens.

### Sequential Execution Is Slow

Nine checks at 1-2 minutes each = 9-18 minutes per PR push. Parallel execution within a single runner requires managing background processes, stdout routing, and API rate limits. A matrix strategy (one job per check) means nine npm installs, nine fresh VMs, and nine separate API calls.

**Mitigation:** Start with fewer, high-value checks. Parallelize with a matrix strategy if cost is acceptable. Use concurrency groups to cancel stale runs.

### Structured Output Parsing

The agent doesn't always produce valid JSON. Malformed output, missing closing braces, unescaped quotes. You'll add JSON validation, a fallback parser, and retry-on-parse-failure. The parsing logic grows from 2 lines to 20.

**Mitigation:** Use structured output modes if your agent runtime supports them. Define a simple, flat output schema. Treat parse failures as errors, not silent passes.

### Cost

Each check is a full agent session: reads files, processes a diff, reasons about subjective quality. At typical API pricing, $0.10-0.50 per check. Nine checks per push, 30 pushes per day: $27-135/day. No way to skip inapplicable checks without a pre-filter that reads the diff first, which costs its own tokens.

**Mitigation:** Start with 3-5 high-value checks. Add early-exit conditions in check prompts ("If no migration files were added or changed, no action is needed"). Monitor cost per check and prune checks with low acceptance rates.

### Surfacing Suggested Fixes

When a check fails with suggestions, the workflow posts a pull request review with GitHub suggestion comments. These are committable directly from the PR UI: one click to accept a fix. The agent returns structured suggestions with file, line range, and replacement code; the workflow constructs a review payload with ````suggestion` blocks and posts it via the GitHub API.

Edge cases to watch: suggestions on lines not in the diff will be rejected by GitHub (the workflow handles this gracefully). Accepting a suggestion creates a new commit, which triggers `synchronize`, which reruns all checks. If rerun loops become a problem, add commit-author detection to skip runs triggered by `github-actions[bot]`.

### Session Continuity

When a check fails and you want to understand why, the agent's reasoning is a terminated process on a recycled VM. You can't ask "why did you flag line 42? That's intentional." You can't refine the check in conversation with the agent that ran it. The session is gone.

**Mitigation:** Save full agent output as artifacts. Accept that CI checks won't have session continuity — use local check runs for iterative refinement.

### The Feedback Loop That Isn't

Writing checks is easy. Keeping them good is the hard part. Your codebase evolves. Conventions shift. A check calibrated in January produces false positives in March because the codebase moved and the check didn't.

There's no built-in mechanism to answer: which checks do humans override? Which produce accepted fixes? Which cry wolf? Without this data, checks fossilize. They either produce noise until someone turns them off, or miss things until something ships that shouldn't have.

**Mitigation:** Track acceptance/rejection rates manually. Review check results weekly. Delete or rewrite checks that consistently produce rejected suggestions. Test check changes against recent PRs before merging.

### Credential and provider management

Each repository (or organization) configures four values:

| Name | Kind | Purpose |
| --- | --- | --- |
| `PROVIDER_API_KEY` | Secret | Sent to the agent as `PROVIDER_API_KEY`. The action does not read `ANTHROPIC_API_KEY` or any other vendor-specific variable. |
| `CHECKS_PROVIDER` | Variable | `anthropic`, `openai`, `deepseek`, `openrouter`, or `openai-compatible` |
| `CHECKS_MODEL` | Variable | Model id, such as `deepseek-v4-pro` |
| `CHECKS_BASE_URL` | Variable | Required for `openai-compatible`. Optional override for a named provider. |

`gh secret set PROVIDER_API_KEY` prompts for the key, so the key is not stored in shell history. Do not pipe the key into the command with `echo`.

One repo, one API key, is a small amount of setup. Ten repos means duplicating secrets or using organization secrets and variables with repository access policies. The same provider settings work for every agent adapter. `claude-code` is the exception: it can only call Anthropic-compatible endpoints, so an OpenAI or `openai-compatible` provider has to use `opencode` or `nextcli`.

**Mitigation:** Use organization secrets and variables with repository access policies. Rotate keys on a schedule. Give the workflow token only `contents: read`, `pull-requests: write`, and `checks: write`.

### Local/CI Parity

`npm test` locally gives the same result as in CI. Checks don't. The local agent has different prompt construction, different context window management, different tool access than the CI version. A check passes locally, fails in CI.

**Mitigation:** Use the same agent runtime and prompt construction locally and in CI. Document known differences. Accept that exact parity is not achievable — local runs are for iteration, CI runs are the source of truth.

## What Success Looks Like

The primary signal: **suggested fix acceptance rate**. Acceptance means the developer got value. Rejection means the check was noisy or the suggestion was wrong.

Supporting signals:

- **Adoption:** checks per repo, creation and deletion rate over time
- **Retention:** checks still active after 30/60/90 days
- **Quality:** pass rate per check over time, fewer reverts and post-merge hotfixes
- **Noise:** rejection rate per check — high rejection means the check needs refinement

## Design Principles

1. **One check per concern.** A single check that tries to cover security, test coverage, and documentation will produce muddled results. Split it.

2. **Concrete pass/fail criteria.** Vague instructions produce vague results. "Check that the code is secure" will fail. "Flag new API endpoints without input validation" will work.

3. **Checks are standards you've already decided matter.** Don't use checks for opinions. Use them for things the team has agreed on. If there's no consensus, there shouldn't be a check.

4. **Start small.** Begin with your most painful recurring review comment — the thing reviewers say on every other PR. Automate that. See it catch something real. Then add more.

5. **Iterate through use.** The best checks are written quickly and refined through feedback. Don't try to write the perfect check. Write a good-enough check, run it, see what it catches and what it misses, and adjust.
