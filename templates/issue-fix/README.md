# Issue Fix Templates

This template sets up a Claude Code-powered "idle bug fixer": a
manually dispatched workflow that triages recent open GitHub
issues, picks the one that is most important *and* achievable, and
has Claude Code propose a fix as a draft pull request. The commit
is tagged with `Fixes #NNNN` so the issue closes automatically if
the PR merges.

The intended trigger is an external conductor that dispatches the
workflow when spare CI capacity is available (for example, when the
sfcbr cluster is idle), but a human can also dispatch it directly,
optionally naming a specific issue.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `issue-fix.yml` | `.github/workflows/issue-fix.yml` | Triage and fix workflow |

## How it works

1. The **triage job** lists the most recent open issues with no
   linked PR and no `automated-fix-attempted` label, filters them to
   issues authored by users with write access, and asks a cheap
   model (Haiku) to select one -- or none. "Nothing suitable" is a
   successful no-op run.
2. The **fix job** applies the `automated-fix-attempted` label and
   posts a comment linking to the run, then asks Claude Code to fix
   the issue, verifies the test suite passes, and publishes a draft
   PR labelled `automated-fix` with the maintainer as reviewer.

## Design decisions

- **The attempt label is applied at the start of the attempt**, not
  the end, so crashes and timeouts still mark the issue as
  attempted and the workflow never grinds on the same issue every
  idle cycle. Removing the label re-arms the issue for another
  attempt.
- **The size budget lives in two places.** Triage is told to prefer
  issues it estimates at under 100 changed lines excluding tests --
  but that is only an estimate. The enforceable cap is applied
  after the fix is produced: if the non-test diff exceeds
  `max_diff_lines` (default 400), no PR is created; the branch is
  pushed and a comment on the issue asks for human judgment.
- **Only issues authored by users with write access are eligible**,
  and only issue comments from such users are fed to the model.
  Issue text is untrusted input to an agent with push access;
  restricting authorship closes the injection channel via the issue
  body, and comment filtering closes it via the thread. Dispatching
  with an explicit `issue_number` bypasses the author check --
  doing so asserts a human has read and trusts the issue text.
- **Output is always a draft PR** (or an issue comment). The
  workflow has no path to merging code; a human reviews and merges
  every proposed fix.

## Conductor integration

The fix runs on the `claude-code` static runner, so it does not
consume test cluster capacity itself -- the cluster cost is the
functional CI on the resulting PR. A conductor should therefore
gate on output backlog, not runner availability:

```bash
# Only dispatch when no automated fix PR is already in flight:
if [ "$(gh pr list --repo OWNER/REPO --label automated-fix \
    --state open --json number --jq length)" -eq 0 ]; then
  gh workflow run issue-fix.yml --repo OWNER/REPO
fi
```

Completion can be watched with
`gh run list --workflow=issue-fix.yml`.

## Customisation required

Search for `{{PLACEHOLDER}}` markers and comments explaining what
to replace:

- **`{{ENVIRONMENT}}`** -- optional project environment (proxies
  etc.)
- **`{{TEST_PATH_PATTERN}}`** -- regular expression matching test
  paths, excluded from the size cap
- **`{{INSTALL_DEPENDENCIES}}`** -- project dependency installation
- **`{{PROJECT_CONTEXT}}`** -- project description for the Claude
  prompt
- **`{{TEST_COMMAND}}`** -- the test runner, in two places: the
  Claude prompt and the verify step
- **`{{MAINTAINER}}`** -- GitHub username for PR assignee/reviewer
- **`{{MAINTAINER_NAME}}` / `{{MAINTAINER_EMAIL}}`** -- for the
  commit `Signed-off-by`

## Prerequisites

- Self-hosted runners with the `claude-code` label
- Claude Code CLI installed and authenticated on `claude-code`
  runners
- The dispatching user (or conductor token) needs write access

## Projects using these templates

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live (original) |
