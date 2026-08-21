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

### The trigger handling must be the shared action

`pr-re-review.yml` must reach `shakenfist/actions/pr-bot-trigger@main`
rather than hand-rolling the phrase match, permission lookup, reaction
and refusal reply in inline shell. `pr-retest.yml` and
`pr-address-comments.yml` already do.

This is a security requirement, not a tidiness one. `pr-bot-trigger`
refuses pull requests from forks, and a hand-rolled copy does not
inherit that. The action's `pr-ref` output is `.head.ref` -- the branch
name in the *head* repository, carrying nothing to say which repository
that is -- and callers hand it to `actions/checkout` and to
`git push origin HEAD:refs/heads/<ref>` against **their own**
repository. Fork pull requests are commonly opened from the fork's
default branch, so `.head.ref` is literally `main`: the checkout
succeeds against the target's `main`, the bot commits to it, and the
push lands unreviewed commits there. No malice is required -- a
maintainer typing the trigger phrase on a fork pull request is enough.

Because the guard lives in the action, every workflow that uses it
picked the fix up at `@main` with no change on its side. That is the
whole argument for the requirement: a shared action is how a fix
reaches ten repositories at once, and a local copy is how one of them
misses it.

An earlier version of the template open-coded this, which is why every
deployment needs replacing rather than editing. The template copy had
also drifted in ways that matter less but point the same way: it
reacted with `+1` instead of `rocket`, worded its refusal differently,
and never checked the trigger phrase itself, so it could not distinguish
"phrase not matched" from "not authorized".

The check reports nothing when `pr-re-review.yml` is absent -- that is
already a finding on its own, and reporting both would be two findings
for one missing file.

### Review JSON validation

`render-review.py` resolves its schema as
`Path(__file__).parent / 'review-schema.json'`. A deployed copy of the
script must therefore keep `review-schema.json` in the same
directory. Without it `load_schema()` returns `None` and
`validate_review()` returns success without checking anything, so
`--validate` accepts a review carrying an invented `category` or
`action` while still exiting zero. The structural fallback in that
function is a separate branch, reached only when `jsonschema` cannot be
imported; on a runner where it can, a missing schema file means no
validation at all.

That matters because `address-comments-with-claude.sh` runs
`render-review.py --validate` as its gate before letting Claude Code
loose on the review's items: a review the schema would have rejected
gets acted on instead. The failure is silent in both directions -- the
script reports success and the audit saw nothing -- so the check looks
for a `render-review.py` with no `review-schema.json` beside it.

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

Last regenerated: 2026-08-21T06:54:10.992868+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#126 |
| client-python | non-compliant | shakenfist/client-python#367 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#36 |
| clingwrap | non-compliant | shakenfist/clingwrap#121 |
| cloudgood | non-compliant | shakenfist/cloudgood#1 |
| development | non-compliant | shakenfist/development#30 |
| divergulent | non-compliant | shakenfist/divergulent#36 |
| instar | non-compliant | shakenfist/instar#515 |
| kerbside | non-compliant | shakenfist/kerbside#348 |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#949 |
| library-utilities | non-compliant | shakenfist/library-utilities#32 |
| occystrap | non-compliant | shakenfist/occystrap#120 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#303 |
| sfui | non-compliant | shakenfist/sfui#26 |
| shakenfist | non-compliant | shakenfist/shakenfist#3314 |

Details for non-compliant projects:

- **agent-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **client-python** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **client-python-k3s** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **clingwrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **cloudgood** (Status): Missing workflows: pr-re-review.yml, pr-address-comments.yml
- **development** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **divergulent** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **instar** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **kerbside** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **kerbside-patches** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **library-utilities** (Status): Missing pr-re-review.yml; Missing pr-address-comments.yml; Missing pr-retest.yml; No workflow uses shared action review-pr-with-claude@main
- **occystrap** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
- **ryll** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; tools/render-review.py has no review-schema.json beside it, so its --validate accepts any review
- **sfui** (Status): pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard; tools/render-review.py has no review-schema.json beside it, so its --validate accepts any review
- **shakenfist** (Status): Missing pr-retest.yml; pr-re-review.yml does not use shakenfist/actions/pr-bot-trigger@main, so it hand-rolls the trigger handling and does not inherit the action's fork pull request guard
<!-- consistency-audit:end -->
