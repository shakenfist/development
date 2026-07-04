# Guidance for AI agents

This repository holds Shaken Fist development documentation, consistency
audit specifications, and the automation that enforces them. There is no
application code here.

## Adding or changing a consistency audit

An audit item touches several files, all of which must stay in sync:

1. `scripts/audit-check.py` -- add a `check_*()` function returning a
   dict with `id`, `status` (`pass` / `fail` / `not_applicable`) and
   `details`; register it in `run_all_checks()` and `CHECK_NAMES`.
2. `scripts/audit-manage-issues.py` -- add the check id to
   `AUDIT_METADATA` (spec file, optional template) and `ISSUE_TITLES`.
3. `audits/<check-id>.md` -- the audit specification, following the
   structure documented in `audits/README.md`.
4. `audits/README.md` -- add the new file to the audit index.
5. `PROJECT-CONSISTENCY-AUDITS.md` -- describe the expectation in prose;
   this is the authoritative human-readable specification.

Repo-specific exceptions (private repos, docs-only repos, non-Python
repos) live in `REPO_OVERRIDES` in `scripts/audit-check.py`.

To add a repository to the audits, add it to the matrix in
`.github/workflows/consistency-audit.yml` and to the in-scope list in
`audits/README.md`.

## Testing changes

The audit scripts have no unit tests. Test by running them against local
clones:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/<repo> \
    --repo-name <repo> > /tmp/audit-result-<repo>.json
python3 scripts/audit-manage-issues.py --results-dir /tmp/results/ --dry-run
```

Always use `--dry-run` for `audit-manage-issues.py` -- without it the
script creates and closes real GitHub issues.

## Conventions

- Python: single quotes, no external dependencies in the audit scripts
  (stdlib plus the `git` and `gh` CLIs only).
- This repository is itself excluded from the consistency audits (it is
  internal tooling, listed in the exceptional cases in
  `PROJECT-CONSISTENCY-AUDITS.md`).
