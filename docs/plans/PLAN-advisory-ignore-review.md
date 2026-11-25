# Advisory ignore review

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

Two of the 24 repositories in the audit matrix scan their Rust
dependencies against the RustSec advisory database: ryll and
andris. Both run `cargo deny` (policy in `deny.toml`) and
`cargo audit` (policy in `.cargo/audit.toml`) on every pull
request, and both keep an `[advisories].ignore` list in each file.
uncalibrated-sextant and visual-digest-rust carry a `Cargo.lock`
but neither scanner. No repository in the matrix ignores a Python
or npm advisory.

Nothing gives an ignore an end date, and nothing asks whether one
still does anything. On 2026-11-25 every ignore was removed from
both files in each repository and the scanners were run again
(cargo-deny 0.22.1, cargo-audit 0.23.0, in a `rust:latest`
container):

| Repository | Ignores | Needed by `cargo deny` | Needed by `cargo audit` | Suppressing nothing |
|------------|---------|------------------------|-------------------------|---------------------|
| ryll | 10 | RUSTSEC-2023-0071, RUSTSEC-2025-0134 | RUSTSEC-2023-0071 | 8 |
| andris | 2 | RUSTSEC-2023-0071 | RUSTSEC-2023-0071 | 1 (RUSTSEC-2025-0134) |

