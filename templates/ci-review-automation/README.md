# CI Review Automation Templates

These templates set up the bot-triggered workflows for Shaken Fist
projects. Both files copy directly into `.github/workflows/` with no
modifications: everything they need lives in the shared actions, so
there are no helper scripts to copy alongside them and nothing to edit
per project.

The comment addresser used to ship from here as a third workflow and
three helper scripts. It is retired -- see "The comment addresser is
retired" below.

For automatic test fixing (suited to projects with large test
suites), see the separate
[`templates/test-drift-fix/`](../test-drift-fix/) templates.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `pr-re-review.yml` | `.github/workflows/pr-re-review.yml` | Manual re-review trigger |
| `pr-retest.yml` | `.github/workflows/pr-retest.yml` | Manual functional test re-run (dispatches `functional-tests.yml`; substitute the project's own test workflow name if it differs, as development does for `ci.yml`) |

## Syncing deployed copies

Everything in this directory is now ahead of every deployment. The
2026-08-21 round of fixes came out of the automated review of
shakenfist/actions#22, which was the first time these files were read
adversarially rather than copied, and it found defects in the templates
themselves rather than in that deployment. **Every repository listed
at the bottom of this file is running pre-fix copies of the two
workflows in this directory.** That is about these files, not about
which reviewer a project uses: the migration to the reusable
`pr-auto-review.yml` is finished everywhere, and is a separate matter
from the bot triggers being out of date.

### Workflow fixes (2026-08-21)

* **`pr-re-review.yml` is rewritten.** It used to run the permission
  check, the reaction and the refusal reply on a `claude-code` runner --
  holding a scarce runner for every comment containing the phrase,
  including comments from people with no write access. It now splits
  trigger from review, matching the other two files.
* **`pr-re-review.yml` uses `pr-bot-trigger`.** It open-coded the same
  three steps in about thirty lines of inline shell, and the copy had
  drifted: `+1` instead of `rocket`, a differently worded refusal, and
  no trigger-phrase check of its own so it could not tell "phrase not
  matched" from "not authorized". **Treat this one as a security
  update**: `pr-bot-trigger` gained a fork-pull-request guard, and the
  inline copy does not have it. See below.
* **`pr-re-review.yml` checks out the pull request's merge ref.** It had
  no `ref:`, so for an `issue_comment` event it checked out the default
  branch -- and `review-pr-with-claude.sh` reads `AGENTS.md`,
  `ARCHITECTURE.md` and `README.md` from the working directory. A
  re-review therefore judged a change against the conventions as they
  were *before* it. The diff comes from the API and is unaffected, which
  makes the symptom subtle: a real review, calibrated wrongly.
* **`pr-re-review.yml` has a concurrency group.** Two requests in quick
  succession ran concurrently and posted two reviews.
* **Both ignore comments authored by a bot.** The success and
  no-changes comments embed `${summary}`, which is model-generated text.
  A per-item rationale that quotes a trigger phrase re-fires the
  workflow, and `contains()` does not care that it is inside a quote.
* `actions/upload-artifact` moved from `@v6` to `@v7`.

### Hardening from CodeQL (2026-08-22)

Enabling CodeQL on the development repository put an Actions scanner in
front of these files for the first time, and it raised
`actions/untrusted-checkout/high` against the workflows that check out
a pull request: "checkout of untrusted code in a privileged workflow
with later potential execution". Every repository running these
workflows already carries the same open alert.

The finding is right about the shape and cannot see the mitigation: the
fork guard lives in `pr-bot-trigger`, a composite action in another
repository, so from the scanner's position the checkout is unguarded.
Two changes came out of triaging it, and both are worth having on their
own merits:

* **`persist-credentials: false` on `pr-re-review.yml`'s checkout.**
  This was a real gap rather than a scanner artefact.
  The retired `pr-address-comments.yml` had always set it; this file
  did not, so the default left a write-scoped token in the
  `.git/config` of a working tree holding the pull request's own code
  -- the tree Claude Code is then pointed at with
  `--dangerously-skip-permissions`.
  `review-pr-with-claude` authenticates `gh` from `GH_TOKEN` and never
  pushes, so nothing needed the credential helper.
* **`pr-re-review.yml` now checks `same_repo` explicitly** in the job `if:`,
  alongside `authorized`. It is redundant -- `pr-bot-trigger` folds the
  fork check into `authorized` -- and that is the point: the guard is
  the one restriction in this file worth stating twice, so a
  regression in the shared action cannot quietly widen what runs, and
  the restriction is visible without leaving the file.

The residual risk the alert points at is real and accepted: refusing
forks reduces the exposure to accounts with push access, which under
branch protection is not the same set as accounts that can merge. The
reviewer still runs a write-capable agent over content those accounts
control, including the `AGENTS.md` it reads for context. Closing that
properly means sandboxing the reviewer or dropping its token to
read-only, which is a larger piece of work than this section.

### The fork guard

`pr-bot-trigger`'s `pr-ref` output is `.head.ref`: the branch name in the
*head* repository, carrying nothing to say which repository that is.
Callers hand it to `actions/checkout` and to
`git push origin HEAD:refs/heads/<ref>` against **their own**
repository. Fork pull requests are commonly opened from the fork's
default branch, so `.head.ref` is literally `main` -- and then the
checkout succeeds against the target repository's `main`, the bot
commits to it, and the push lands unreviewed commits there. No malice
is required; a maintainer typing the trigger phrase on a fork pull
request is enough.

That is fixed in `shakenfist/actions/pr-bot-trigger`, folded into the
existing `authorized` output, so **every deployment inherits it at
`@main` with no change on their side** -- provided the workflow actually
uses the action. `pr-retest.yml` does. The old inline
`pr-re-review.yml` does not, which is why replacing it is the one item
here that cannot wait for a convenient moment.

## Setting up the automatic review

The automatic review is not a file in this directory. Its body lives
once, as a reusable workflow in the actions repository at
`shakenfist/actions/.github/workflows/pr-auto-review.yml`, because the
gate "review only after the tests pass" has to be expressed in terms
of each project's own test jobs. Projects add a small calling job to
their CI workflow -- `functional-tests.yml` in most projects,
`ci.yml` in development and ryll, `unit-tests.yml` in divergulent:

```yaml
  automated_reviewer:
    name: "Automated reviewer"
    needs: [sanity_checks, smoke_collection]
    permissions:
      contents: read
      pull-requests: write
      issues: write
    uses: shakenfist/actions/.github/workflows/pr-auto-review.yml@main
```

Replace the `needs:` list with the project's own test jobs -- that
list is the CI-passed gate, since a job skipped because a dependency
failed never starts the reusable workflow. It is also the only part
which differs between projects; everything else (runner, timeout,
fork restriction, bot-commit check, concurrency) is centralised.

Three details specific to reusable workflows:

* The `permissions:` block goes on the **calling** job. A
  cross-repository reusable workflow cannot grant itself more token
  scope than its caller has, so omitting this leaves the reviewer
  unable to post.
* The calling job cannot set `runs-on:` or `timeout-minutes:`; both
  are defined inside the reusable workflow.
* Do **not** add `secrets: inherit`. Nothing in the reviewer chain
  reads a secret -- `pr-auto-review.yml` and `review-pr-with-claude`
  both authenticate with `github.token`, which comes from the
  `permissions:` block above -- so inheriting buys nothing while
  handing every secret your repository holds, publishing tokens
  included, to a workflow which lives in another repository. An
  earlier version of this template carried the line, and nine
  repositories copied it before it was corrected. The
  `ci-review-automation` audit now checks for it, so a repository
  which reintroduces the line -- or which was deployed from the old
  template and has not been cleaned up yet -- is told rather than
  discovered. Which repositories those are is the compliance table
  in the
  [audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/ci-review-automation.md),
  which regenerates daily; it is not restated here, because a count
  written into a file nobody edits goes stale silently.

This is the same calling pattern as
`shakenfist/actions/.github/workflows/smoke-cluster.yml`, which
several projects already call from their CI workflows -- though that
one does read secrets, so its callers inherit and should keep doing
so. The two are not interchangeable on this point.

### Migrating from the in-CI reviewer job

Projects which predate this arrangement have a full `automated_reviewer`
job, and a `check-bot-commit` job it depends on, written out inside
`functional-tests.yml` (or `ci.yml`). To migrate:

1. Replace the `automated_reviewer` job body with the calling job
   above, keeping the existing `needs:` list.
2. Delete the `check-bot-commit` job, unless another job uses its
   `is_bot` output. The reusable workflow does that check itself, over
   the API, which saves a job and a runner.
3. Keep the CI workflow's top-level `permissions` block.

### How a review is gated

Three independent gates, in order:

1. **CI passed** -- the calling job's `needs:` list.
2. **Last commit is not the bot's** -- prevents a bot commit
   triggering a review which triggers another bot commit.
3. **The bot has not already reviewed this PR** -- this one lives
   inside `review-pr-with-claude` itself, which skips when it finds an
   existing `shakenfist-bot` review unless its `force` input is set.
   The automatic review deliberately does not set `force`, and
   `pr-re-review.yml` does. So a human commenting
   `@shakenfist-bot please re-review` is the only way to get a second
   review on a PR.

### Fork pull requests are not reviewed

The reusable workflow requires the PR to come from a branch in the
same repository. The reviewer runs Claude Code with
`--dangerously-skip-permissions` on a runner holding a token with
`pull-requests: write` and `issues: write`, and it is fed the PR diff,
which is untrusted input. Reviewing a fork PR would put an attacker's
text in front of a write-capable token. Lifting this restriction means
sandboxing the reviewer or giving it a read-only token first.

The restriction lives in the reusable workflow rather than in each
caller, so it cannot be lost when a project edits its CI workflow.

## Prerequisites

These workflows require:

- Self-hosted runners with `claude-code` and `static` labels
- Claude Code CLI installed and authenticated on `claude-code` runners
- `gh` CLI available on all runners
- The shared actions from
  [shakenfist/actions](https://github.com/shakenfist/actions):
  - `pr-bot-trigger` -- handles bot command parsing and authorisation
  - `review-pr-with-claude` -- runs automated code reviews
  - `.github/workflows/pr-auto-review.yml` -- the reusable workflow
    wrapping `review-pr-with-claude` for the automatic review

## Bot commands

Once deployed, repository collaborators with write access can
comment on PRs with:

| Command | Description |
|---------|-------------|
| `@shakenfist-bot please retest` | Re-run functional tests |
| `@shakenfist-bot please re-review` | Request a fresh automated review |

For the `@shakenfist-bot please attempt to fix` command, see the
separate [`templates/test-drift-fix/`](../test-drift-fix/) templates.

## The comment addresser is retired

`pr-address-comments.yml` answered `@shakenfist-bot please address
comments` by handing each item of a review to Claude Code and pushing a
commit per item. It shipped from this directory along with
`address-comments-with-claude.sh`, `render-review.py` and
`review-schema.json`. All four were removed in August 2026.

It was retired because it went unused. Review items are worked through
interactively with the reviewer instead, and a bot authoring commits
from a review no human had read is the part that stopped anyone
reaching for it. That is a preference about how review works, not a
defect in the implementation, so there is nothing here to fix and
nothing to migrate to.

What it leaves behind is worth removing rather than ignoring. The
workflow triggers on `issue_comment`, so it holds `contents: write`
against the pull request branch for a feature nobody wants, and it is
the last thing in a project that calls `render-review.py` -- so the
script and its schema become dead weight that the next project copies.
The `ci-review-automation` audit now fails a repository carrying any of
the four files, and they should be removed in a single commit: deleting
the workflow but keeping the scripts leaves behind exactly the copy
that gets propagated.

One thing does change. `render-review.py` in the shared action
still ends every review it posts with a line telling the reader to
use the addresser's trigger phrase. Once the chain is reaped that
invites a command nothing answers -- no workflow, no reply, no
failure -- which is the outcome the retired workflow's own failure
reporting existed to avoid. Dropping those lines is a change to
shakenfist/actions and cannot land here.

The reviewer is otherwise unaffected. It reaches `render-review.py`
through `shakenfist/actions/review-pr-with-claude@main`, which carries
its own copy of the script and its own schema.

## Projects using these templates

The bot-triggered workflows (`pr-re-review.yml` and `pr-retest.yml`)
are live in agent-python, client-python, client-python-k3s, clingwrap,
development, divergulent, imago, instar, kerbside, kerbside-patches,
occystrap, ryll, sfui and shakenfist.

imago is the one to watch when reaping the addresser: it has the
workflows and it still carries `pr-address-comments.yml`, but it is not
in the consistency audit matrix, so nothing will ever file an issue
about it. Everywhere else the audit does the asking.

All of those except kerbside-patches call the reusable
`pr-auto-review.yml` for their automatic review. The in-CI
`automated_reviewer` job the migration section above describes no
longer exists anywhere; that section is kept for repositories which
have not adopted any of this yet. `development` and `ryll` call it
from `ci.yml`, divergulent from `unit-tests.yml`, everyone else from
`functional-tests.yml`. development called it from the start rather
than migrating to it, so its `ci.yml` is the worked example of the
calling job described above -- including the `needs:` list doing duty
as the CI-passed gate, and the absence of an event guard for the
`workflow_dispatch` trigger.

Three audited repositories have no automated review at all:
cloudgood, kerbside-patches and library-utilities. kerbside-patches
does carry the two bot triggers, so `@shakenfist-bot please re-review`
works there even though nothing reviews automatically.
