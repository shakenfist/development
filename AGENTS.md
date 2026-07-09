# Guidance for AI agents

This repository holds Shaken Fist development documentation, consistency
audit specifications, and the automation that enforces them. There is no
application code here.

## Adding or changing a consistency audit

An audit item touches several files, all of which must stay in sync:

1. `scripts/audit-check.py` -- add a `check_*()` function returning a
   dict with `id`, `status` (`pass` / `fail` / `not_applicable`) and
   `details`; register it in `run_all_checks()` and `CHECK_NAMES`.
2. `scripts/audit_common.py` -- add the check id to `AUDIT_METADATA`
   (spec file, optional template) and `ISSUE_TITLES`. This module is
   shared by `audit-manage-issues.py` and `audit-update-docs.py`.
3. `audits/<check-id>.md` -- the audit specification, following the
   structure documented in `audits/README.md`. Include an empty
   consistency-audit marker block under `## Projects`; the compliance
   table between the markers is regenerated daily by
   `scripts/audit-update-docs.py` and must not be edited by hand.
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
python3 scripts/audit-update-docs.py --results-dir /tmp/results/ --no-issues
```

Always use `--dry-run` for `audit-manage-issues.py` -- without it the
script creates and closes real GitHub issues. `audit-update-docs.py`
rewrites the tables in `audits/*.md` in place; discard the changes with
`git restore audits/` after testing (CI regenerates them from the full
repo matrix, so a locally generated table only covers the repos you fed
it).

## Code review tracking

Conventions for whole-codebase human review (weAudit, signed
commits, staleness pruning) live in `docs/code-review-tracking.md`;
the phased design is `docs/plans/PLAN-code-review-tracking.md`.
When implementing later phases, read the plan's analysis section
first -- several design constraints (sidecar rather than fields in
weAudit's JSON, prune locally rather than from CI) exist for
non-obvious verified reasons, and the plan's "Back brief" section
applies.

## Conventions

- Python: single quotes, no external dependencies in the audit scripts
  (stdlib plus the `git` and `gh` CLIs only).
- This repository is itself excluded from the consistency audits (it is
  internal tooling, listed in the exceptional cases in
  `PROJECT-CONSISTENCY-AUDITS.md`).
