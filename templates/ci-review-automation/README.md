# CI Review Automation Templates

These templates set up Claude Code-powered PR review automation and
bot-triggered workflows for Shaken Fist projects. The workflow files
can be copied directly with no modifications. The two helper scripts
copy into the target repository's `tools/` directory, and
`address-comments-with-claude.sh` needs one edit: replace
`PROJECT_NAME` in its Claude prompt with the project's name (and a
one-line description if that helps the model).

**`review-schema.json` must land in the same directory as
`render-review.py`.** The script resolves it as
`Path(__file__).parent / 'review-schema.json'`, and when it is not
there `load_schema()` returns `None` and `validate_review()` returns
success **without checking anything** -- so `--validate` starts
accepting reviews with invented categories and actions, and still exits
zero. (The structural fallback in that function is a different branch,
reached only when `jsonschema` is not importable at all; with
`jsonschema` installed and no schema file, nothing is checked.) Nothing
reports the downgrade, so a repository in that state is
indistinguishable from one that is validating. This directory shipped
the script without the schema until now, and ryll was deployed from it
in exactly that state; the `ci-review-automation` audit now checks for
a `render-review.py` with no schema beside it. Both scripts must have
merged to the default branch before the bot trigger works, because
`pr-address-comments.yml` reads them from a trusted checkout of that
branch. Forgetting the scripts leaves the bot reacting to trigger
comments and then failing with "No such file or directory" -- this
has happened in practice on client-python-k3s, and again on sfui
where `render-review.py` alone was missed (this directory not
carrying the scripts is how both happened; it does now).

