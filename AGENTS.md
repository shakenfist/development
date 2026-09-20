# Guidance for AI agents

This repository holds Shaken Fist development documentation, consistency
audit specifications, and the automation that enforces them. There is no
application code here.

## Working on the consistency audits

`docs/consistency-audits.md` is the reference: what a daily run does,
how to add a criterion, how to bring a repository into scope, and how
to test a change before it reaches the fleet. Read it before changing
anything under `scripts/` or `docs/audits/`.

The parts worth knowing before you start:

- A criterion is a `Check` subclass in `scripts/audit/checks/`,
  registered in `scripts/audit/registry.py`, plus its specification
  under `docs/audits/`. The metadata tables are views over the
  registry, so there is nothing to keep in sync by hand.
- The compliance tables live in `docs/audits/compliance.md` between
  the `consistency-audit` markers, and are regenerated and pushed by
  the daily workflow. Never edit them by hand, and never add a table
  or a marker to a criterion spec: the specs are hand-written so they
  can hold a human review mark.
- Issue titles are the idempotency key for filing and closing, so a
  check's `issue_title` is an interface. Renaming one orphans every
  open issue for that check fleet-wide;
  `scripts/tests/test_metadata.py` freezes all of them for that
  reason.
- A check that does not apply must report `not_applicable` with a
  reason rather than being omitted; an omitted check renders as
  `unknown`.
- Always pass `--dry-run` to `audit-manage-issues.py` when running it
  by hand. Without it, it files and closes real issues fleet-wide.

Repo-specific exceptions live in `REPO_OVERRIDES` in
`scripts/audit/repo.py`.

## Testing changes

This repo lints *and tests* itself with pre-commit, holding to the same
actionlint/shellcheck/flake8 standard the audits require of audited
projects -- and, since it is in the audit matrix, is measured against
every other standard here too. One command covers everything, and
`ci.yml` runs the same command on every pull request -- run it before
committing:

```
pre-commit run --all-files
```

The hooks are configured in `.pre-commit-config.yaml`. Python is
wrapped at 120 characters, configured in `.flake8`; self-hosted runner
labels are declared in `.github/actionlint.yaml`.

`git commit` only runs the hooks in a clone where they are installed,
which is per clone and not carried in the repository:

```
pre-commit install
```

Worth doing rather than relying on remembering: CI will catch it on
the pull request, but the local run is faster and quieter.

The individual test suites, and how to exercise a check against a real
repository, are in `docs/consistency-audits.md`. So is the
`CheckTestCase` convention a check's own test follows -- see
*Testing a change* there.

`pre-commit` does not look at diagrams. Mermaid fails at render time,
so a broken diagram commits cleanly and breaks a page instead: run
`tools/mermaid-lint.sh` after editing one. It needs docker, which is
why it is not a hook and why `mermaid-lint.yml` runs on a
docker-capable runner rather than `static`. Diagrams of structure or
flow are mermaid rather than ASCII here, enforced by the
`diagram-format` audit; `templates/shared-blocks/diagram-discipline.md`
says which drawn blocks are deliberately *not* diagrams.

`PUSH-AUDIT.md` is a runbook followed in place before pushing:
`pre-commit`, the diff-level greps, then four judgment sub-agents over
code quality, tests, documentation and security. Its briefs are
written for this repository's blast radius -- a defect here breaks
twenty other repositories quietly rather than breaking a service. It
is being made the last phase of every master plan -- see
`docs/plans/PLAN-push-audit-phase.md`, which is rolling that out;
drop this qualifier once the sweep has landed.

`REVIEWS.md` is owned by the `prune-reviews` workflow. In a pull
request that changes code or documentation, do not run `prune` or
`regen`, and do not commit the file: both the staleness caused by
editing a reviewed file and the header count moved by a file entering
or leaving `.vscode/review-scope.toml` are corrected on the next push
to main, because `prune` regenerates the file whether or not it pruned
anything. `review-tracking-tests` deliberately does not assert the
header count, so nothing here fails. Pruning from a branch is also
wrong more often than it is right, and not for the obvious reason:
`prune` compares each stamp against `HEAD`, which on a branch is the
branch tip, so it drops the marks for the files the pull request itself
touched and keeps the ones main has already pruned.

A review session is the exception. `stamp` regenerates `REVIEWS.md`
as well as writing the marks, and the rows, sidecars and marks are
committed together -- see `docs/code-review-tracking.md`. Where a
repository requires a pull request to reach its default branch, that
is how its review sessions land. See the `plan-phase-landing` shared
block in `PLAN-TEMPLATE.md`.

A pruned file needs a human to read it again and re-mark it in
weAudit. Do not re-stamp -- the mark attests that a person read that
exact content, so there is no version of this an agent can finish
alone. Accumulated staleness is the `review-coverage` audit's job to
report, not a pull request's.

`review-tracking.py` is run by hand in target repositories (via a thin
wrapper like ryll's `tools/review-tracking.sh`), deliberately not from
git hooks. Three subcommands also run from CI in steady state: `prune`
from an adopting repo's `prune-reviews` workflow on pushes to its
default branch, and `status` and `scope-orphans` from the consistency
audit's `review-coverage` and `review-scope-completeness` checks --
see `docs/code-review-tracking.md`.

## Working on review tracking

`ARCHITECTURE.md` describes the shape of the review tracking system
and where its pieces live; the conventions it enforces are in
`docs/code-review-tracking.md`. When implementing later phases of
`docs/plans/PLAN-code-review-tracking.md` or its steady-state
follow-on `docs/plans/PLAN-review-coverage.md`, read the plan's
analysis section first -- several design constraints
(sidecar rather than fields in weAudit's JSON, no stamping from CI)
exist for non-obvious verified reasons, and the plan's "Back brief"
section applies. Note the original "prune locally rather than from
CI" constraint was about developer clones and git hooks; CI pruning
of a repo's own default branch is the steady-state design, not a
violation of it.

Deploying the tooling to a repository (and verifying a deployment,
including that expensive CI skips review-only PRs) is covered by the
`review-tracking-adoption` skill in `.claude/skills/` -- the CI-skip
check is deliberately skill-based rather than part of
`audit-check.py`, because classifying workflows as build CI versus
content scanner differs per project and takes judgment.

## Conventions

- Python: single quotes, no external dependencies in the audit scripts
  (stdlib plus the `git` and `gh` CLIs only).
- Some of the prose here is parsed. `audit/scope.py` reads the
  in-scope and excluded lists out of `docs/audits/README.md` by
  splitting it on literal phrases, and raises `ScopeParseError` unless
  those phrases still delimit a list of repository names -- so reword
  freely and let its two consumers, `AuditScopeIsStatedOnceTest` and
  the `scope-coverage` check, say when a phrase mattered. Any new
  parse of a document by phrase gets the same treatment: a named
  constant and a guard, not a bare `split()`.
- This repository is audited by its own consistency audits. Two checks
  are N/A for stated reasons in `REPO_OVERRIDES` (its Python is never
  packaged, and it keeps `main` because it publishes no releases); see
  the excluded projects section of `docs/audits/README.md`.
