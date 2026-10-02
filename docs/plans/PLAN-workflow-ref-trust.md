# Workflow ref trust

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

Two things make planning here different from planning in a
repository that holds a product, and both should shape any plan
written from this template:

* **The blast radius is other people's repositories.** The daily
  workflow files and closes GitHub issues fleet-wide. A change
  that is merely wrong does not produce a red build; it produces
  issues in ten repositories, or silently closes ones that should
  have stayed open. Always pass `--dry-run` when running
  `audit-manage-issues.py` by hand.
* **This repository is in its own audit matrix.** A standard we
  exempt ourselves from is a standard we stop noticing the cost
  of. A change to a criterion is a change we are measured against
  the next morning, so a plan should say how many repositories --
  including this one -- it newly fails.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

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

[#153](https://github.com/shakenfist/development/issues/153) named
two habits in the fleet's workflows: reusable workflows are called
at `@main` with `secrets: inherit`, and third-party actions are
referenced by a tag rather than a commit sha. Both mean that code
nobody reviewed in the calling repository runs inside its CI.

**The `secrets: inherit` half has already landed** (fe4b3ba,
2026-09-29). The `reusable-workflow-secrets` criterion reports any
job that passes `secrets: inherit` to a reusable workflow, and
leaves two callees to criteria that already owned them
(`export-repo-config`, `ci-review-automation`). The fix in the
calling repositories is still rolling out. As of the compliance
page at 72d863e:

| Criterion | Non-compliant | Open issues |
|-----------|---------------|-------------|
| `reusable-workflow-secrets` | 4 | client-python#410, instar#610, occystrap#150, shakenfist#4380 |
| `export-repo-config` | 10+ | for example agent-python#146, client-python#409 |
| `ci-review-automation` | 10+ | for example agent-python#145, client-python#408 |

When the criterion was added, no reusable workflow in the fleet
read a secret. Every one of these inherits is therefore fixed by
deleting a line.

**The pinning half has not been started.** The spec of
`reusable-workflow-secrets` explicitly leaves it out. These
measurements come from local clones of the default branches, with
worktree copies excluded, on 2026-10-02:

- There are 633 remote `uses:` references, and none is pinned to a
  sha.
- 93 of them are `shakenfist/actions/...@main`, in 14 repositories.
- The other 540 are third-party tag references. The largest
  groups are `actions/checkout` (272), `actions/upload-artifact`
  (93), `actions/download-artifact` (36), `dtolnay/rust-toolchain`
  (16), `renovatebot/github-action` (15), `github/codeql-action/*`
  (40), `dorny/paths-filter` (13), `softprops/action-gh-release`
  (11) and `pypa/gh-action-pypi-publish` (11). `pypa/...@release/v1`
  is a branch, not a tag.
- **Every renovate-managed repository already runs Renovate**, self
  hosted through `renovatebot/github-action`. Every config has
  `automerge: false` for minor, patch and digest updates, so each
  bump is a pull request that a person reviews. Two repositories
  have no `renovate.json` at all: visual-digest-rust and
  private-ci.

**What `@main` on `shakenfist/actions` actually trusts.** That
branch has a ruleset ("Protect default branch history") that blocks
deletion and non-fast-forward pushes, and nothing more. There is no
classic branch protection and no pull request requirement. Over the
last three months, 146 first-parent commits landed on it:

| How it landed | Count |
|---------------|-------|
| Merged pull request | 81 |
| Pushed directly, by a person | 21 |
| Pushed directly, by `shakenfist-bot` (review-mark prune or import) | 44 |

The bot pushes with `DEPENDENCIES_TOKEN`, a personal access token
that falls back to `github.token`
(`actions/.github/workflows/prune-reviews.yml:90`). Today, anything
that can push to `actions` main runs, at the next CI run, in every
one of the 14 consuming repositories. That includes anyone holding
that token. It runs with whatever those jobs can reach, which after
the inherit rollout is `github.token` and the caller's
`permissions:` block. This is the narrowest point of the whole
problem: one setting in one repository, rather than 93 references
spread over 14.

**What sha-pinning `shakenfist/actions` would cost.** At 146 changes
per quarter, about 1.6 a day, Renovate would open roughly one
digest pull request per consuming repository per change. That is
on the order of 20 pull requests a day across the fleet, each one
needing a human. The audit also has callers that hard-code `@main`
as the expected form: `CI_REVIEW_SHARED_ACTION` and
`CI_REVIEW_TRIGGER_ACTION` in
`scripts/audit/checks/ci_workflows.py:162-165`, and the rationale
in `REPO_OVERRIDES` (`scripts/audit/repo.py:33`). Those would all
have to learn to accept a sha.

## Mission and problem statement

Close #153 with a settled fleet policy on which workflow references
may move, enforced by the audit wherever it can be decided
mechanically. In brief, the policy is:

1. **Finish the inherit rollout** that fe4b3ba started, so no
   callee can see a secret it was not given by name.
2. **Make `@main` on `shakenfist/actions` mean "reviewed".** Change
   that repository's ruleset so that changes reach main only
   through a pull request that passed its checks. First-party
   reusable workflows and composite actions keep the moving ref.
   The decision not to sha-pin them, and its cost, is recorded in
   the specs.
3. **Pin third-party actions to a sha**, with the version in a
   trailing comment. Renovate (`helpers:pinGitHubActionDigests`)
   keeps the pins current, and a new criterion enforces them.

Out of scope:

- Docker image digests in `container:` and `image:` keys. The
  `image-supply-chain` plan owns the images the fleet builds.
- Pinning the Python, cargo or npm dependencies that workflows
  install. Other criteria cover those.
- Harden-runner-style egress control.

## Open questions

1. **How does the review-mark bot keep landing on `actions` main
   once a pull request is required?** If `shakenfist-bot` is a
   ruleset bypass actor, the PAT path stays open. If it is not,
   `prune-reviews` has to open a pull request and auto-merge it,
   which is a template change across every adopted repository.
   *Default:* bypass for `shakenfist-bot` only. State the residual
   risk in the spec, which is that `DEPENDENCIES_TOKEN` is a
   credential that can write code that 14 repositories execute.
   Raise rotation and scoping of that PAT as future work. Revisit
   if the review-import plan already moves prune to pull requests.
2. **What does "a pull request is required" mean with one
   maintainer?** A required approval count of one cannot be met by
   the author. *Default:* require a pull request with zero required
   approvals, plus `ci.yml` as a required status check. The gain is
   that nothing lands without CI and a visible record. It is not
   gated on a second person, and the spec says so rather than
   implying otherwise.
3. **Should GitHub-owned actions (`actions/*`, `github/*`) be exempt
   from sha-pinning?** They are the bulk of the references (about
   75%) and the lowest risk. *Default:* no exemption. One rule is
   easier to hold than one with a carve-out (the same reasoning
   `reusable-workflow-secrets` gives for local callees), and the
   Renovate preset makes the cost of the pins uniform.
4. **Is the extra Renovate pull request volume acceptable?** Today a
   `@v4` tag silently absorbs every v4.x release. Once pinned, each
   release becomes a digest pull request. *Default:* measure it in
   phase 3a on this repository before deciding the fleet rollout.
   If volume is the problem, the answer is a grouping rule (one
   pull request per repository for all action digests), not
   dropping the pins. Grouping interacts with #88 (standardise
   renovate groupings), so check that issue's state first.
