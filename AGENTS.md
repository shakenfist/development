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

- A criterion spans four files (check, metadata, spec, index), plus
  a column heading if it shares a spec file with another check, and
  they must stay in sync. `pre-commit` runs the tests that catch
  the cross-file breakages.
- The compliance tables between the `consistency-audit` markers in
  `docs/audits/*.md` are regenerated and pushed by the daily workflow.
  Never edit one by hand.
- Issue titles are the idempotency key for filing and closing, so
  `ISSUE_TITLES` in `scripts/audit_common.py` is an interface.
  Renaming an entry orphans every open issue for that check.
- A check that does not apply must report `not_applicable` with a
  reason rather than being omitted; an omitted check renders as
  `unknown`.
- Always pass `--dry-run` to `audit-manage-issues.py` when running it
  by hand. Without it, it files and closes real issues fleet-wide.

Repo-specific exceptions live in `REPO_OVERRIDES` in
`scripts/audit-check.py`.

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
repository, are in `docs/consistency-audits.md`.

Adding or removing a file matched by `.vscode/review-scope.toml`
changes the in-scope count in `REVIEWS.md`, which is generated. Run
`python3 scripts/review-tracking.py regen` and commit the result with
the change, or `review-tracking-tests` fails. It can also fail on a
branch that did not cause it, when another branch adds an in-scope
file and both regenerate to the same header text: the fix is the same
one command.

Editing a file that carries a review mark stales that mark, and the
same suite fails. Run `prune` and say so: the file then needs a human
to read it again and re-mark it in weAudit. Do not re-stamp -- the
mark attests that a person read that exact content, so there is no
version of this an agent can finish alone.

`review-tracking.py` is run by hand in target repositories (via a thin
wrapper like ryll's `tools/review-tracking.sh`), deliberately not from
git hooks. Two subcommands also run from CI in steady state: `prune`
from an adopting repo's `prune-reviews` workflow on pushes to main, and
`status` from the consistency audit's `review-coverage` check -- see
`docs/code-review-tracking.md`.

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
of a repo's own main branch is the steady-state design, not a
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
- Some of the prose here is parsed. `AuditScopeIsStatedOnceTest`
  reads the in-scope and excluded lists out of
  `docs/audits/README.md` by splitting it on literal phrases, and
  asserts those phrases still delimit a list of repository names --
  so reword freely and let the tests say when a phrase mattered. Any
  new parse of a document by phrase gets the same treatment: a named
  constant and an assertion, not a bare `split()`.
- This repository is audited by its own consistency audits. Two checks
  are N/A for stated reasons in `REPO_OVERRIDES` (its Python is never
  packaged, and it keeps `main` because it publishes no releases); see
  the excluded projects section of `docs/audits/README.md`.
