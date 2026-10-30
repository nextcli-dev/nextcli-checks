---
name: Documentation Freshness
description: Keep docs in sync when public APIs or configuration changes
---

Look for changes to public-facing APIs or configuration where the documentation wasn't updated:

- A public function, class, or type signature was added or changed — update the corresponding docs (README, docs/, or inline JSDoc/docstrings)
- Configuration options were added or renamed (config files, environment variables, CLI flags) — update the relevant documentation to reflect the new options
- API route paths or request/response shapes changed — update the API documentation

No changes needed if the PR doesn't touch public APIs or configuration, or if docs were already updated.
