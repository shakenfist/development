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
there `load_schema()` returns `None` and `validate_review()` falls
back to structural checks -- so `--validate` starts accepting reviews
with invented categories and actions, and still exits zero. Nothing
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
| `pr-retest.yml` | `.github/workflows/pr-retest.yml` | Manual functional test re-run |
| `pr-address-comments.yml` | `.github/workflows/pr-address-comments.yml` | Address review comments |
| `address-comments-with-claude.sh` | `tools/address-comments-with-claude.sh` | Addresses review items with Claude Code (edit `PROJECT_NAME`) |
| `render-review.py` | `tools/render-review.py` | Validates review JSON and renders it to markdown |
| `review-schema.json` | `tools/review-schema.json` | The schema `render-review.py` validates against |

## Syncing deployed copies of the scripts

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
    secrets: inherit
```

Replace the `needs:` list with the project's own test jobs -- that
list is the CI-passed gate, since a job skipped because a dependency
failed never starts the reusable workflow. It is also the only part
which differs between projects; everything else (runner, timeout,
fork restriction, bot-commit check, concurrency) is centralised.

Two details specific to reusable workflows:

* The `permissions:` block goes on the **calling** job. A
  cross-repository reusable workflow cannot grant itself more token
  scope than its caller has, so omitting this leaves the reviewer
  unable to post.
* The calling job cannot set `runs-on:` or `timeout-minutes:`; both
  are defined inside the reusable workflow.

This is the same pattern as
`shakenfist/actions/.github/workflows/smoke-cluster.yml`, which
several projects already call from their CI workflows.

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
clingwrap, imago, instar, kerbside, occystrap, ryll and shakenfist.

The standalone `pr-auto-review.yml` is new. Every one of those
projects still runs the automatic review as an in-CI
`automated_reviewer` job and needs the migration described above:

| Project | Automatic review |
|---------|------------------|
| [agent-python](https://github.com/shakenfist/agent-python) | In-CI job, to migrate |
| [client-python](https://github.com/shakenfist/client-python) | In-CI job, to migrate |
| [clingwrap](https://github.com/shakenfist/clingwrap) | In-CI job, to migrate |
| [instar](https://github.com/shakenfist/instar) | In-CI job, to migrate |
| [kerbside](https://github.com/shakenfist/kerbside) | In-CI job, to migrate |
| [occystrap](https://github.com/shakenfist/occystrap) | In-CI job, to migrate |
| [ryll](https://github.com/shakenfist/ryll) | In-CI job, to migrate |
| [shakenfist](https://github.com/shakenfist/shakenfist) | In-CI job, to migrate |
