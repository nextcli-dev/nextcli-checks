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
