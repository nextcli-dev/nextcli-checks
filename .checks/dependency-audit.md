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
