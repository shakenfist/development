# Architecture

This repository is the coordination point for keeping all Shaken Fist
projects consistent. It contains no application code.

## Layout

- `PROJECT-CONSISTENCY-AUDITS.md` -- the authoritative prose
  specification of everything we expect from a Shaken Fist project,
  including the list of excluded repositories.
- `audits/` -- one machine-oriented specification file per audit
  criterion, each with a per-project compliance table regenerated daily
  by the audit workflow (between the consistency-audit markers).
  `audits/README.md` holds the index and the in-scope project list.
- `scripts/` -- the automation that enforces the audits (see below).
- `templates/` -- canonical starting points (workflows, configs) for
  rolling infrastructure out to projects.
- `docs/` -- longer-form documentation of the automation systems.
- `tools/` -- the trusted-checkout helpers the pull request automation
  runs (`address-comments-with-claude.sh`, `render-review.py` and the
  schema it validates against), copied from
  `templates/ci-review-automation/`.
- `.github/workflows/consistency-audit.yml` -- the daily audit workflow.
- `.github/workflows/ci.yml` -- this repository's own pull request
  checks: the pre-commit gate, a smoke run of the audit, and the
  automated reviewer gated on both.
- `.github/workflows/` also carries the fleet-standard supporting
  workflows: the three `pr-*.yml` bot triggers, `secret-scan.yml`,
  `codeql-analysis.yml`, `renovate.yml` and `export-repo-config.yml`.

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
   used as the idempotency key, so titles must remain stable. Repo
   names are resolved to their canonical form first (GitHub issue
   search does not follow repo renames, while issue creation does, so
   a stale matrix entry would otherwise file a fresh duplicate on
   every run); a rename still fails the job so the matrix gets
   updated. If duplicate issues exist anyway, the oldest is kept and
   the rest are closed automatically.
3. A final job runs `scripts/audit-update-docs.py`, which regenerates
   the per-project compliance tables between the consistency-audit
   markers in `audits/*.md` from the same results (linking the open
   `consistency` issues), then commits and pushes any changes back to
   `main` via `scripts/commit-audit-docs.sh`. The tables are therefore
   always a rendering of the latest audit run, never hand-maintained.

The shared check-to-spec-file mapping and issue title conventions live
in `scripts/audit_common.py`. All the scripts are stdlib-only Python;
the only external dependencies are the `git` and `gh` CLIs available on
the self-hosted runners.

Repo properties that cannot be detected from a clone (docs-only repos,
repos where Python is incidental) are hardcoded in `REPO_OVERRIDES` in
`scripts/audit-check.py`. Repository visibility is queried live from
the GitHub API rather than hardcoded, because it changes over time.

Audit scope is otherwise all-or-nothing per repository -- in the
matrix means every check applies -- except where a repository carries
an `only_checks` override, which narrows it to the listed check ids.
The remaining checks are not run at all and are reported
`not_applicable`, which matters because several checks query the
GitHub API and some of those queries fail on a private repository for
reasons unrelated to compliance. `private-ci` uses this to be audited
for its vendored sfui copy and nothing else.

## Review tracking automation

This repository is also the home of the whole-codebase review
tracking system: conventions in `docs/code-review-tracking.md`,
design in `docs/plans/PLAN-code-review-tracking.md`, steady state
in `docs/plans/PLAN-review-coverage.md`. The automation is
`scripts/review-tracking.py` (stamp reviews with blob SHAs, prune
stale reviews when files change, regenerate the per-repo
`REVIEWS.md`, pick the next file to review, report effective
coverage against HEAD), run by hand in target repositories via a
thin wrapper (for example ryll's `tools/review-tracking.sh`) --
deliberately not from git hooks. In steady state two subcommands
also run from CI: adopting repos prune stale marks on every push
to main via a `prune-reviews` workflow, and the daily consistency
audit's `review-coverage` check alerts (via a GitHub issue) when
five or more in-scope files need review. Tests are in
`scripts/test_review_tracking.py`.

## Testing the automation

The unit tests (`scripts/test_audit_check.py`,
`scripts/test_audit_update_docs.py`, `scripts/test_review_tracking.py`
and `scripts/test_check_audit_smoke.py`) run as `local` pre-commit
hooks, and `ci.yml` runs the same `pre-commit run --all-files` on every
pull request. Until `ci.yml` existed the hooks were the only gate
between an edit and the 06:00 UTC run, and only in a clone where
somebody had installed them.

`ci.yml` also runs `audit-check.py` against this repository, which is
the part linting cannot do: the scheduled workflow's runtime
assumptions -- that skillsaw installs, and that it lands somewhere
`audit-check.py` can invoke it -- are only exercised by running it. A
bare `pip install` meeting PEP 668 stopped the fleet's audits for a day
in August 2026 without any file in the repository being wrong. The
smoke job asserts the audit measured rather than that it approved,
because `llm-context-lint` renders a missing skillsaw as
`not_applicable`, which is the same word the audit uses for a
deliberate exemption.
They concentrate on invariants that span files -- a check id
scheduled somewhere it is not defined, a check sharing a spec file
without a column heading -- because those are the failures a single
file review does not catch, and because the audit's own failure mode
is unattended: it rewrites every table before it discovers it cannot
render one, so a small omission publishes nothing at all.
