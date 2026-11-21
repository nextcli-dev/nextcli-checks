---
name: checks-debug
description: Debug a nextcli-checks run that errored, timed out, hit a limit or gave a wrong verdict. Use when an AI-checks job or its PR comment looks wrong.
---

# Debug a checks run

Paths are relative to the nextcli-checks repo root. `action.yml` documents
every input; `scripts/run-check.sh` is the per-check flow. The examples use
the `nextcli` repository, whose `.github/workflows/ai-checks.yml` calls the
reusable workflow with `agent: nextcli`, `max-cost` 2 and
`timeout-per-check` 25.

## 1. Find the run and the job

```bash
gh run list -R <owner/repo> --workflow ai-checks.yml --limit 10
gh run view <run-id> -R <owner/repo>                 # one job per check
gh run view <run-id> -R <owner/repo> --log-failed | tail -200
```

The job log shows the agent install step (which `@nextcli/cli` version ran,
or the `nextcli-ref` it built) and each `::notice::` the scripts print about
retries and limits.

If `gh` isn't authenticated or a command fails, say which command and what it
printed, and work from artifacts the user saved, or stop. Don't look for
credentials or read outside the repository.

## 2. Download what the check left

```bash
gh run download <run-id> -R <owner/repo> -n check-results-<check> -D <scratch dir>
```

Kept for 7 days, already redacted:

- `check-<name>.verdict.json`: the verdict, its summary and whether it is
  complete;
- `check-<name>.err`: the agent's stderr (limit messages, errors);
- `check-<name>.calls.jsonl`: one line per model call (`NEXTCLI_TRACE=basic`):
  tokens, cost, timings, finish reason;
- `check-<name>.session.json`: the agent's session, with each tool call and
  what it returned.

## 3. Match the symptom

- **ERROR "timed out".** The agent exited 124. `timeout-per-check` covers the
  first run and its retry together; `cn` gets `--max-time` of the time left
  minus a minute. Look at the call timings for one slow call or many calls.
- **INCOMPLETE / partial.** The agent stopped at `--max-cost` or `--max-turns`
  (exit 3) and its verdict was kept. Sum `cost` in `calls.jsonl`. A retry only
  gets what the first run left of the budget (`checks.py retry-budget`), and
  starts only with a third of the time left.
- **ERROR about provider or configuration.** Exit 2. Check the `provider`,
  `model`, `base-url` inputs and the repository variables they come from.
- **A finding about code that is there.** Compare `gh pr diff <N> | wc -c`
  with `max-diff-bytes` (default 100000). The agent saw only
  `/tmp/pr-truncated.diff`.
- **The check followed the PR's instructions.** `.checks/*.md` come from the
  base commit (sparse checkout in `action.yml`), and
  `scripts/neutralize-pr-config.sh` moves `.claude/`, `.env*`, `.mcp.json`
  and `.continue` settings out of the checkout. `AGENTS.md` and always-on
  `.continue/rules` still load from the PR head: report that as a finding.
- **No PR comment.** The comment is posted by a later job; the caller's
  workflow may have a safety-net job that posts it from the artifacts.

## 4. Reproduce

Run the same check locally with the same `cn` version on the PR branch, or
re-run the one job (`gh run rerun <run-id> --job <job-id>`) once the cause is
fixed.

## Example

"The security-review check on PR 214 says ERROR."

- Cause: timed out (exit 124) after 25 minutes. `calls.jsonl` shows 3 calls,
  the last one 19 minutes long with `finish_reason: length`.
- Not the diff (31,204 bytes, under `max-diff-bytes`) and not cost ($0.41).
- Change: lower `max-output-tokens` for this check, or use a faster model.
  A re-run costs a full check.

## Stop when

You can name the cause with its evidence (log line, artifact field, input
value) and the change it needs: in the check's markdown, the action's inputs,
this repository's scripts, or `cn` itself. If you suggest a re-run, say it
costs a full check.
