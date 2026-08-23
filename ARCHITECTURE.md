# Architecture

This repository is the coordination point for keeping all Shaken Fist
projects consistent. It contains no application code.

## Layout

- `PROJECT-CONSISTENCY-AUDITS.md` -- the authoritative prose
  specification of everything we expect from a Shaken Fist project,
  including the list of excluded repositories.
- `scripts/` -- the automation that enforces the audits (see below).
- `templates/` -- canonical starting points (workflows, configs) for
  rolling infrastructure out to projects.
- `docs/` -- longer-form documentation of the automation systems, and
  `docs/audits/`: one specification file per audit criterion, each with
  a per-project compliance table regenerated daily by the audit
  workflow (between the consistency-audit markers).
  `docs/audits/README.md` holds the index and the in-scope project
  list. The specifications live under `docs/` so that they publish to
  shakenfist.com with everything else: what we hold a project to is
  documentation, and a criterion nobody outside the fleet can read is
  a criterion nobody outside the fleet can meet.
- `tools/` -- the review tracking wrappers: `review-tracking.sh` for
  local use, and `ci-prune-reviews.sh` which `prune-reviews.yml` runs
  on every push to main. The pull request automation's helpers used to
  live here too; they went with the retired comment addresser, and what
  the reviewer still needs ships inside
  `shakenfist/actions/review-pr-with-claude`.
- `.github/workflows/consistency-audit.yml` -- the daily audit workflow.
- `.github/workflows/ci.yml` -- this repository's own pull request
  checks: the pre-commit gate, a smoke run of the audit, and the
  automated reviewer gated on both.
- `.github/workflows/` also carries the fleet-standard supporting
  workflows: the `pr-re-review.yml` and `pr-retest.yml` bot triggers,
  `secret-scan.yml`, `codeql-analysis.yml`, `renovate.yml` and
  `export-repo-config.yml`.

## The consistency audit pipeline

The daily `consistency-audit.yml` workflow turns "are the projects
consistent" into a measurement, in four stages:

1. A matrix job per target repository shallow-clones it and runs
   `scripts/audit-check.py`, producing an `audit-result-<repo>.json`
   artifact. Checks are file-based where possible; a few query the
   GitHub API through `gh`, and the git-hygiene checks shell out to
   `git` in the clone.
2. `scripts/audit-manage-issues.py` reads every artifact and files a
   `consistency`-labelled issue on each target repository for each
   failing check, closing issues for checks that now pass or no longer
   apply. Issue titles are the idempotency key.
3. `scripts/audit-update-docs.py` regenerates the compliance tables in
   `docs/audits/*.md` from the same results, and
   `scripts/commit-audit-docs.sh` pushes them back to `main`. The
   tables are a rendering of the latest run, never hand-maintained.
4. On failure, a reporting job files or updates an `audit-failure`
   issue here -- because while the pipeline is down the tables keep
   showing yesterday's verdicts, so a broken audit looks like a
   healthy one.

Data flows one way: a clone produces JSON, JSON produces issues and
tables. Nothing reads the tables back, which is why they can be
regenerated wholesale.

The shared check-to-spec mapping and issue title conventions live in
`scripts/audit_common.py`. All the scripts are stdlib-only Python; the
only external dependencies are the `git` and `gh` CLIs on the
self-hosted runners, plus a pinned `skillsaw` for one check.

Repo properties that cannot be detected from a clone are declared in
`REPO_OVERRIDES` in `scripts/audit-check.py`, which is also where a
repository is narrowed to a subset of checks. Scope is otherwise
all-or-nothing: in the matrix means every check applies.

`docs/consistency-audits.md` documents all of this in working detail --
adding a criterion, bringing a repository into scope, and testing a
change before it reaches the fleet.

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

The four test suites under `scripts/` run as `local` pre-commit hooks,
and `ci.yml` runs `pre-commit run --all-files` on every pull request.
Until `ci.yml` existed the hooks were the only gate between an edit and
the 06:00 UTC run, and only in a clone where somebody had installed
them.

`ci.yml` also runs `audit-check.py` against this repository, which is
the part linting cannot do: the scheduled workflow's runtime
assumptions are only exercised by running it. A bare `pip install`
meeting PEP 668 stopped the fleet's audits for a day in August 2026
without any file in the repository being wrong.

The tests concentrate on invariants that span files, because those are
the failures a single file review does not catch, and because the audit
runs unattended: a 2026-08 run rewrote every table before discovering it
could not render one, and published none of them. Rendering now warns
and falls back rather than raising, and a test catches the omission
first. See `docs/consistency-audits.md`.
