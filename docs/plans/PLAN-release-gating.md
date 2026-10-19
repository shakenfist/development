# Release gating

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every week
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The weekly
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  on the next run, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

**In this repository.** Plans here keep their phases as sections
inside the master plan rather than as separate phase files, and
the Execution table's `Plan` column is dropped accordingly;
`docs/plans/index.md` says so. The shared convention above is the
fleet default, and a plan large enough to want phase files should
use them rather than argue with the block.

## Situation

Measured on 2026-10-18 against `origin/main` at `e0beea8`, and
against GitHub for the fleet. The prompt was client-python-ova#19,
"The release workflow publishes untested and ungated", which the
push audit of that repository's OVF parser plan raised against its
own `release.yml`. Every gap it names is a gap in
`templates/release-automation/release.yml`, so every repository
that copied the template has it too.

Thirteen repositories carry a `.github/workflows/release.yml`:
agent-python, client-python, client-python-k3s, client-python-ova,
clingwrap, divergulent, hunkydory, instar, kerbside,
library-utilities, occystrap, ryll and shakenfist. All thirteen use
`develop` as the default branch and cut their tags from it.

**1. Nothing checks that a release waits for a human.** The template
puts `sign-tag` and `publish-pypi` behind `environment: release`,
and `RELEASE-SETUP.md:75-91` says to give that environment a
required reviewer and restrict it to `v*` tags. Whether anyone did
is invisible to the audit: no criterion reads `environments` or
`deployment-branch-policies` (the only planned readers are
`bypass-actor-identity` in `PLAN-bot-bypass-identity.md:639` and
`moving-ref-source-protection` in `PLAN-workflow-ref-trust.md:519`,
neither started). On the morning of this plan:

| Repository | `release` environment | `v*` tags | Exposure |
|---|---|---|---|
| shakenfist | existed with no protection rules and no deployment policy, holding one secret | 112 | every release unreviewed, and any branch's workflow could name the environment and read the secret |
| clingwrap | did not exist | 43 | the next tag would create it unprotected, which is GitHub's behaviour for a referenced environment that does not exist |
| client-python-ova | did not exist | 0 | the same, on its first tag |
| divergulent, instar, kerbside, ryll | required reviewer, no deployment policy | | gated by the reviewer, but deployable from any ref |
| the other six | required reviewer and a `tag: v*` policy | | the shape `RELEASE-SETUP.md` asks for |

The first three were repaired by hand the same morning, before this
plan was written, to match client-python's environment: `mikalstill`
as required reviewer, `custom_branch_policies: true`, and a single
`tag: v*` policy, each read back afterwards. They are recorded under
Bugs fixed. The four in the fourth row are left for phase 1, so the
criterion that would have caught them is the thing that confirms
them fixed.

**2. No release waits for CI.** The template's `build` job
(`release.yml:24-56`) builds, runs `twine check` and uploads; no job
runs tests or depends on a CI result. Across the fleet, ryll alone
runs tests in its release (`make test` and `cargo test --workspace`);
instar's and ryll's `check-version` jobs compare the tag with the
`Cargo.toml` version and nothing else. So the only thing between a
broken commit and PyPI is whoever approves the environment, reading
a deployment review page that does not show CI status.

That this matters is not hypothetical. kerbside v0.7.0 (`2d9ee91`)
was released at 03:08 today. Its pull request, #576, was merged
outside the merge queue at 02:59 after the queue's functional test
run failed. GitHub holds no `push` or `merge_group` CI run for that
commit; the one push-triggered workflow that ran on it, Demo
compose, failed.

Would "require a successful CI run on the tagged commit" have
passed each repository's most recent tag?

