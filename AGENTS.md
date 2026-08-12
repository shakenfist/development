# Guidance for AI agents

This repository holds Shaken Fist development documentation, consistency
audit specifications, and the automation that enforces them. There is no
application code here.

## Adding or changing a consistency audit

An audit item touches several files, all of which must stay in sync:

1. `scripts/audit-check.py` -- add a `check_*()` function returning a
   dict with `id`, `status` (`pass` / `fail` / `not_applicable`) and
   `details`; register it in `check_calls()` and `CHECK_NAMES`. The id
   written in `check_calls()` must be the id the function returns, and
   a test asserts it: the calls are deferred so a scoped repository can
   skip a check without running it, which means the table is what
   schedules the check, not the function itself.
2. `scripts/audit_common.py` -- add the check id to `AUDIT_METADATA`
   (spec file, optional template) and `ISSUE_TITLES`. This module is
   shared by `audit-manage-issues.py` and `audit-update-docs.py`.
3. `audits/<check-id>.md` -- the audit specification, following the
   structure documented in `audits/README.md`. Include an empty
   consistency-audit marker block under `## Projects`; the compliance
   table between the markers is regenerated daily by
   `scripts/audit-update-docs.py` and must not be edited by hand.
   A check may instead join an existing spec file (as the workflow
   standards checks do); in that case also add a column heading for
   the check id to `COLUMN_NAMES` in `scripts/audit-update-docs.py`.
4. `audits/README.md` -- add the new file to the audit index.
5. `PROJECT-CONSISTENCY-AUDITS.md` -- describe the expectation in prose;
   this is the authoritative human-readable specification.

Repo-specific exceptions (private repos, docs-only repos, non-Python
repos) live in `REPO_OVERRIDES` in `scripts/audit-check.py`.

To add a repository to the audits, add it to the matrix in
`.github/workflows/consistency-audit.yml` and to the in-scope list in
`audits/README.md`. Adding it subjects it to every check, and every
failure becomes an issue on the next run, so check first what it
would file:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/<repo> \
    --repo-name <repo> --github-org shakenfist
```

A repository that should be audited for some checks but not others
takes an `only_checks` list in `REPO_OVERRIDES` (private-ci is the
example: it is excluded from the conventions but does vendor sfui).
Checks outside the list are reported `not_applicable` with the reason,
never omitted -- `audit-update-docs.py` renders a check it cannot find
as `unknown`, and "we decided not to" should not read as "we did not
measure".

## Testing changes

This repo lints itself with pre-commit, holding to the same
actionlint/shellcheck/flake8 standard the audits require of audited
projects (even though `development` is exempt from the audits). Run it
before committing:

```
pre-commit run --all-files
```

The hooks are configured in `.pre-commit-config.yaml` (distinct from
`.pre-commit-hooks.yaml`, which is the review-stamp/review-prune hook
set this repo *provides* to other repositories). Python is wrapped at
120 characters, configured in `.flake8`; self-hosted runner labels are
declared in `.github/actionlint.yaml`.

The review tracking script has fixture-repo tests -- run them after any
change to `scripts/review-tracking.py`:

```
python3 scripts/test_review_tracking.py
```

The script is run by hand in target repositories (via a thin wrapper
like ryll's `tools/review-tracking.sh`), deliberately not from git
hooks. Two subcommands also run from CI in steady state: `prune`
from an adopting repo's `prune-reviews` workflow on pushes to main,
and `status` from the consistency audit's `review-coverage` check --
see `docs/code-review-tracking.md`.

The audit scripts have unit tests. Nothing runs them for you -- there is
no CI workflow in this repository other than the audit itself, and they
are not pre-commit hooks -- so run both after changing either script:

```
python3 scripts/test_audit_check.py
python3 scripts/test_audit_update_docs.py
```

They cover the invariants that span files, which are the ones that
break: that every check id scheduled in `check_calls()` is a real
check, and that every check sharing a spec file has a `COLUMN_NAMES`
heading. The second exists because its absence broke the 2026-08-12
run -- `review-marks-pre-commit` joined the workflow-standards spec
without a heading, and the rendering crashed after rewriting every
`audits/*.md` but before committing any, so the whole fleet's tables
silently stayed a day stale.

Also test by running the scripts against local clones:

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
the phased design is `docs/plans/PLAN-code-review-tracking.md`, and
the steady-state automation (CI pruning on main, the
`review-coverage` audit) is planned in `PLAN-review-coverage.md`.
When implementing later phases, read the plan's analysis section
first -- several design constraints (sidecar rather than fields in
weAudit's JSON, no stamping from CI) exist for non-obvious verified
reasons, and the plan's "Back brief" section applies. Note the
original "prune locally rather than from CI" constraint was about
developer clones and git hooks; CI pruning of a repo's own main
branch is the steady-state design, not a violation of it.

Deploying the tooling to a repository (and verifying a deployment,
including that expensive CI skips review-only PRs) is covered by the
`review-tracking-adoption` skill in `.claude/skills/` -- the CI-skip
check is deliberately skill-based rather than part of
`audit-check.py`, because classifying workflows as build CI versus
content scanner differs per project and takes judgment.

## Conventions

- Python: single quotes, no external dependencies in the audit scripts
  (stdlib plus the `git` and `gh` CLIs only).
- This repository is itself excluded from the consistency audits (it is
  internal tooling, listed in the exceptional cases in
  `PROJECT-CONSISTENCY-AUDITS.md`).
