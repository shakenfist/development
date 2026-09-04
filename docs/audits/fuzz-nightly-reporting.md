# Audit: Fuzz nightly reporting

## What we check

Fuzzing is a discovery activity with no natural end. A fuzz target
does not pass or fail in the way a unit test does — it runs until you
stop it, and what it produces is a crash nobody knew about. That makes
it valuable, and it makes it the wrong shape for a merge gate.

Three things follow, and a repository with fuzz targets is checked for
all of them.

**The fuzz targets must run on a schedule.** A `schedule:` trigger on
a workflow that invokes the targets. Fuzzing that only ever happens
when someone remembers to dispatch it is fuzzing that does not happen;
a nightly run is what turns the targets from a directory of code into
a thing that finds bugs.

**The scheduled run must report what it finds as GitHub issues.** This
is the requirement that is easiest to skip and most expensive to skip.
A failing pull request check is impossible to miss — it is standing
between someone and their merge. A failing scheduled workflow is a red
mark on the Actions tab that nobody is looking at, and GitHub's only
notification for it is an email to whoever pushed last, which at 04:00
UTC is nobody's inbox in particular. So a scheduled fuzz lane has to
carry its own route to a human: `issues: write`, and something that
calls `gh issue create` when a target crashes.

**The fuzz lane must not gate the merge queue.** No `merge_group`
trigger on a workflow that runs fuzz targets. A short build-and-smoke
on `pull_request` or on `push` to the default branch is good practice
and is not what this forbids — catching a fuzz target that stopped
compiling is worth ten seconds of a PR. What it forbids is putting the
fuzz lane in the merge queue's path, where its cost is charged against
the queue's timeout.

## Why the merge queue in particular

Merge queues time out. GitHub's `check_response_timeout_minutes` caps
at 360, it is a wall clock from when the merge group forms, and it does
not distinguish a job that is running slowly from one that has not been
given a runner yet. A fuzz matrix asking a shared self-hosted pool for
several runners at the moment a merge group forms is therefore a bet
that the pool is free, settled against a six-hour deadline, with the
PR's place in the queue as the stake.

ryll lost that bet three times in eight days
([shakenfist/ryll#329](https://github.com/shakenfist/ryll/issues/329)).
Its four `Fuzz (*)` jobs were `merge_group`-only, so they asked the
six-worker `l` pool for four runners that its pull request CI never
requested. On the third occurrence the jobs waited six hours, got
runners, and passed — twenty minutes after the queue had already
evicted the PR, leaving a merge group whose run was entirely green and
a pull request that had silently failed to merge. The failure mode is
not "fuzzing found a bug"; it is "fuzzing was queued behind someone
else's build".

## The reference implementation

instar is the worked example, in
[`.github/workflows/coverage-fuzz.yml`](https://github.com/shakenfist/instar/blob/develop/.github/workflows/coverage-fuzz.yml)
and
[`tools/ci/report-fuzz-crash.sh`](https://github.com/shakenfist/instar/blob/develop/tools/ci/report-fuzz-crash.sh).
It fuzzes forty targets nightly against a tiered time budget, a single
target for ten seconds on pull requests, and every target for fifteen
seconds after a merge. It has no `merge_group` trigger.

Four things in it are worth copying and are not obvious:

- **Deduplicate before filing.** A crash that recurs every night must
  become one issue with comments on it, not one issue per night. instar
  keys on the target plus a normalized panic location and message, with
  the numbers in the message collapsed — a panic that interpolates the
  fuzz-derived values that provoked it produces a different string
  every night for one bug.
- **A failure to report must not end the run.** The remaining targets
  still have to be fuzzed. Count the reporting failures and fail the
  job at the end instead.
- **Decide deliberately what turns the run red.** For a campaign that
  reports crashes, red on a crash that was successfully filed is wrong:
  the issue has already reached a human, and going red as well leaves
  the nightly permanently red for as long as any known crash is open —
  which makes it a thing nobody reads, the exact failure this criterion
  exists to prevent, reintroduced one step further along. instar
  therefore fails only on crashes it could not report. A
  build-and-smoke gate is the other case: its failures are "this stopped
  compiling", they get fixed rather than accumulating, so red on the
  failure itself is useful and ryll's `fuzz.yml` does that. What is not
  optional either way is that a failure nobody could file an issue about
  fails the run.
- **Persist the work before failing.** The corpus push and the artifact
  upload run before the step that fails the job. When issue filing is
  the thing that broke, the uploaded artifacts are how the crash
  reaches a human, so they must not be collateral damage.

The reporting logic belongs in a script under `tools/`, not inline in
the workflow YAML. instar's is there because the inline version broke
the nightlies silently for a month: a large crash input made a
`jq --arg` invocation exceed `MAX_ARG_STRLEN`, the step ran under
`bash -e`, and the run aborted at the first crash — no issue filed,
half the targets never fuzzed, corpus push skipped. A script is
testable; `tools/ci/test-report-fuzz-crash.sh` now covers that case.

## Exceptions

A repository whose fuzz lane must run in the merge queue takes an
`audit-ok: fuzz-in-merge-queue` comment in the workflow, ideally with
a reason.

Repositories with no fuzz targets are not applicable.

## Template

No template. The workflow is too shaped by what is being fuzzed to
generalize usefully; copy instar's `coverage-fuzz.yml` and
`tools/ci/report-fuzz-crash.sh` and cut them down. The crash reporter
is the part that generalizes most directly — its input is a target
name, a crash artifact and a log.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](compliance.md#fuzz-nightly-reporting).
