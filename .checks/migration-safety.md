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