Nine of the fleet's twelve ignores suppressed nothing. Every one
of the nine is an unmaintained or unsound advisory. cargo-deny
0.22 checks those advisory kinds only for the workspace's direct
dependencies unless `unmaintained` and `unsound` are widened, and
`cargo audit` exits non-zero only for vulnerabilities, printing
everything else as a warning. So an ignore for an unmaintained
transitive crate suppresses nothing in either scanner, while
reading as a gate that holds something back. cargo-deny already
reports every such ignore as `advisory-not-detected`, but only as
a warning in a job that passes, so nobody reads it. ryll has
reaped its stale ignores by hand three times: one in July 2026,
four in September and eight in November (shakenfist/ryll#805),
each found by someone happening to look.

The ignores that do suppress something have no review clock
either. ryll and andris have ignored RUSTSEC-2023-0071 (the `rsa`
Marvin attack) since their scanners landed, and nothing asks
whether a fix has shipped since.

cargo-deny 0.22 already has the mechanisms. Read from the 0.22.1
source (`src/advisories/cfg.rs`, `src/advisories/diags.rs`) and
its changelog (EmbarkStudios/cargo-deny#904):

- An ignore entry may be a table, `{ id = "...", reason = "...",
  expiry = "..." }`. `expiry` is either an absolute date,
  `YYYY-MM-DD` and valid through that day, or an ISO 8601 duration
  starting `P`. Once the run date is past it, the ignore no longer
  applies and the advisory fails the run at deny level, as
  `advisory-ignore-expired`.
- A duration is measured from the advisory's *publication* date,
  and so is the global `ignore-expiry` default. For an advisory
  published in 2023, any sensible duration has already run out, so
  neither works as a review clock. The absolute date does.
- `unused-ignored-advisory` sets the level of the
  `advisory-not-detected` diagnostic. It defaults to `warn`; `deny`
  fails the run on any ignore that suppresses nothing.
- `EmbarkStudios/cargo-deny-action@v2`, the action both
  repositories' CI uses, runs cargo-deny 0.22.1 at the time of
  writing.

cargo-audit 0.23 has no expiry on its ignores.

The fleet's other ignore files, `.gitleaksignore` (four
repositories) and `.gitleaks.toml` allowlists (six), record
fingerprints of false positives. A false positive does not go
stale the way an accepted advisory does, so they are out of scope.

## Mission and problem statement

Make every RustSec ignore in the fleet either do something and be
on a review clock, or be gone. Enforcement is split along what
each layer can see:

- **Each repository's own CI** enforces the clock and the
  liveness, because only it has the toolchain and the advisory
  database. That is cargo-deny's per-entry `expiry`, which fails
  the run once the date passes, and `unused-ignored-advisory =
  "deny"`, which fails it on an ignore that suppresses nothing.
- **The weekly consistency audit** makes sure that configuration
  is in place, and warns before an expiry breaks CI. That is a new
  criterion, `advisory-ignore-review`, which applies to any
  repository that tracks a `deny.toml` at its root and fails when:
  1. `[advisories]` does not set `unused-ignored-advisory =
     "deny"`;
  2. an `[advisories].ignore` entry is not a table with an `id`, a
     non-empty `reason`, and an `expiry` written as an absolute
     date rather than a duration;
  3. an `expiry` is more than 180 days after the run date;
  4. an `expiry` is 14 days or fewer after the run date, or
     already past -- review is due, and the issue names each ID
     and its date;
  5. an ID in `.cargo/audit.toml`'s ignore list, where that file
     exists, is not also in `deny.toml`'s. cargo-audit cannot
     carry a date, so its ignores ride on the clock of the
     matching cargo-deny entry. The reverse is not required:
     `cargo audit` fails on fewer advisories than `cargo deny`
     does, so its list is legitimately shorter (shakenfist/ryll#805
     made it so).

The specification sets conventions the check does not try to
enforce, because it cannot see an entry's history. A new or
reviewed ignore expires 90 days out by default, and up to 180 days
for an advisory where no upstream movement is expected (an
archived crate with no successor). Reviewing an ignore is a pull
request that moves the date and rewrites `reason` to say what was
checked ("dav-server still at 0.12.0, which requires `^0.16.3`").
The first dates written are staggered, so that a repository's
ignores do not all come due in the same week.

With this criterion, ryll and andris newly fail, because neither
sets the lint level or any expiry. This repository is not
applicable, because it has no `deny.toml`.

Not covered: the gitleaks files above; advisory ignores for other
ecosystems, since none exist yet (see *Future work*); whether
every Rust repository should run cargo-deny at all; and paying
down the debt the ignores record, which stays with each
repository's own plans.

## Open questions

1. **An expiry breaks every pull request in that repository on
   the day it passes.** This is the same failure mode as a newly
   published advisory, which is exactly what happened to ryll on
   2026-11-24 with RUSTSEC-2026-0596. By default the plan accepts
   that: the criterion's 14-day warning means at least two weekly
   audit runs file an issue first, and a hard stop is what
   "insists" means. The alternative is to put the date in
   `reason` only and enforce it in the audit alone, which never
   breaks CI but also never stops a repository that ignores the
   issue.
2. **`cargo audit` duplicates `cargo deny`'s advisory check from
   the same database, and its ignores cannot expire.** By default
   both scanners stay and rule 5 ties them together. Dropping
   `cargo audit` from ryll's and andris's CI would remove a file
   and a rule; that is a separate decision, recorded in *Future
   work*.
3. **The warning window.** Default 14 days: two weekly runs.

## Execution

The phases are sections below.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. The criterion and its specification | Not started | |
| 2. Adopt in ryll and andris | Not started | |
| 3. Push audit | Not started | |

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

### Phase 1: the criterion and its specification

Effort: high, because a new criterion changes what the fleet is
measured against.

#### Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 1a | high | opus | none | Add `scripts/audit/checks/supply_chain.py`, a new family module (`packaging.py` and `ci_workflows.py` are both close to their size-ratchet baselines), holding one `Check` subclass with `id = 'advisory-ignore-review'`. `applies(repo)` is true when `deny.toml` is tracked at the repository root. `run(repo)` reads `deny.toml` and, where present, `.cargo/audit.toml` through `repo.read()` and parses them with `tomllib`, as `packaging.py` does; an unparseable file is a failure that says so, not a raise. It implements rules 1 to 5 from *Mission* and reports every finding, not just the first, naming each advisory ID and, for rules 3 and 4, its date. The run date comes from one injectable source so that tests can pin it. Register it in `CHECKS` in `scripts/audit/registry.py`. |
| 1b | high | opus | none | `scripts/tests/test_supply_chain.py`, following the `CheckTestCase` convention in `docs/consistency-audits.md`. Cover: not applicable without `deny.toml`; a passing configuration; each of rules 1 to 5 failing alone; a string-form entry; a duration-form `expiry`; an expiry exactly 14 and exactly 15 days out, and exactly 180 and 181; an expired date; a `.cargo/audit.toml` ID missing from `deny.toml`, and the reverse passing; an unparseable `deny.toml`; and an `[advisories]` table with no `ignore` key, which passes as long as rule 1 holds. Add the `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` lines in `scripts/tests/test_metadata.py`. |
| 1c | medium | opus | none | `docs/audits/advisory-ignore-review.md`, following the structure in `docs/audits/README.md`: what is checked, why the work splits between cargo-deny and the audit (the *Situation* table is the evidence), the conventions the check does not enforce (90 days by default, 180 at most, what a review pull request says, staggering), what it does not cover, and `compliance.md#advisory-ignore-review` under `## Projects`. Add its line to the criterion index in `docs/audits/README.md`. |
| 1d | medium | opus | none | Management session, inline. Run `scripts/audit-check.py` against fresh clones of ryll, andris and this repository, and `audit-manage-issues.py --dry-run` over the result. Record each verdict in this plan. Expected: ryll and andris fail rules 1 and 2, and this repository is not applicable. Any other verdict is either a check bug or a premise this plan got wrong. Re-confirm the cargo-deny version that `EmbarkStudios/cargo-deny-action@v2` runs, and record it. Run `pre-commit run --all-files` before proposing the commit. |

#### Definition of done

- The five criterion files are in step, as *Success criteria*
  lists.
- The dry run names ryll and andris and no other repository.
- The specification says what each layer enforces and why, and a
  reader who was not here can follow the 2026-11-25 measurement it
  rests on.

### Phase 2: adopt in ryll and andris

Merged: one line per repository, as `<repo> <sha> (#pr)`.

Each repository gets one pull request, which closes the issue
that phase 1's first real run files there with `Fixes #N`. ryll's
depends on shakenfist/ryll#805, which removes the eight ignores
that suppress nothing; land that first.

#### Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2a | medium | opus | worktree | In ryll: set `unused-ignored-advisory = "deny"` under `[advisories]` in `deny.toml`. Convert each ignore to `{ id, reason, expiry }`, carrying the comment's rationale into `reason`, with the expiries staggered across the next 90 days. Update the ignore policy in `docs/ci.md` and the *Situation* of `docs/plans/PLAN-supply-chain-followups.md` to describe the review clock. |
| 2b | medium | opus | worktree | In andris: the same, after removing RUSTSEC-2025-0134 from both files, since it suppresses nothing there (cargo-deny reports it as `advisory-not-detected`, and `cargo audit` only warns). Read andris's own docs for the place that states its scanner policy, rather than assuming ryll's layout. |
| 2c | medium | opus | none | Management session, inline, for each repository: run both scanners in a `rust:latest` container and confirm they pass. Then prove that each mechanism fails, on a scratch copy and not on the branch: an expiry set to yesterday must fail `cargo deny` with `advisory-ignore-expired`, and a bogus ignore ID must fail it with `advisory-not-detected`. Re-run phase 1's check against each branch and confirm it passes. |

#### Definition of done

- Both pull requests have merged, and the next weekly run closes
  both issues, or a hand-dispatched run does.
- Both mechanisms were shown to fail, not just to pass.

### Phase 3: push audit

Run `PUSH-AUDIT.md` over the recorded landings: phase 1's merge
commit in this repository, and phase 2's in ryll and andris, each
read against that repository's default branch.

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

Once a plan has been finished for 90 days it is retired as
`docs/plan-retirement.md` describes: its reasoning is moved into
docs/ and its *Future work* is filed as issues. Write both so they
survive being read by someone who was not here.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- uncalibrated-sextant and visual-digest-rust carry a `Cargo.lock`
  and run neither scanner. Whether every Rust repository should run
  cargo-deny is a criterion of its own.
- Drop `cargo audit` where `cargo deny` already runs (open question
  2). That would remove `.cargo/audit.toml` and rule 5 with it.
- Extend the review clock to other ecosystems when the first
  advisory ignore appears in one. osv-scanner's `ignoreUntil` is
  the equivalent of `expiry`; pip-audit has none.
- visual-digest-rust depends on `rqrr` 0.10, which pulls in `lru`
  0.16.4 (RUSTSEC-2026-0253, unsound). `rqrr` 0.11 moves to `lru`
  0.18, which carries the fix. That is not an ignore, but it was
  found while measuring the *Situation* table.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- shakenfist/ryll#802: RUSTSEC-2026-0596, an out-of-bounds read in
  `etherparse` 0.21.0, was failing `cargo audit` and `cargo deny`
  on every ryll pull request. Fixed with a lockfile bump to 0.21.1.
  It was first misread as the `lru` advisory, which is what started
  this plan.
- shakenfist/ryll#805: removes ryll's eight ignores that suppress
  nothing, and stops `.cargo/audit.toml` from mirroring `deny.toml`.
  Phase 2 depends on it.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
