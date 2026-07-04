# Architecture

This repository is the coordination point for keeping all Shaken Fist
projects consistent. It contains no application code.

## Layout

- `PROJECT-CONSISTENCY-AUDITS.md` -- the authoritative prose
  specification of everything we expect from a Shaken Fist project,
  including the list of excluded repositories.
- `audits/` -- one machine-oriented specification file per audit
  criterion, each with a per-project compliance table. `audits/README.md`
  holds the index and the in-scope project list.
- `scripts/` -- the automation that enforces the audits (see below).
- `templates/` -- canonical starting points (workflows, configs) for
  rolling infrastructure out to projects.
- `docs/` -- longer-form documentation of the automation systems.
- `.github/workflows/consistency-audit.yml` -- the daily audit workflow.

## The consistency audit pipeline

The daily `consistency-audit.yml` workflow runs a matrix job per target
repository:

1. Each matrix job shallow-clones the target repo and runs
   `scripts/audit-check.py` against it, producing a JSON result artifact
   (`audit-result-<repo>.json`). Checks are file-based where possible;
   a few (default branch, security settings) query the GitHub API via
   `gh`, and the git-hygiene checks shell out to `git` in the clone.
2. A follow-up job downloads all result artifacts and runs
   `scripts/audit-manage-issues.py`, which creates a GitHub issue
   (labelled `consistency`) on each non-compliant target repo for each
   failing check, and closes issues for checks that now pass or no
   longer apply. Issue titles are `Consistency: <check name>` and are
   used as the idempotency key, so titles must remain stable.

Both scripts are stdlib-only Python; the only external dependencies are
the `git` and `gh` CLIs available on the self-hosted runners.

Repo properties that cannot be detected from a clone (private repos,
docs-only repos, repos where Python is incidental) are hardcoded in
`REPO_OVERRIDES` in `scripts/audit-check.py`.
