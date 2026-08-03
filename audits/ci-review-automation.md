# Audit: CI review automation and developer automation

## What we check

### Automated review

* Claude Code automated review runs in the CI workflow, only after
  all other tests pass.
* The reviewer job must be a call to the shared reusable workflow
  `shakenfist/actions/.github/workflows/pr-auto-review.yml@main`,
  with the project's own test jobs in its `needs:` list. Writing the
  reviewer job out in full in the project's CI workflow is
  superseded: projects still carrying a hand-written
  `automated_reviewer` job should migrate to the reusable workflow
  and delete their `check-bot-commit` job, which the reusable
  workflow replaces with an API call.
* The reviewer must reach Claude Code through the shared action
  `shakenfist/actions/review-pr-with-claude@main` (not per-project
  scripts). The reusable workflow does this for its callers.
* The calling job needs `pull-requests: write` and `issues: write`
  permissions, because a cross-repository reusable workflow cannot
  grant itself more token scope than its caller has.
* The automatic review must not pass `force` to the review action,
  so that a PR the bot has already reviewed is left alone.
  `pr-re-review.yml` is the only workflow which sets `force`, making
  an explicit human request the sole way to override an existing
  review.
* The reviewer runs Claude Code with
  `--dangerously-skip-permissions` while holding a write-capable
  token, and the PR diff is untrusted input, so the automatic review
  must be restricted to same-repository pull requests. Fork PRs are
  reviewed only on explicit human request.

### Developer automation

Projects should include bot-triggered workflows responding to
`@shakenfist-bot` comments from authorised users:

* `pr-re-review.yml` -- triggers another automated review (with
  `pull-requests: write` and `issues: write`).
* `pr-address-comments.yml` -- triggers Claude Code to address
  review comments.
* `pr-retest.yml` -- re-runs functional tests.

### Test drift fixing (optional)

Projects with large test suites prone to drift should also add:

* `pr-fix-tests.yml` + `test-drift-fix.yml` -- triggers Claude Code
  to fix CI failures.

These use shared composite actions from the `actions/` repository:

* `shakenfist/actions/pr-bot-trigger@main`
* `shakenfist/actions/review-pr-with-claude@main`

### Automated reviewer prompt

The automated reviewer's prompt should ensure it checks that
documentation in the `docs/` directory has been updated for any
user-visible changes.

## Template

Template: `templates/ci-review-automation/`
See: `templates/ci-review-automation/README.md`
Docs: `docs/ci-review-automation.md`, `docs/automated-pr-review.md`

Test drift fixing template: `templates/test-drift-fix/`
See: `templates/test-drift-fix/README.md`

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-03T09:51:13.600453+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | compliant | - |
| client-python | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| divergulent | non-compliant | shakenfist/divergulent#36 |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#949 |
| library-utilities | non-compliant | shakenfist/library-utilities#32 |
| occystrap | compliant | - |
| ryll | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3314 |

Details for non-compliant projects:

- **cloudgood** (Status): Missing workflows: pr-re-review.yml, pr-address-comments.yml
- **divergulent** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **kerbside-patches** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **library-utilities** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **shakenfist** (Status): Missing pr-retest.yml
<!-- consistency-audit:end -->