5. **Does Renovate pin `owner/repo/.github/workflows/x.yml@ref`
   reusable-workflow references the same way it pins actions?** This
   only matters for third-party reusable workflows, and there are
   none today. *Default:* the criterion measures them anyway, and
   phase 3a confirms the Renovate behaviour against a fixture rather
   than assuming it.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Finish the inherit rollout | Not started | |
| 2. Make `actions` main mean reviewed | Not started | |
| 3. Pin third-party actions to a sha | Not started | |
| 4. Record the policy and close #153 | Not started | |
| 5. Push audit | Not started | |

### Phase 1: finish the inherit rollout

This work is all in other repositories. Use the `consistency-fix`
skill there, one pull request per repository, and skip any issue
already in flight. Record each landing as `<repo> <sha> (#pr)`. The
phase is done when `reusable-workflow-secrets`, and the
`secrets: inherit` part of `export-repo-config` and
`ci-review-automation`, are compliant fleet-wide. Nothing in this
repository changes. The point of putting this phase first is that
phase 2's "after the inherit rollout" premise only becomes true
here.

Planning effort: medium. The fix is a deleted line, and the
criteria already exist.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | worktree | In each of client-python, instar, occystrap and shakenfist, run the `consistency-fix` skill for its open `Consistency: Reusable workflow secrets` issue (#410, #610, #150, #4380). Delete each `secrets: inherit` line under jobs that call `smoke-cluster.yml` or `test-drift-fix.yml`. Before deleting, grep the callee in `shakenfist/actions` (or locally) for `secrets.` to confirm it still reads none. If it reads one, stop and report rather than passing it. |
| 1b | medium | sonnet | worktree | The same for the open `Export repo config` and `CI review automation` issues, limited to their `secrets: inherit` findings. Leave their other findings to those issues' own fixes. |

### Phase 2: make `actions` main mean reviewed

Change the existing "Protect default branch history" ruleset on
`shakenfist/actions` (or add a second ruleset beside it) to:

- require a pull request, with the approval count from open
  question 2;
- require `ci.yml` to pass;
- give the bypass decided in open question 1.

Do this by hand, as an operator, and record the ruleset JSON in the
phase. Then add a criterion so that this does not quietly regress.
The criterion is `moving-ref-source-protection`, in
`scripts/audit/checks/github_config.py`. It applies to the
repositories that the fleet consumes at a moving ref, named in a
module constant (today only `actions`). Everywhere else it reports
`not_applicable` with that reason. It reads
`repos/{org}/{repo}/rules/branches/{default}` and fails unless a
`pull_request` rule is present.

Planning effort: high. This is what decides what the fleet trusts,
and it touches GitHub settings that this repository cannot test.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | Operator step, done by the management session with the operator, not by a sub-agent. Apply the ruleset change. Then prove it on a throwaway branch: a direct push to main is rejected, and a `prune-reviews` run still lands (or opens its pull request, per open question 1). |
| 2b | high | opus | worktree | Add a `MovingRefSourceProtection` class to `scripts/audit/checks/github_config.py`, with the id `moving-ref-source-protection`, its `spec` and its `issue_title` (`Moving ref source protection`), following `DeleteBranchOnMerge` at line 213 for the API-call shape. Register it in `CHECKS` in `scripts/audit/registry.py`. Write `docs/audits/moving-ref-source-protection.md`, following the structure in `docs/audits/README.md`; its Why section cites the landing counts from this plan's Situation. Add the file to `docs/audits/README.md`, and add its lines to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`. Add pass, fail and not-applicable tests to the github_config test file, using `CheckTestCase` and a stubbed GitHub client. |

### Phase 3: pin third-party actions to a sha

Planning effort: high for 3a, which decides the rollout. Medium for
the rest, which follows `renovate.md`'s pre-commit manager
requirement as a worked example.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | worktree | Probe in this repository. Add `"extends": ["helpers:pinGitHubActionDigests"]` to `renovate.json`. The existing `managerFilePatterns` already cover `templates/`. Let one Renovate run produce its pinning pull request, and record: the comment format it writes; whether `pypa/gh-action-pypi-publish@release/v1` (a branch) pins cleanly; whether `actionlint` and the template byte-identity checks still pass; and the digest pull request count over the following two weeks. Answer open questions 4 and 5 in the plan, with numbers. |
| 3b | medium | sonnet | none | Add the same `extends` to `templates/renovate/renovate.json`, and say why in `templates/renovate/README.md`. Pin every third-party `uses:` under `templates/` to a sha with a `# vX.Y.Z` comment, keeping template copies and their deployed twins here byte-identical. Grep `scripts/audit/` for any check that matches a `uses:` line by tag (for example `@v`), and update it to accept a pinned form. |
| 3c | medium | sonnet | worktree | Add the criterion `third-party-action-pinning` to `scripts/audit/checks/ci_workflows.py`, beside `ReusableWorkflowSecrets` (line 2162). It is measured: every remote `uses:` whose owner is not `shakenfist` ends in a 40-hex sha. It is confirmed by a reviewer: the trailing version comment, and the Renovate preset being on. Reuse the existing workflow-parsing helpers in that file rather than adding a YAML dependency. Give it the five criterion files as in 2b, plus tests for a tag ref, a sha ref, a branch ref, a commented-out line, a `docker://` ref (out of scope, not a finding) and a local `./` ref (not a finding). Run it against fresh clones of the fleet. The commit message states how many repositories it newly fails, including this one, which must already pass after 3b. |

### Phase 4: record the policy and close #153

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | In `docs/audits/reusable-workflow-secrets.md`, replace "What this does not cover" with the settled policy: first-party refs move, and the `moving-ref-source-protection` criterion is what makes that acceptable; third-party refs are pinned, under `third-party-action-pinning`. Include the roughly 20 pull requests a day this avoids, and the PAT residual from open question 1. Link both new specs. Close #153 from the pull request body, and link phase 1's landings. |

### Phase 5: push audit

Run `PUSH-AUDIT.md` over the union of the `Merged` cells above, read
as `origin/main...` ranges after a fetch. Phase 1's landings are in
other repositories and were audited by their own pull requests, so
this phase cites those audits rather than re-running them.

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

<!-- shared-block: plan-push-audit-phase v3 -->
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
  untriggered for as long as it did.
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

**In this plan.** Step 2a is the exception: it changes GitHub
settings on another repository, so the management session does it
with the operator rather than delegating it.

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

Specific to this plan:

* `reusable-workflow-secrets` is compliant in every repository in
  scope.
* A direct push to `shakenfist/actions` main by a person is
  rejected, and `moving-ref-source-protection` reports compliant
  for `actions`.
* No third-party `uses:` in this repository or under `templates/`
  is unpinned. `third-party-action-pinning` has filed its issues
  fleet-wide, and the plan records how many.
* #153 is closed, and the specs say why first-party refs move and
  third-party refs do not.

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

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

**In this repository.** Future work, as of writing:

- Rotate and narrow `DEPENDENCIES_TOKEN`. While it can bypass the
  `actions` ruleset, it is the one credential that can put
  unreviewed code into every consumer's CI (open question 1).
- `visual-digest-rust` and `private-ci` have no `renovate.json`.
  Phase 3's pins would rot in those two repositories. The
  `renovate` criterion already owns that.
- Composite actions in `shakenfist/actions` (for example
  `pr-bot-trigger@main`) run inside the caller's job, with its
  environment and `github.token`. Phase 2 covers them, because they
  live on the same branch, but the specs should say so explicitly.

Related issues, as of writing:

- #153 is the issue this plan closes.
- #88 (standardise renovate groupings) interacts with open
  question 4's grouping rule.
- #58 (Dependency Dashboard) is where phase 3a's pinning pull
  request will show up.
