# Audit: CI review automation and developer automation

## What we check

### Automated review

* Claude Code automated review runs in the CI workflow, only after
  all other tests pass.
* Must use the shared action
  `shakenfist/actions/review-pr-with-claude@main` (not per-project
  scripts).
* The reviewer job needs `pull-requests: write` and `issues: write`
  permissions.

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

| Project | Review | Re-review | Address | Retest | Fix tests | Issue |
|---------|--------|-----------|---------|--------|-----------|-------|
| agent-python | compliant | compliant | compliant | compliant | N/A | - |
| client-python | non-compliant | non-compliant | non-compliant | non-compliant | non-compliant | |
| clingwrap | compliant | compliant | compliant | compliant | N/A | - |
| cloudgood | N/A | non-compliant | non-compliant | N/A | N/A | |
| imago | compliant | compliant | compliant | compliant | compliant | - |
| kerbside | compliant | compliant | compliant | compliant | N/A | - |
| kerbside-patches | compliant | non-compliant | non-compliant | N/A | non-compliant | |
| library-utilities | non-compliant | non-compliant | non-compliant | non-compliant | non-compliant | |
| occystrap | compliant | compliant | compliant | compliant | compliant | - |
| ryll | non-compliant | non-compliant | non-compliant | non-compliant | non-compliant | |
| shakenfist | compliant | compliant | compliant | compliant | N/A | - |

N/A for fix tests: small test suite, not prone to drift.
N/A for cloudgood review/retest: no CI test workflows.
