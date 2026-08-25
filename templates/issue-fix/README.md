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
| `claude-model-fallback.sh` | `tools/claude-model-fallback.sh` | Model fallback wrapper (needs `chmod +x`) |

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

- **The fixer reads the project's plans before it writes code.** In
  a project which plans work in advance, a one-off fix can easily
  cut across a partially implemented plan: either ignoring the
  pattern the plan's landed phases established, or hand-rolling a
  workaround for a defect class an outstanding phase is designed to
  fix properly. Both have to be unpicked before the plan can
  proceed, which makes them worse than no fix at all. Triage
  therefore skims the plan index and deprioritises issues an
  unlanded plan already owns, and the fix job reads the relevant
  plan files in full before diagnosis, declining with `NO_FIX` when
  the fix belongs to a plan rather than to it. Note that the plans
  are *read from the checkout at run time* rather than summarised
  into the prompt: plans change constantly, and a summary baked
  into a workflow file would be describing a state of the world
  that no longer holds. Delete both sections in a project which
  does not plan this way.
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
- **The fix attempt falls back between models when credit runs
  out.** Pinning a single model means the fixer stops working
  entirely once that model's subscription allowance is exhausted.
  The claude CLI's own `--fallback-model` flag does not cover this:
  it handles overloaded or unavailable models, not an exhausted
  allowance, which arrives as an HTTP 429 carried in the
  `--output-format json` payload as `api_error_status`.
  `claude-model-fallback.sh` detects that and moves to the next
  model in the `models` input. A refused request is free -- no
  tokens, `total_cost_usd` of 0 -- so the wrapper attempts the real
  job rather than paying for a pre-flight probe on every run where
  the preferred model is in fact available. Triage stays pinned to
  Haiku, which is cheap enough not to need this.
- **The model writes the PR description, not the workflow.** A
  body assembled by the workflow can only ever be a diffstat and a
  boilerplate paragraph, which throws away everything the model
  learned while fixing the issue -- what the root cause turned out
  to be, which half of the issue it deliberately left alone, which
  judgement calls a reviewer might make differently. The prompt
  therefore asks for a `PR_DESCRIPTION_START`/`END` block alongside
  the commit summary, and the workflow appends only the mechanical
  parts (`Fixes #NNNN`, diffstat, verification note, run link).
  Both blocks are best-effort: a missing description falls back to
  the commit message body, and a missing commit message to the old
  boilerplate, because the code changes are worth publishing even
  when the prose is lost.
- **The marker extraction is pinned by a test**, in
  `scripts/test_issue_fix_extraction.py`, which lifts the awk
  program out of this workflow and runs it over the shapes a
  transcript can take: two blocks, a repeated `START`, an `END`
  before any `START`, a block truncated by the end of the run, a
  marker named mid-line in the prose. None of those are syntax
  errors, so actionlint and shellcheck pass over an extraction
  which quietly publishes marker lines or a slab of transcript as
  the PR body. The first version of this code used a `sed` address
  range and did exactly that.
- **The description is passed to `gh` with `--body-file`.** It is
  model output, so interpolating it into a shell string -- an
  unquoted heredoc in particular -- would execute any `$(...)` or
  backticks it contained.
- **The prompt tells the model that ending its turn ends the run.**
  The model may take as many turns as it likes -- `max_turns` is
  200 -- but under `claude -p` there is nobody to reply and nothing
  to re-invoke it, so the turn which ends without a tool call is
  the last one, and any work it meant to come back to is lost.
  This is not hypothetical: a run backgrounded the test suite,
  ended its turn intending to check on it, and committed a correct
  fix under a placeholder commit message with an empty PR
  description. The "How this run works" section of the prompt
  exists to say run the test suite in the foreground and wait.
- **Output is always a draft PR** (or an issue comment). The
  workflow has no path to merging code; a human reviews and merges
  every proposed fix.
- **The draft PR does not start CI by itself.** GitHub deliberately
  does not run workflows on pull requests created with the default
  workflow token (this prevents recursive triggering). The human
  reviewer needs to nudge the PR -- push to it, or close and reopen
  it -- to run the full CI suite. Switching to a PAT or GitHub App
  token would remove this friction if it becomes annoying.

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

The Shaken Fist deployment of this trigger lives in the private-ci
conductor (`conductor/bugfixer.py`): it dispatches when the sfcbr
cluster has been continuously idle for five minutes, rate limited
to one dispatch an hour and five per trailing day (durable across
conductor restarts), and skips dispatch while an `automated-fix`
PR is open or a fixer run is already in flight.

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
- **`{{PLANS_LOCATION}}` / `{{PLANS_INDEX}}`** -- the directory
  holding the project's plan documents and its index file (for
  Shaken Fist, `docs/plans/` and `docs/plans/index.md`). Both
  appear in the triage prompt and the fix prompt. If the project
  has no plans, delete the "Not already owned by a plan" triage
  criterion and the "Before you start: check the design intent"
  section instead of substituting them.
- **`{{TEST_COMMAND}}`** -- the test runner, in two places: the
  Claude prompt and the verify step
- **`{{MAINTAINER}}`** -- GitHub username for PR assignee/reviewer
- **`{{MAINTAINER_NAME}}` / `{{MAINTAINER_EMAIL}}`** -- for the
  commit `Signed-off-by`

## Prerequisites

- Self-hosted runners with the `claude-code` label
- Claude Code CLI installed and authenticated on `claude-code`
  runners
- `jq` on the `claude-code` runners, used by
  `claude-model-fallback.sh` to read the claude CLI's JSON output
- The dispatching user (or conductor token) needs write access

## Upgrading an existing deployment

The `model` input was replaced by `models` (a comma-separated
preference list). GitHub rejects a `workflow_dispatch` carrying an
input the workflow does not declare, so any dispatcher that names
the old input fails outright rather than degrading -- check yours
before deploying, and update both in the same window.

The Shaken Fist conductor described above is unaffected: it
dispatches with no inputs at all, so the workflow's own defaults
apply and the rename is invisible to it. A conductor which pins the
model explicitly, or a human running `gh workflow run ... -f
model=...`, needs to switch to `-f models=...`.

## Projects using these templates

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live (original) |