| Repository | CI triggers that reach the tagged commit | Last tag | CI on that exact commit |
|---|---|---|---|
| agent-python | none (`pull_request` and dispatch only) | v1.0.1 | none |
| client-python | none (`pull_request` only) | v0.8.3 | none |
| clingwrap | none (`pull_request` only) | v2.1 | none |
| shakenfist | `merge_group` | v0.7.2 (2024, a `v0.7-releases` branch) | none |
| client-python-k3s | `merge_group` | v0.3.0 | success |
| divergulent | `push: develop` | v0.1 | success |
| library-utilities | `push: develop` | v0.8.9 | success |
| occystrap | `push: develop`, `paths-ignore: docs/**, **.md` | v0.4.17 | success |
| client-python-ova | `push: develop` | no tags | |
| hunkydory | `push: develop`, `paths-ignore` the review files | v0.2.0, on a `prune-reviews` commit | skipped by `paths-ignore`; the parent passed |
| ryll | `merge_group` | v0.3.0, on a `prune-reviews` commit pushed directly | none; the parent (#573) passed in the queue |
| instar | `merge_group` | v0.3.0, a release commit pushed directly | none |
| kerbside | `merge_group` | v0.7.0 | none; the queue run failed |

Four of twelve would have passed. The failures are of three kinds,
and each shapes the design. A `pull_request` run's `head_sha` is the
pull request head, never the merge commit, so a repository whose CI
runs only on pull requests has no signal to read at all. A commit
that `prune-reviews.yml` pushes changes only `REVIEWS.md` and
`.vscode/*.weaudit*`, gets no CI run, and is often the tip of
`develop` when someone tags. And kerbside and instar are exactly the
cases a gate exists for. One implementation trap: a `head_sha=`
query also returns `schedule` runs (Renovate, CodeQL, export-config)
while the tagged commit is still the tip, so "any successful run" is
the wrong test.

**3. Nothing checks what a distribution contains.** The latest wheel
of each Python package on PyPI, downloaded and listed:

| Package | Version | Members under a `tests/` directory |
|---|---|---|
| divergulent | 0.1 | 57 |
| kerbside | 0.7.0 | 30 |
| shakenfist | 0.7.2 | 29 |
| occystrap | 0.4.17 | 17 (`occystrap.tests` is listed in `packages` deliberately) |
| shakenfist-client | 0.8.3 | 4 |
| shakenfist-agent | 1.0.1 | 2 |
| shakenfist-utilities | 0.8.9 | 2 |
| clingwrap | 2.1 | 1 |
| shakenfist-client-k3s | 0.3.0 | 0 |

The mechanism is the one client-python-k3s's `tools/check-dist.sh`
documents: under `pyproject.toml`, `include-package-data` defaults to
true, and setuptools_scm's file finder offers every git-tracked file
as package data, so a `packages.find` exclude drops the tests
*package* and the tests come back as data. k3s and client-python-ova
both set `include-package-data = false`; only k3s inspects its wheel,
in CI and again in its release. kerbside's `tools/check-wheel.py`
checks that runtime files are present, not that tests are absent.
The existing `release-artifacts-exclude-plans` criterion
(`packaging.py:2016`) guards the sdist against `docs/plans/` and
nothing else.

**What the audit already measures.** `release-process`
(`scripts/audit/checks/packaging.py:831-858`) skips repositories
without `pyproject.toml` and fails on `release.sh` or
`requirements.txt`, or on a missing `release.yml` or
`RELEASE-SETUP.md`. It calls five helpers: `release_asset_issues`
(:433), `release_dispatch_guard_issues` (:771),
`release_workspace_issues` (:545), `dist_agreement_issues` (:695) and
`release_container_path_issues` (:626). That is the natural home for
the workflow-shape rules this plan adds. Environment protection is
GitHub configuration, not file content, and belongs beside
`DeleteBranchOnMerge` (`github_config.py:217-242`) and
`MergeQueueConfig` (:245-280).

**The token.** Checks run under `AUDIT_READ_TOKEN`
(`.github/workflows/consistency-audit.yml:120,140,192`), whose
documented permissions are Metadata, Contents, Actions and
Administration, all read (`docs/consistency-audits.md:107-125`).
Nothing records whether that is enough to read an environment or its
deployment policies. `PLAN-bot-bypass-identity.md` step 3c assumes it
can and names `AUDIT_TOKEN` while doing so, which is the wrong token
since #278 moved `AUDIT_TOKEN` into the `audit-write` environment.

**Relation to other plans.** `PLAN-workflow-ref-trust.md` phase 5
(:564-615, not started) may turn copied templates into callers of
shared workflows. It never names `release.yml`; step 5a classifies
"every template directory under `templates/`", so the release
template is in scope only by implication. `PLAN-typescript-onboarding.md`
met the unprotected-environment problem in hunkydory as HD-1
(:1934) and fixed it by hand in 7b.2, without proposing a check.
`PLAN-bot-bypass-identity.md` owns the `develop` ruleset bypass that
let kerbside's merge skip the queue; this plan does not touch it.

## Mission and problem statement

Make three things true of every repository that publishes through a
`release.yml`, and make the audit say so every week, so that they
stop depending on someone remembering `RELEASE-SETUP.md`:

1. **A release waits for a human.** Every environment a release job
   names exists, has a required reviewer, and can only be deployed
   from a tag.
2. **A release waits for CI.** Nothing is signed or published unless
   the repository's CI succeeded on the commit the tag points at, or
   on the nearest ancestor that differs from it only by review
   marks.
3. **A wheel contains what was meant.** No test package or fixture
   reaches a published wheel, checked in CI on every pull request and
   again in the release.

Out of scope, each for a stated reason:

* **Tag rulesets** (who may push a `v*` tag). Five repositories have
  one. A tag push is what starts a release, and the reviewer gate
  now sits after it, so the ruleset is defence in depth rather than
  the gate. Future work.
* **The `develop` ruleset bypass** that kerbside's merge used.
  `PLAN-bot-bypass-identity.md` owns it. This plan makes that bypass
  unable to reach PyPI, which is the half of the problem that is
  ours.
* **Turning `release.yml` into a shared workflow.** That is
  `PLAN-workflow-ref-trust.md` phase 5's decision to make. This plan
  puts the new logic in shared composite actions, so whichever way
  phase 5 goes, the logic exists once.
* **sdist contents.** An sdist carrying its tests is conventional
  and harmless, and `release-artifacts-exclude-plans` already guards
  the one thing in it that should not ship.
* **hunkydory's unsigned tags**, which `templates/release-automation/README.md`
  already records as a deliberate per-repository choice.

## Decisions

1. **Gate on the CI result for the tagged commit; do not re-run the
   tests in the release.** This is the decision a reviewer is most
   likely to push back on, because running the tests is the obvious
   fix and is what client-python-ova#19 half-suggests. It is the
   wrong fix here. CI is already the fleet's definition of "tested",
   and a second copy of it in the release would drift from the
   first. The functional tiers need infrastructure that only CI
   workflows set up, and run for 25 to 100 minutes (instar 100,
   client-python-k3s 76, kerbside 73), so a release would either
   carry that cost or run a cheaper tier and call it tested. Neither
   is better than asking CI. Reading the result also catches the case
   that actually happened: kerbside's release would have stopped at
   the gate, because its merge had no passing CI. The cost is that
   three repositories (agent-python, client-python, clingwrap) need
   a CI trigger that reaches the merged commit before they can
   release, and phase 3 adds one.
2. **Accept an ancestor only across review-mark commits, with a fixed
   allowlist.** The gate walks first-parent ancestors from the tagged
   commit and stops at the first one with a qualifying CI result. It
   accepts that result only if every commit it crossed changed
   nothing but `REVIEWS.md` and `.vscode/*.weaudit*`, the files
   `prune-reviews.yml` writes. The allowlist is a constant in the
   action, not an input. A per-repository input would grow "docs
   only" and then "anything that is not code", and a release would
   be gated by whatever the last person to edit the list thought was
   harmless. A tag on a docs-only commit that `paths-ignore` skipped
   (occystrap) therefore fails closed, and the failure message says
   to tag the last commit CI ran on. That is a one-command recovery
   before anything is published.
3. **A qualifying result is the latest attempt of each named
   workflow, run on `push` or `merge_group`, concluding `success`.**
   The caller names the CI workflow files that must have passed. The
   input is required and has no default, because "CI" is a different
   set of files in each repository (occystrap has two). Runs from any
   other event are ignored, which removes the scheduled-run trap. A
   `push` run counts only if its `head_branch` is the default branch,
   and a `merge_group` run only if its ref is the default branch's
   queue (`gh-readonly-queue/<default>/...`). A push to any other
   branch runs the workflow file as that branch has it. So anyone who
   can push a branch could edit CI there to succeed trivially, then tag
   the commit. A workflow with no run for the commit is a failure, not
   a pass, and the message names it. So is a partial set: if some of
   the named workflows qualify on a commit and others have no run
   there, the gate fails naming the missing ones, rather than walking
   past a commit that CI half-tested. Every qualifying run is judged
   at its latest attempt, so a failed `merge_group` run is not
   outranked by a later successful `push` run on the same commit.
   Runs from a fork do not count. And before any run is judged, the
   tagged commit must be on the default branch's first-parent
   history, because a run's metadata cannot tell a tag from a branch:
   a tag named `develop` produces `push` runs whose `head_branch` is
   `develop`. Phase 2 added these three rules in review; its
   execution notes say why.
4. **No override input.** A CI run that failed for infrastructure
   reasons is re-run in CI, and then the gate job is re-run through
   "Re-run jobs", which keeps the original `push` event, the same
   path `release.yml:56-66` already documents for the publish guard.
   An override would be one more thing the environment reviewer has
   to notice.
5. **The gate and the wheel check are composite actions in
   `shakenfist/actions`**, as `require-ci-success` and `check-dist`,
   called at `@main` like the fleet's other first-party actions.
   Thirteen hand-adapted copies of a gate are thirteen places for it
   to rot, and the template README already says copies drift
   (`templates/release-automation/README.md:6-14`). The actions are
   the single implementation; each `release.yml` gains a few lines
   calling them. This also keeps scripts out of workflow steps.
6. **`ci-gate` is its own job, and `build` needs it.** Every
   publishing job already needs `build`, so the gate sits upstream
   of all of them without editing each `needs:`. It runs on the
   static pool with `actions: read` and nothing else, and carries the
   same `push` and tag guard as the publishing jobs, so a
   `workflow_dispatch` dry run still exercises the build.
7. **`check-dist` checks wheels only, and fails on any member with a
   directory component named `tests` or ending in `_tests`.** That
   catches every case in the table above without a per-repository
   count. client-python-k3s's member-count ceiling stays where it is,
   as that repository's stricter local check. A `forbid` input takes
   extra globs for a repository that wants them. Rust repositories
   call it only where they publish Python wheels (kerbside's proxy
   wheels, ryll's `build-ryll-wheels`); instar and hunkydory publish
   none and skip it.
8. **The environment criterion is `release-environment-protection`,
   in `github_config.py`.** It applies where
   `.github/workflows/release.yml` exists, and otherwise reports
   `not_applicable` with that reason. For every environment that a
   job in `release.yml` names, it requires that the environment
   exists; that it has a `required_reviewers` rule with at least one
   reviewer; that `deployment_branch_policy.custom_branch_policies` is
   true; and that the deployment policies are non-empty and all of
   type `tag`. A 404 on the environment is a fail with the reason
   that GitHub would create it unprotected, read through
   `http_status()` the way `ScopeCoverage` does
   (`github_config.py:283-291,447-467`). Any other API failure is a
   `ToolFailure`. It does not judge `prevent_self_review`, which must
   stay off in an organisation with one maintainer or nobody could
   release, nor `can_admins_bypass`; the spec puts both in its
   reviewer-confirmed half.
9. **The workflow-shape rules join `release-process` as helpers, not
   new criteria**, and land last (phase 4), after the fleet has
   adopted the gate. Phase 3 makes each rule true, and phase 4 starts
   measuring it. The reverse order would file "Release process"
   failures in twelve repositories on the first weekly run after
   merge, for work that is already scheduled. There are three
   helpers: `ci-gate` exists, uses `require-ci-success`, and is
   upstream of every job that names an environment; every workflow it
   names exists and triggers on `push` to the default branch or on
   `merge_group`; and the `build` job runs `check-dist` on what it
   uploads, where it uploads a wheel. The CI half of the wheel check,
   which runs on pull requests, is a recommendation in the template
   rather than a measured rule, because the release copy is the one
   that stops a bad wheel.
10. **Phase 3 is one pull request per repository, carrying all of
    that repository's changes.** These are the gate, the wheel check,
    any CI trigger the gate needs, and the packaging fix. One review
    per repository is cheaper than three, and the changes test each
    other: the packaging fix is exactly what makes `check-dist` pass.

## Open questions

1. **Does `AUDIT_READ_TOKEN` read environments?** The criterion
   needs `GET repos/{o}/{r}/environments/{name}` and
   `.../deployment-branch-policies`. Fine-grained tokens list
   Environments as a separate permission from Administration. Step 1a
   probes it. **Default:** if it cannot, the operator adds
   Environments: read to the token before step 1c merges. Widening a
   read token by one read permission is cheaper than an audit that
   cannot see the thing it exists to check.
2. **Does occystrap still need `occystrap.tests` in its wheel?** It
   lists the package deliberately. **Default:** remove it, and run the
   functional tests from the checkout, as every other repository
   does. If there is a consumer of the installed tests that this plan
   cannot see, the operator says so in step 3b and occystrap carries a
   `forbid`-free `check-dist` call with an `audit-ok` reason instead.
3. **shakenfist's `v*-releases` branches.** Its last tag came from
   one, and its CI triggers on `merge_group` for `develop` only.
   **Default:** none exist today, so the gate is adopted against
   `develop`, and the step brief records that cutting a release
   branch again means adding that branch to the CI triggers in the
   same change.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Release environment criterion | Complete | b87012d (#288) |
| 2. Shared `require-ci-success` and `check-dist` actions | Complete | actions 69d4589 (#182) |
| 3. Template and fleet adoption | Not started | |
| 4. `release-process` helpers | Not started | |
| 5. Push audit | Not started | |

Phases run in order. Phase 2 lands in `shakenfist/actions` and
phase 3 lands in the template and in twelve other repositories, so
their `Merged` cells take the `<repo> <sha> (#pr)` form, one entry
per repository.

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

**In this repository.** The same term is written twice: once in
this plan's own phase table, and once in the row the plan carries
in `docs/plans/index.md`. The index row is the whole-plan status,
so it only reaches `Complete` once every phase has been
completed, abandoned or superseded. The `plan-index` criterion
reads that table, and this repository is inside its own audit
matrix, so a status that drifts out of the vocabulary fails our
own tooling before it fails anybody else's.

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

**In this repository.** `PUSH-AUDIT.md` exists at the repository
root and is referenced from `AGENTS.md`, so the final phase runs
it rather than explaining its absence. Note that every diff
command in it is written against `main...HEAD`: a stale local
`main` silently widens the audit to unrelated history, so fetch
before starting, or read it as `origin/main...HEAD`.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

**In this repository.** The plan index is `docs/plans/index.md`.
`review-tracking-tests` deliberately does not assert the `REVIEWS.md`
header count, so a phase that adds or removes an in-scope file needs
no regeneration commit; `prune-reviews` corrects the count on the
next push to main.

### Phase 1: release environment criterion

Planning effort: medium. The criterion follows `DeleteBranchOnMerge`
and `ScopeCoverage`; what needs care is the 404 meaning and the
token.

Blast radius: after step 1b, newly fails **zero** repositories,
including this one, which has no `release.yml` and reports
`not_applicable`. Without 1b, it would newly fail four: divergulent,
instar, kerbside and ryll.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | operator | none | **Operator, not a sub-agent.** With `AUDIT_READ_TOKEN` exported as `GH_TOKEN`, run `gh api repos/shakenfist/client-python/environments/release --jq .protection_rules` and `gh api repos/shakenfist/client-python/environments/release/deployment-branch-policies --jq .branch_policies`. Both must return data. If either returns 403, add Environments: read to the token and repeat. Record the outcome in this section and in `docs/consistency-audits.md:107-125`'s permission list. |
| 1b | low | operator | none | **Operator, with approval at execution time.** For divergulent, instar, kerbside and ryll, `PUT repos/shakenfist/<r>/environments/release` with `{"reviewers":[{"type":"User","id":1530643}],"prevent_self_review":false,"wait_timer":0,"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}`, then `POST .../deployment-branch-policies` with `name=v*`, `type=tag`, and read both back. Before that, check that each repository's release really tags `v*`: instar's and ryll's `check-version` jobs read the tag, so confirm the prefix in their `release.yml` `on.push.tags`. kerbside also publishes proxy wheels from the same environment, so confirm it names no second environment. |
| 1c | medium | opus | none | Add a `ReleaseEnvironmentProtection` class to `scripts/audit/checks/github_config.py` beside `MergeQueueConfig` (:245-280), with `id='release-environment-protection'`, `spec='docs/audits/release-environment-protection.md'`, `template=None` and `issue_title='Release environment protection'`. `applies(repo)` returns a skip reason when `repo.workflow('release.yml')` is None (see `packaging.py:784-786` for the call). `run(repo)` collects every `environment:` value from the jobs of the parsed workflow (a string, or a mapping with `name`), then for each one GETs `repos/{org}/{name}/environments/{env}` using raw `repo.github.api()` and `http_status()` as `ScopeCoverage` does (:283-291, :447-467). A 404 fails, saying GitHub would create the environment unprotected on first use. Any other non-zero status is `ToolFailure`. On success, fail unless there is a `protection_rules` entry of type `required_reviewers` with a non-empty `reviewers` list, and unless `deployment_branch_policy` is non-null with `custom_branch_policies` true. Then GET `.../deployment-branch-policies` and fail unless `branch_policies` is non-empty and every entry has `type == 'tag'`. Each failure names the environment and the missing property, and points at `RELEASE-SETUP.md`. Register it in `CHECKS` in `scripts/audit/registry.py` beside the other `github_config` entries (:90-95). Add the id to `NETWORK_CHECKS` in `scripts/audit_snapshot.py:34`. It does not join the empty-directory tool-failure list in `scripts/tests/test_metadata.py:633-637`, because `applies()` skips an empty directory before any API call. Write `docs/audits/release-environment-protection.md` following `docs/audits/README.md`, with a "Required, but confirmed by a reviewer" half covering `prevent_self_review` and `can_admins_bypass`, and why neither is measured. Add its row to the README table (:180-), and its lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`. In `scripts/tests/test_github_config.py`, using `GithubConfigTestCase` (:65-81) and `FakeGitHub`, test: a pass; no `release.yml` (`not_applicable`); a 404 environment; no reviewers rule; a reviewers rule with an empty list; a null deployment policy; `protected_branches` instead of custom; an empty policy list; a `branch`-type policy beside a tag one; two environments with one failing; a 403 (`ToolFailure`); and a timeout. Follow the six-file list in `docs/consistency-audits.md:403-`. |
| 1d | low | sonnet | none | Run `pre-commit run --all-files`, then `python3 scripts/audit-check.py --repo-path . --repo-name development` and confirm `release-environment-protection` is `not_applicable` and nothing else moved. Then run the single check against fresh clones of the thirteen release repositories, all listed in Situation, and confirm all thirteen pass. Report any that do not, with the failure text. Do not run `audit-manage-issues.py` except with `--dry-run`. |

#### Phase 1 execution notes

Run on 2026-10-19 (AEDT).

* **1a.** The operator probed `AUDIT_READ_TOKEN` against
  client-python's `release` environment. Both endpoints returned data,
  so the token's existing permissions are enough, and open question 1
  is settled without widening it. `docs/consistency-audits.md` records
  this beside the token's permission list.
* **1b.** The operator approved the change, and the management session
  made it. divergulent, instar, kerbside and ryll each now have a
  single `tag: v*` deployment policy and `mikalstill` as required
  reviewer, and all four were read back. Each `release.yml` was first
  confirmed to tag `v*` and to name only `release`. None of the four
  environments holds a secret. The `PUT` replaced each reviewer list
  without reading it first, and the organisation audit log is not
  available on this plan, so the previous lists cannot be confirmed.
  Every other release environment in the fleet lists only
  `mikalstill`.
* **1c.** opus at medium effort. Departures from the brief:
  * The empty-directory tool-failure list was left alone, because
    `applies()` skips before any call. The brief has been corrected
    above.
  * The agent made two choices beyond the brief, and both were kept
    because they fail closed. A job whose `environment:` is an
    expression fails, asking for a literal name. A `release.yml` that
    names no environment fails.

  It added 20 tests, and the first review round added five more. A mutation script flipped the tag-type test and
  the 404 branch, and the tests caught each. The full suite passes.
* **1d.** sonnet at low effort, against fresh shallow clones. All
  thirteen release repositories pass, sfui (no `release.yml`) reports
  `not_applicable`, and so does this repository. The management
  session reproduced the shakenfist and sfui verdicts. The predicted
  blast radius of zero newly failing repositories holds.

### Phase 2: shared `require-ci-success` and `check-dist` actions

Planning effort: high. This is the gate itself, and a gate that
passes when it should not is worse than none, because it is trusted.

Lands in `shakenfist/actions` (default branch `main`), as composite
actions in top-level directories beside `pr-review-ref/`, with tests
under its `tests/`, following that repository's own conventions and
`AGENTS.md`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | worktree | Add `require-ci-success/action.yml` and its script, in Python, standard library only, and run with `python3 -I`. Inputs: `workflows` (required, a newline-separated list of workflow file names, empty is an error), `sha` (default `github.sha`), and `token` (default `github.token`; the caller grants `actions: read` and `contents: read`). Behaviour: starting at `sha`, for each candidate commit, query `repos/{repo}/actions/workflows/{file}/runs?head_sha={c}` for each named workflow. Keep only runs whose `event` is `push` with `head_branch` equal to the repository's default branch (read it from `repos/{repo}`), or `merge_group` with `head_branch` starting `gh-readonly-queue/<default>/`. Take the one with the highest `run_attempt` of the newest `run_number`, and require `status == completed` and `conclusion == success`. If every named workflow qualifies, pass, and print which commit and which run URLs did. If some named workflows qualify on the commit and others have no qualifying run there, fail without walking, naming the missing ones. If no named workflow has any qualifying-event run for the commit, step to its first parent, but only if the commit's diff against that parent touches nothing outside the fixed allowlist `REVIEWS.md` and `.vscode/*.weaudit*` (read via `repos/{repo}/commits/{c}`). Otherwise fail. Stop and fail after 20 steps. Any run that exists and did not succeed (failed, cancelled, still in progress) fails immediately, without walking. Each failure message names the commit, the workflow and the observed state, and for the walk case says to tag the last commit CI ran on. Write the allowlist as a module constant with a comment saying why it is not an input: a per-repository list grows. Tests in `tests/test_require_ci_success.py` with a stubbed HTTP layer, covering: success on the exact commit; a scheduled success ignored, then a fail; a `pull_request` success ignored; a `push` success on a feature branch ignored, then a fail; a `merge_group` success on another branch's queue ignored; a failed latest attempt after an earlier success; success after a re-run attempt; one of two workflows passing and the other with no run on the commit (fails without walking, naming the missing one); a walk across one and across two review-mark commits; a walk refused because a crossed commit touched `README.md`; the 20-step cap; an in-progress run; empty `workflows`. Document the action in `docs/` following the repository's pattern for its other actions. |
| 2b | medium | sonnet | none | Add `check-dist/action.yml` and a standard-library Python script generalising `shakenfist/client-python-k3s/tools/check-dist.sh` (read it first; keep its reasoning about using `zipfile` names rather than parsing `-l` output). Inputs: `path` (default `dist/`), and `forbid` (optional newline-separated globs). For every `*.whl` under `path`, fail if any member path has a directory component equal to `tests` or ending in `_tests`, or matches a `forbid` glob. Print every offending member, and a one-line hint that `include-package-data = false` in `pyproject.toml` is the usual fix, citing the setuptools_scm file-finder mechanism. Having no wheel under `path` is an error. Tests in `tests/test_check_dist.py` build small zip files in a temp directory: clean; `pkg/tests/test_x.py`; `pkg/corpus_tests/x`; a `forbid` hit; a member named `contests.py` (must pass, since only directory components count); no wheels; a non-zip `.whl`. Document it beside 2a. |
| 2c | low | sonnet | none | In `shakenfist/actions`, run its full check (`pre-commit run --all-files`, plus the test command its `AGENTS.md` names) and confirm both new test files run in CI, not just locally. |

#### Phase 2 execution notes

Run on 2026-10-19 (AEDT), in a worktree of `shakenfist/actions`, and
merged as actions#182 at 69d4589.

* **2a.** opus at high effort, as briefed; commit 6b47526. It shipped
  with 46 tests, and thirteen mutations each failed the suite.
  Departures from the brief, all kept because they fail closed:
  * The allowlist is the three exact patterns `REVIEWS.md`,
    `.vscode/*.weaudit` and `.vscode/*.weaudit-shas.json`, matched
    within one directory, rather than the looser `.vscode/*.weaudit*`.
    A renamed file is judged by its old name as well as its new one.
  * The walk refuses a merge commit, a commit with no files listed,
    and a commit whose file list may be truncated at the commits API's
    300-file cap.
  * Only runs whose `head_repository` is this repository count, so a
    fork's run does not.
  * Every qualifying run is judged at its latest attempt, not only the
    newest `run_number`, so a failed `merge_group` run is not
    outranked by a later successful `push` run. Decision 3 has been
    corrected above to say so.
* **2b.** sonnet at medium effort; commit b111e0d, with 21 tests. Two
  extras were kept. It fails on a backslash in a member name, which no
  correct wheel has. It also fails on an unreadable `.whl`, rather
  than skipping it.
* **2c.** The full check passed, and both test files run under the
  repository's CI test step.
* **A run still in progress fails the gate immediately.** The
  operator chose this over waiting. The recovery is to wait for the
  run, then re-run the gate job.
* **The push audit ran before the pull request opened.** The operator
  chose this order. Wave 1 used gitleaks v8.28.0, downloaded for the
  run because the runner had none, with a planted secret as the
  positive control. Wave 2's security reader found that a tag named
  like the default branch spoofs a run's `head_branch`. This was
  confirmed against kerbside's `v0.7.0` tag run, whose `head_branch`
  is `v0.7.0`. Commit 43b6850 fixed it by requiring the tagged commit
  to be reachable from the default branch.
* **Review round 1** found that reachability still admits a commit
  from a feature branch that was merged with a merge commit. Commit
  5b11e51 requires the default branch's first-parent history instead.
  It reads that history from the tip's sha, not the branch name,
  which a branch-named tag would make ambiguous. It reads at most 500
  listed commits, and fails closed beyond that.
* **Review round 2** raised no `fix` items. Commit f53cd9b took two
  `consider` items, both defects in code this phase added:
  * `check-dist` now escapes workflow-command characters in member
    names;
  * a merge queue `head_branch` must match
    `gh-readonly-queue/<default>/pr-<n>-<sha>` whole.
* **The final tree** has 758 tests, and all 42 mutants are caught. It
  gave these verdicts when run read-only against live repositories:
  * kerbside `v0.7.0` (2d9ee91) fails: it is a merge commit with no CI
    run, and the gate exists to stop exactly this release;
  * client-python-k3s `v0.3.0` passes on the exact commit;
  * ryll `v0.3.0` passes, after stepping across one review-mark commit;
  * kerbside 664068f, a second-parent commit, is refused.
* **The docs recommend `on: push: branches:` for gated CI.** Gated CI
  workflows should restrict pushes to branches, so that a tag push
  starts no CI at all. The `actions` docs say so, and phase 3 applies
  it where a repository's CI triggers on every push.

### Phase 3: template and fleet adoption

Planning effort: high, for the per-repository packaging judgement.
The workflow edits are mechanical.

Each repository gets one pull request (decision 10). The step for
each must:

* add the `ci-gate` job (decision 6) and make `build` need it;
* call `check-dist` in `build` after `twine check`, and in the CI
  job that already builds a wheel where there is one;
* add whatever CI trigger the gate needs;
* fix packaging until `check-dist` passes, by diffing the wheel's
  member list before and after and stating every removed member.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | In `templates/release-automation/release.yml`, add a `ci-gate` job on `[self-hosted, static]` with `permissions: {actions: read, contents: read}`, the same `if:` guard as `sign-tag` (:71-72), and one step: `uses: shakenfist/actions/require-ci-success@main` with `workflows:` set to a `{{CI_WORKFLOWS}}` placeholder. Make `build` `needs: ci-gate`. Because of that `if:`, a `workflow_dispatch` run would skip `build`, so give `build` `if: ${{ !cancelled() && (needs.ci-gate.result == 'success' \|\| github.event_name == 'workflow_dispatch') }}` and explain the expression in a comment. The `${{ }}` wrapper is required, because YAML reads a bare value starting with `!` as a tag. Add a `check-dist` step after `twine check`. Update `templates/release-automation/README.md`: add the placeholder row; add a section explaining the gate, the review-mark allowlist, the recovery path (re-run CI, then "Re-run jobs" on the gate), and why there is no override; and add a prerequisite that CI must run on `push` to the default branch or on `merge_group`. Update `RELEASE-SETUP.md` to say the reviewer approves a release whose CI has already been checked, and to point at the `release-environment-protection` criterion. Note in the README that `PLAN-workflow-ref-trust.md` phase 5 must carry the `ci-gate` job if it converts this template. |
| 3b | high | opus | worktree | One sub-agent per repository, in its own clone, with the template from 3a as the pattern. Each `release.yml` is a per-repository adaptation, so apply the change by hand; never copy the template over the file. The per-repository facts are below this table. For each repository: make the edits listed above this table; build the wheel before and after and report the member diff; run the repository's own pre-commit; and push a branch, but do not open a pull request. The management session opens them after review. The occystrap step waits for open question 2's answer before removing `occystrap.tests`. |

Per-repository facts for 3b. `workflows` is what `ci-gate` names.
The trigger column is the change the gate needs. The wheel column
counts the test members found on PyPI.

| Repository | `workflows` | Trigger change | Wheel |
|---|---|---|---|
| agent-python | `functional-tests.yml` | add `push: branches: [develop]` | 2 test members |
| client-python | `functional-tests.yml` | add `push: branches: [develop]`; it also has a `v*-releases` PR trigger, apply open question 3's rule | 4 |
| clingwrap | `functional-tests.yml` | add `push: branches: [develop]` | 1 |
| shakenfist | `functional-tests.yml` | none (`merge_group`) | 29; check what package data it genuinely ships before turning `include-package-data` off |
| client-python-k3s | its functional tests workflow | none | 0; replace its release-side `check-dist.sh` call with the action and keep the CI-side count check |
| divergulent | `unit-tests.yml` | none | 57 |
| library-utilities | `ci.yml` | none | 2 |
| occystrap | `python-unit-tests.yml` and `functional-tests.yml` | none; `paths-ignore` stays, and decision 2's failure message covers a docs-only tag | 17; open question 2 |
| client-python-ova | `ci.yml` | none | 0 expected, so `check-dist` should pass unchanged; the PR closes client-python-ova#19 |
| hunkydory | `ci.yml` | none | no wheel; gate only |
| ryll | `ci.yml` | none | its Python wheels from `build-ryll-wheels`; its release already runs tests, which stay |
| instar | `functional-tests.yml` | none | no wheel; gate only |
| kerbside | `functional-tests.yml` (and others its queue requires; confirm against its ruleset's required checks) | none | 30, in the main wheel and possibly the proxy wheels; `tools/check-wheel.py` stays |

### Phase 4: `release-process` helpers

Planning effort: medium. These are three more helpers in the shape of
the five already in `ReleaseProcess`.

Blast radius: newly fails **zero** repositories once phase 3 has
merged everywhere. Step 4b measures that before merge, and a
repository whose phase 3 pull request has not merged holds this phase
rather than taking a failure.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | opus | none | In `scripts/audit/checks/packaging.py`, add three helpers called from `ReleaseProcess.run` (:831-858), each returning a list of issue strings like `release_dispatch_guard_issues` (:771). (1) `release_ci_gate_issues`: some job has a step that `uses: shakenfist/actions/require-ci-success@...`, and every job that names an `environment:` is downstream of it through `needs:` (walk the needs graph; a cycle or unknown job name is an issue, not a crash). Being downstream is not enough on its own. A job whose `if:` contains `always()` or `!cancelled()` runs even when an upstream job failed, so every job on the path from the gate to an environment job must either have no such guard, or require `needs.<job>.result == 'success'` for the job it depends on. `build`'s own guard in the template passes this test only because the `workflow_dispatch` branch of it leads to no environment job, since the publishing jobs require a `push` event. The helper checks that the publishing jobs' guards exclude `workflow_dispatch`, rather than taking it on trust. (2) `release_ci_trigger_issues`: every file in that step's `workflows` input exists under `.github/workflows/` and has `push` with the default branch in `branches` (or no branch filter), or `merge_group`. (3) `release_check_dist_issues`: wherever a job runs `python -m build` or uploads a `*.whl`, a `check-dist` step follows in that job. Since `ReleaseProcess.applies` skips repositories without `pyproject.toml`, also add the first two to hunkydory's path, the language-neutral case `PLAN-typescript-onboarding.md:637-665` describes, without changing what the skip means for the existing helpers. Update `docs/audits/release-process.md` to describe each rule and its reason. Tests in `scripts/tests/test_packaging.py` beside the existing `ReleaseProcess` tests: each helper's pass, fail, and the edge cases named here. No new criterion, so no metadata tables change; confirm `test_metadata.py` still passes. |
| 4b | low | sonnet | none | `pre-commit run --all-files`; `audit-check.py` against this repository, unchanged; and `release-process` against fresh clones of the thirteen, reporting any failure with its text. |

### Phase 5: push audit

Runs `PUSH-AUDIT.md` over every recorded landing in the Execution
table, across `development`, `actions` and the twelve adopting
repositories. Checklist item 5 is the centre of it: for each
repository, confirm with a `workflow_dispatch` dry run (which skips
the publish jobs by design) that `build` and `check-dist` still run,
and confirm by reading that `ci-gate` is upstream of every
environment.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

**In this repository.** High effort is anything that changes what
a criterion means, anything that touches the scheduler in
`audit-check.py`, and anything that reaches
`audit-manage-issues.py` -- those decide what the fleet is held
to and what lands in other people's issue trackers. Medium effort
covers adding a criterion that follows the shape of an existing
one, a documentation sweep, or a template change with a worked
example already in the tree.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

**In this repository.** A worked brief: instead of "add a check
that plans are indexed", write "add a `PlanIndex` class to
`scripts/audit/checks/plans.py` declaring the id `plan-index`, its
`spec` and its `issue_title` as class attributes and implementing
`run(repo)`, register the instance in `CHECKS` in
`scripts/audit/registry.py` beside the other plan checks, write
`docs/audits/plan-index.md` following the structure in
`docs/audits/README.md` and linking to `compliance.md#plan-index`,
add the file to `docs/audits/README.md`, add its lines to
`FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
`scripts/tests/test_metadata.py`, and add tests to
`scripts/tests/test_plans.py` covering pass, fail and
not-applicable." A brief that names four of the criterion's five
files is the characteristic defect here, and the frozen tables are
the one most often left out.

A check that does not apply reports
`not_applicable` with a reason rather than being omitted, because
an omitted check renders as `unknown`.

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

**In this repository.** The project-specific checks referred to
above are:

- [ ] `pre-commit run --all-files` passes. It runs actionlint,
      shellcheck, flake8, skillsaw and all five test suites, and
      `ci.yml` runs the same command on every pull request.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the
      change, or the plan says why the verdict moved.
- [ ] If the change touches issue filing, it was exercised with
      `--dry-run` only.

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `pre-commit run --all-files` passes.
* `scripts/audit-check.py` run against this repository reports no
  new failures, or the plan states which verdicts moved and why.
* Any new or changed criterion has all five of its files in step:
  the `Check` subclass in `scripts/audit/checks/<family>.py`, its
  registration in `CHECKS` in `scripts/audit/registry.py`, the
  specification under `docs/audits/`, its row in
  `docs/audits/README.md`, and its frozen lines in
  `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in
  `scripts/tests/test_metadata.py` -- plus a
  `FROZEN_COLUMN_NAMES` line where a spec page carries more than
  one check. `AUDIT_METADATA`, `ISSUE_TITLES` and `COLUMN_NAMES`
  are derived from the registry rather than tables anybody edits;
  the issue title is the fleet-wide idempotency key, so renaming
  one orphans every open issue for that check across the fleet.
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Any entry added to `REPO_OVERRIDES` carries a stated reason.
* Anything under `templates/` is judged as the code it will
  become in ten other repositories: placeholders consistent, no
  reference to paths that only exist here, and the README beside
  it saying what to substitute.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.
* Documentation in `docs/` describes any user-visible change.
  `AGENTS.md` changes only if a convention changed;
  `ARCHITECTURE.md` only if the shape of the system changed;
  `README.md` only if the pitch, the install story or the
  documentation links changed.

### Documentation index maintenance

When creating a new master plan from this template, add one row to
the table in `docs/plans/index.md`: the date the plan was written,
a link to it, a one-line intent, and its status from the
vocabulary above. Rows run oldest first. One row per master plan,
never one per phase -- the phases are tracked in the plan's own
Execution table, and duplicating them in the index is how the two
drift apart.

There is no phase-arithmetic column and no `order.yml` here; both
belong to repositories whose documentation is published through a
generated navigation. The `plan-index` criterion checks the
columns this index actually has.

The index row carries the whole-plan status, so it only reaches
`Complete` once every phase has been completed, abandoned or
superseded. Update it as the plan progresses, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* **Tag rulesets** for the eight release repositories without one
  (agent-python, clingwrap, divergulent, instar, kerbside,
  library-utilities, shakenfist, client-python-ova), and a criterion
  for them. Defence in depth behind the reviewer gate.
* **The `develop` ruleset bypass** that let kerbside#576 merge with a
  failed queue run: `PLAN-bot-bypass-identity.md`.
* **The token named in `PLAN-bot-bypass-identity.md` step 3c** is
  `AUDIT_TOKEN`, but the checks run under `AUDIT_READ_TOKEN`. Step 1a
  here settles whether that token reads environments. 3c should be
  corrected when that plan starts.
* **A `release.yml` that calls a reusable workflow** names its
  environments in the called workflow. `release-environment-protection`
  reads only `release.yml`, so it would report that no environment is
  named. That is the closed direction, but the message is wrong. If
  `PLAN-workflow-ref-trust.md` phase 5 converts the template into that
  shape, the criterion must follow the `uses:` reference, in the same
  change that carries the `ci-gate` job.
* **`check-dist` is tested only by its unit tests** in
  `shakenfist/actions`. Running the action itself in that
  repository's `ci.yml`, against a small fixture wheel, would catch a
  broken `action.yml` before the fleet does. The same goes for
  `require-ci-success`, though that needs a real repository's runs to
  read.
* **A tag ruleset that refuses branch-named tags** would close the
  `head_branch` spoof at its source. The gate already refuses such a
  tag unless it sits on the default branch's first-parent history, so
  this is defence in depth, alongside the tag rulesets above.
* **Re-publishing the affected packages** without their tests is not
  worth a version bump on its own. The next ordinary release of each
  carries the fix.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

* **Unprotected `release` environments** in shakenfist (no rules and
  no deployment policy, with a secret in it), clingwrap (no
  environment) and client-python-ova (no environment). These were
  repaired by hand on 2026-10-18, before this plan was written, to
  match client-python's: `mikalstill` as required reviewer and a
  single `tag: v*` deployment policy. Each was read back afterwards.
  Phase 1's criterion is what keeps them repaired.
* Related issues: client-python-ova#19 is the prompt, and its phase 3
  pull request closes it. client-python-k3s's `tools/check-dist.sh`
  is the prior art for `check-dist`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