For automatic test fixing (suited to projects with large test
suites), see the separate
[`templates/test-drift-fix/`](../test-drift-fix/) templates.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `pr-re-review.yml` | `.github/workflows/pr-re-review.yml` | Manual re-review trigger |
| `pr-retest.yml` | `.github/workflows/pr-retest.yml` | Manual functional test re-run (dispatches `functional-tests.yml`; substitute the project's own test workflow name if it differs, as development does for `ci.yml`) |
| `pr-address-comments.yml` | `.github/workflows/pr-address-comments.yml` | Address review comments |
| `address-comments-with-claude.sh` | `tools/address-comments-with-claude.sh` | Addresses review items with Claude Code (edit `PROJECT_NAME`) |
| `render-review.py` | `tools/render-review.py` | Validates review JSON and renders it to markdown |
| `review-schema.json` | `tools/review-schema.json` | The schema `render-review.py` validates against |

## Syncing deployed copies

Everything in this directory is now ahead of every deployment. The
2026-08-21 round of fixes came out of the automated review of
shakenfist/actions#22, which was the first time these files were read
adversarially rather than copied, and it found defects in the templates
themselves rather than in that deployment. **Every repository listed at
the bottom of this file is running the pre-fix versions.**

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
* **All three ignore comments authored by a bot.** The success and
  no-changes comments embed `${summary}`, which is model-generated text.
  A per-item rationale that quotes a trigger phrase re-fires the
  workflow, and `contains()` does not care that it is inside a quote.
* **A failed `pr-address-comments.yml` run says so on the pull
  request.** Both reporting steps were gated on the commit count, so any
  failure before the push left the requester with `pr-bot-trigger`'s
  "Starting to address automated review comments..." and nothing else.
  The likeliest failure is the likeliest user error: asking for comments
  to be addressed on a pull request that was never reviewed.
* **The `addressed` and `skipped` counts are used.** They were extracted
  into `$GITHUB_OUTPUT` and never read by anything.
* `actions/upload-artifact` moved from `@v6` to `@v7`.

### Hardening from CodeQL (2026-08-22)

Enabling CodeQL on the development repository put an Actions scanner in
front of these files for the first time, and it raised
`actions/untrusted-checkout/high` against both workflows that check out
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
  `pr-address-comments.yml` has always set it; this file did not, so the
  default left a write-scoped token in the `.git/config` of a working
  tree holding the pull request's own code -- the tree Claude Code is
  then pointed at with `--dangerously-skip-permissions`.
  `review-pr-with-claude` authenticates `gh` from `GH_TOKEN` and never
  pushes, so nothing needed the credential helper.
* **Both workflows now check `same_repo` explicitly** in the job `if:`,
  alongside `authorized`. It is redundant -- `pr-bot-trigger` folds the
  fork check into `authorized` -- and that is the point: the guard is
  the one restriction in these files worth stating twice, so a
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
uses the action. `pr-retest.yml` and `pr-address-comments.yml` do. The
old inline `pr-re-review.yml` does not, which is why replacing it is the
one item here that cannot wait for a convenient moment.

### Script fixes (2026-08-21)

* **`reset_worktree` runs `git clean -fd`.** `git reset --hard` leaves
  untracked files alone, so a Claude run that wrote a new file and then
  errored, declined, or hit `--max-turns` before `git add` left it in
  the tree -- and a later item reaching for `git add -A` committed it
  under the wrong item's title. That is precisely what the function was
  written to prevent.
* **`--output-dir` inside the work tree is refused**, so the new
  `git clean` cannot delete the script's own state files.
* **`location` is sanitized, and `category` and `severity` are validated
  against their enums.** All three are model output derived from an
  untrusted diff, reaching a prompt for a Claude run holding `GH_TOKEN`
  with `--dangerously-skip-permissions`. `action` was already validated;
  these were not, and the schema is only enforced when `jsonschema` is
  importable on the runner.
* **Malformed items get a summary row and a skipped count.** The id and
  action guards used to `continue` without either, so `items_found` no
  longer equalled `addressed + skipped` and the item vanished from the
  summary table -- silence in the one case where the input was bad.

### Earlier drift

The deployed copies of `address-comments-with-claude.sh` predate this
directory becoming their source of truth, and drifted: fixes landed
in one repository without reaching the others. The canonical script
here carries all of them, and every deployment listed at the bottom
of this file lacks at least one:

- The Claude prompt no longer instructs running `pre-commit` inside
  the untrusted PR checkout, and prohibits running any script from
  it. The workflow's own security model documents why: a PR author
  controls `.pre-commit-config.yaml` and the local hooks it points
  at, so this was arbitrary code execution under a write-scoped
  token. Every deployed copy has this problem; treat syncing this
  fix as a security update, not housekeeping.
- The git index is reset between review items (kerbside had this),
  so an abandoned item's staged changes cannot leak into the next
  item's commit.
- The claude binary is located via `CLAUDE_BIN`, then PATH, then
  `~/.local/bin/claude` (shakenfist had this).
- `--help` prints the whole header comment block instead of a
  hardcoded line range that had already drifted, and reads the
  script through its saved absolute path -- the script changes
  directory before parsing arguments, so a relative `$0` no longer
  resolves. `sanitize_input` escapes pipes so item titles cannot
  break the markdown summary table.

When syncing, preserve the deployment's project name line in the
Claude prompt.

## Setting up the automatic review

The automatic review is not a file in this directory. Its body lives
once, as a reusable workflow in the actions repository at
`shakenfist/actions/.github/workflows/pr-auto-review.yml`, because the
gate "review only after the tests pass" has to be expressed in terms
of each project's own test jobs. Projects add a small calling job to
their CI workflow (`functional-tests.yml`, or `ci.yml` for ryll):

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
  repositories copied it before it was corrected. Seven have since
  merged the removal; `development` and `ryll` were the two still
  carrying it when this was written. The `ci-review-automation`
  audit now checks for it, so a repository which reintroduces the
  line during migration is told rather than discovered.

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
| `@shakenfist-bot please address comments` | Address review comments |

For the `@shakenfist-bot please attempt to fix` command, see the
separate [`templates/test-drift-fix/`](../test-drift-fix/) templates.

## Projects using these templates

The bot-triggered workflows (`pr-re-review.yml`, `pr-retest.yml`,
`pr-address-comments.yml`) are live in agent-python, client-python,
clingwrap, development, imago, instar, kerbside, occystrap, ryll and
shakenfist.

The standalone `pr-auto-review.yml` has one caller. development called
it from the start rather than migrating to it, so its `ci.yml` is the
worked example of the calling job described above -- including the
`needs:` list doing duty as the CI-passed gate, and the absence of an
event guard for the `workflow_dispatch` trigger. Everyone else still
runs the automatic review as an in-CI `automated_reviewer` job and
needs the migration:

| Project | Automatic review |
|---------|------------------|
| [development](https://github.com/shakenfist/development) | Calls the reusable workflow (reference) |
| [agent-python](https://github.com/shakenfist/agent-python) | In-CI job, to migrate |
| [client-python](https://github.com/shakenfist/client-python) | In-CI job, to migrate |
| [clingwrap](https://github.com/shakenfist/clingwrap) | In-CI job, to migrate |
| [instar](https://github.com/shakenfist/instar) | In-CI job, to migrate |
| [kerbside](https://github.com/shakenfist/kerbside) | In-CI job, to migrate |
| [occystrap](https://github.com/shakenfist/occystrap) | In-CI job, to migrate |
| [ryll](https://github.com/shakenfist/ryll) | In-CI job, to migrate |
| [shakenfist](https://github.com/shakenfist/shakenfist) | In-CI job, to migrate |
