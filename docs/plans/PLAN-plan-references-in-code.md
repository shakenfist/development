# Plan references in code

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

Measured on 2026-10-10 against `origin/main` at `5e29779`.

`templates/shared-blocks/plan-references-in-code.md` (v1, canonical
here, embedded in every `PUSH-AUDIT.md` because
`PUSH_AUDIT_BLOCKS` in `scripts/audit/checks/plans.py:174-180`
requires it) says code must not write "added in phase 5", "per
decision 3", "pending step 5f" or "the phase-4 leaks pass", and
that a plan link survives only for work not built yet. Its last
bullet: "A plan reference a diff adds to code is a finding to fix
before pushing. References on lines the diff does not touch are
backlog, not findings against the change."

Nothing enforces either half of that (#239):

* **`plan-phase-references`** (`plans.py:1018`) has the right
  pattern, `PHASE_REFERENCE_RE = \bphases?\s+\d+\b` (`plans.py:28`),
  and the wrong file set: `iter_doc_content_files()` gives it
  `README.md` and `docs/`, minus plans directories.
* **`plan-source-references`** (`plans.py:1080`) has the right file
  set -- every tracked non-markdown file outside `docs/**/plans/`,
  with symlink containment, a 2 MB cap, and line and file
  `audit-ok` markers -- and asks the wrong question: it only checks
  that each `PLAN-*.md` token resolves. A resolving reference
  passes however forbidden its content.
* **`PUSH-AUDIT.md`**'s wave 1 greps (lines 80-155) have no grep
  for an added plan reference, so the diff-level half of the rule
  is left entirely to wave 2's judgment sub-agents. The push audit
  of PLAN-image-supply-chain found eight instances across two
  repositories and made it one of its two blocking findings; its
  "why it was not caught" paragraph said nothing mechanical guards
  the block in code.

**This repository.** The issue lists five; there are more. A
case-insensitive grep for `phases? N`, `phase-N`, `decisions? N`
and `steps? N<letter>` over tracked non-markdown files outside
plans directories finds 78 lines in 12 files. Most of the 68 in
`scripts/audit/checks/plans.py` and `scripts/tests/test_plans.py`
are the pattern's own fixtures and docstrings, and so are
`scripts/audit/files.py:187`, `scripts/audit/text/markdown.py:209`
and `scripts/tests/test_markdown.py:82`. The genuine history
references are:

| Location | Text |
|---|---|
| `.pre-commit-config.yaml:39` | "Phase 3 of ..." |
| `scripts/audit-update-docs.py:16` | `docs/plans/PLAN-audit-compliance-split.md.` |
| `scripts/audit/checks/distros.py:438` | "decision 6.4 of ..." |
| `scripts/audit/repo.py:103` | "phase 6 found and corrected elsewhere" |
| `scripts/tests/test_audit_common.py:7-8` | "recorded in phase 5 of PLAN-push-audit-phase.md" |
| `scripts/tests/test_audit_snapshot.py:183` | "after phase 2 no check spawns `gh`" |
| `scripts/tests/test_distros.py:863` | "Decision 6.4 of PLAN-image-supply-chain lives here." |
| `scripts/tests/test_manage_issues.py:53` | "The push audit of PLAN-push-audit-phase.md found this path" |
| `scripts/tests/test_markdown.py:218` | "PLAN-push-audit-phase.md raised the carried-over header" |
| `scripts/tests/test_plans.py:2355` | "Phase 5 of PLAN-push-audit-phase.md fixed it in step 5a" |
| `scripts/tests/test_registry.py:1059` | "Phase 3 of the review import plan ..." |

**The fleet.** The same grep over the local clones (each at its
last fetch; the date is the clone's newest commit, so the older
ones undercount):

| Repository | Phase lines (files) | Decision lines | `PLAN-*.md` lines | Clone date |
|---|---|---|---|---|
| shakenfist | 887 (190) | 48 | 384 | 2026-10-05 |
| instar | 869 (122) | 180 | 172 | 2026-08-17 |
| divergulent | 210 (48) | 0 | 15 | 2026-09-25 |
| development | 75 (10) | 3 | 256 | 2026-10-08 |
| kerbside | 18 (9) | 4 | 4 | 2026-10-08 |
| client-python | 12 (3) | 0 | 0 | 2026-07-18 |
| actions | 11 (4) | 4 | 8 | 2026-10-05 |
| ryll | 11 (8) | 1 | 65 | 2026-09-30 |
| hunkydory | 4 (4) | 0 | 1 | 2026-10-06 |
| kerbside-patches | 3 (1) | 0 | 0 | 2026-09-27 |
| visual-digest-rust | 3 (2) | 0 | 1 | 2026-07-24 |
| private-ci | 1 (1) | 0 | 2 | 2026-08-06 |
| agent-python, client-python-k3s, sfui | 0 | 0 | 0 | |

Sampled, the hits are overwhelmingly real plan history, not
"phase" in its ordinary sense: `shakenfist/mariadb.py` (70 lines,
"the phase 3 guard"), `shakenfist/external_api/base.py` ("by the
phase 8 audit of PLAN-api-input-validation"), instar's
`src/vmm/src/main.rs` (100 lines), divergulent's
`classify/*.py` ("the security-risk gate (phase 4)"). Two kinds
of false positive showed up, and both matter to the design:

* **mkdocs navigation.** 75 of shakenfist's lines are
  `mkdocs.yml` nav titles such as `"Phase 5: Direct-qemu CI
  workflow": components/kerbside/plans/PLAN-test-harness-phase-05-...md`.
  A nav entry naming a plan page is documentation structure,
  not a claim about code.
* **Generated files.** shakenfist's
  `shakenfist/protos/database_pb2_grpc.pyi` carries 30 lines
  copied from comments in the `.proto` it is generated from. The
  fix is at the source; the generated file follows on
  regeneration.

Shaken Fist's own clones are behind for instar, client-python and
visual-digest-rust, and the clones do not cover clingwrap,
cloudgood, kerbside-client, library-utilities, occystrap, images
or uncalibrated-sextant, which are in the audit matrix. Phase 2
re-measures from fresh clones before anything is decided.

## Mission and problem statement

Make the `plan-references-in-code` block enforceable, in the two
places its own wording asks for:

1. **At the diff**, where the block says an added reference is a
   finding: a wave 1 grep in this repository's `PUSH-AUDIT.md`,
   and the same grep offered to the fleet's runbooks through the
   `push-audit` criterion's guidance. This is the cheap half, and
   the one that stops the backlog growing.
2. **As backlog**, where the block says existing references are:
   a weekly criterion that walks the same files as
   `plan-source-references` and reports plan-shaped history
   references, so the backlog is visible and shrinks rather than
   being rediscovered by each push audit.

Then clean this repository first, so the criterion lands passing
here.

Out of scope:

* **Judging whether a remaining plan link is "deferred work".** The
  block allows `deferred; see PLAN-foo.md` and forbids `per
  PLAN-foo.md`. Telling them apart is a reading of intent, and a
  regex that tried would be either useless or wrong. A bare
  `PLAN-*.md` token stays `plan-source-references`' business
  (does it resolve) and wave 2's (should it be there). The new
  criterion flags the numbered shapes -- phase, decision, lettered
  step -- which are history by construction.
* **Cleaning other repositories.** The criterion files their
  issues; the `consistency-fix` skill works them off. shakenfist
  and instar each need far more than one pull request, and that
  is their owners' sequencing, not this plan's.
* **Changing `plan-phase-references`.** Its scope is
  documentation and its issues are open in five repositories;
  widening it would change what those issues mean. The two
  criteria share the pattern constant instead.

## Open questions

1. **New criterion or a wider `plan-source-references`?** Widening
   the existing check keeps the criteria count flat, but its title
   "Plan references in source" is the idempotency key for issues
   already open, and those issues would suddenly mean something
   else, with a backlog of thousands of lines attached. *Default:
   a new criterion, id `plan-history-in-source`, issue title
   `Plan history in source`, sharing the file walk with
   `plan-source-references` through a helper extracted from
   `plans.py:1103-1131`.* The id deliberately differs from the
   shared block's name so that "plan-references-in-code" keeps
   naming the rule rather than one of its two checks.
2. **Rollout.** On today's numbers the criterion newly fails
   roughly ten repositories, two of them with about a thousand
   lines each. The weekly run would file all of those issues on
   its first pass. *Default: land it failing, as every other
   backlog criterion does -- `plan-phase-references` is
   non-compliant in five repositories today and that is the
   system working. Phase 2 stops at a review point with the fresh
   per-repository counts, so the operator sees the blast radius
   before merge.* The alternative, a threshold or a
   report-only mode, would be the first of its kind and is not
   proposed.
3. **What counts as a match.** `phases? N` and `phase-N` (the
   block's "the phase-4 leaks pass"), `decisions? N` (including
   `decision 6.4`), and a lettered step, `steps? N<letter>` ("step
   5f"). A bare `step 3` is not flagged: procedural comments say
   "step 1: open the file" in the ordinary sense. *Default: those
   four shapes, case-insensitive, with inline code spans removed
   as `plan-phase-references` does.* Rust and Python string
   literals are not removed: a log line saying "phase 3" is as
   much history as a comment saying it.
4. **Exemptions.** *Default:* the existing line marker
   `audit-ok: phase-reference` (`PHASE_REFERENCE_OK` is an HTML
   comment today; the source check accepts the bare token so that
   `# audit-ok: phase-reference` and `// audit-ok:
   phase-reference` work) and a file marker
   `audit-ok: phase-reference-file` on the model of
   `PLAN_SOURCE_FILE_OK`; and `mkdocs.yml`, whose nav entries
   name plan pages by title. Generated files are not exempted:
   the reference is fixed in the source it was generated from.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Diff-level guard and this repository's cleanup | In progress | |
| 2. The `plan-history-in-source` criterion | Not started | |
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

### Phase 1: diff-level guard and this repository's cleanup

Planning effort: medium. One pull request.

The cheap half first, because it stops the backlog growing in this
repository the day it lands, and because cleaning this repository
first is what lets phase 2's criterion land compliant here.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | In `PUSH-AUDIT.md`, add a grep to the wave 1 block (lines 80-155), after the TODO/suppressions greps and before "Documentation touched at all", with a comment in the style of its neighbours explaining why: the `plan-references-in-code` block, embedded further down the same file, says an added plan reference is a finding before pushing, and until now only wave 2 judgment looked for one. The grep is `git diff "${AUDIT_RANGE:-origin/main...HEAD}" -- ':!*.md' ':!docs/**/plans/**' \| grep -niE '^\+[^+].*(\bphases?\s+[0-9]+\b\|\bphase-[0-9]+\b\|\bdecisions?\s+[0-9]+\b\|\bsteps?\s+[0-9]+[a-z]\b\|PLAN-[A-Za-z0-9._-]+\.md)'`. Verify the pathspec excludes plans directories by running it against a range that touched `docs/plans/` (for example `AUDIT_RANGE=f624d26^1..f624d26`). Then, in `docs/audits/push-audit.md`, add a sentence where it describes the wave 1 greps (read it first; if it does not describe them, add a short paragraph near the `plan-references-in-code` mention) saying an adopting repository's runbook should carry the equivalent grep, adapted to its languages. Do not touch the shared block itself. |
| 1b | high | opus | none | Remove the genuine plan-history references from this repository's source, listed in the plan's Situation table: `.pre-commit-config.yaml:39`, `scripts/audit-update-docs.py:16`, `scripts/audit/checks/distros.py:438`, `scripts/audit/repo.py:103`, `scripts/tests/test_audit_common.py:7-8`, `scripts/tests/test_audit_snapshot.py:183`, `scripts/tests/test_distros.py:863`, `scripts/tests/test_manage_issues.py:53`, `scripts/tests/test_markdown.py:218`, `scripts/tests/test_plans.py:2355`, `scripts/tests/test_registry.py:1059`. The block's rule is not "delete the citation" but "write the reason": for each, read the surrounding code and the cited plan, and replace the pointer with the constraint, measurement or failure it stands for, in one or two sentences, or drop it where the surrounding comment already says why. Re-run the grep from the Situation section (`git ls-files \| grep -v '\.md$' \| grep -vE '(^\|/)docs/(.*/)?plans/' \| xargs -d '\n' grep -IniE '\bphases?\s+[0-9]+\b\|\bphase-[0-9]+\b\|\bdecisions?\s+[0-9]+\b\|\bsteps?\s+[0-9]+[a-z]\b'`) and confirm that what remains is only fixtures and docstrings describing the pattern -- in `scripts/audit/checks/plans.py`, `scripts/tests/test_plans.py`, `scripts/audit/files.py:187`, `scripts/audit/text/markdown.py:209` and `scripts/tests/test_markdown.py:82`. Do not add markers yet; phase 2 introduces them. `pre-commit run --all-files` must pass. One commit for 1a, one for 1b. |

### Phase 2: the `plan-history-in-source` criterion

Planning effort: high. It changes what the fleet is held to and
will file issues in about ten repositories. One pull request, with
a review point before merge.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Close out phase 1 per `plan-phase-landing`: set its Status and Merged cells and the index row. Separate commit. |
| 2b | high | opus | none | In `scripts/audit/checks/plans.py`, extract the file walk from `PlanSourceReferences.run()` (lines 1103-1131: `tracked_paths`, skip `.md`, skip `plan_source_is_plan_record`, `isfile`, `repo.contains`, `plan_source_is_oversize`, read) into a generator, say `iter_plan_source_files(repo)`, yielding `(rel, content)`, and have `PlanSourceReferences` use it with no behaviour change (its tests in `scripts/tests/test_plans.py` must pass unmodified). Return `LS_FILES_FAILED` handling to the caller as it is today. Commit separately. |
| 2c | high | opus | worktree | Add `PlanHistoryInSource(Check)` beside `PlanSourceReferences`: id `plan-history-in-source`, spec `docs/audits/plan-history-in-source.md`, `issue_title = 'Plan history in source'`, `template = None`. It walks `iter_plan_source_files`, skips `mkdocs.yml` at any depth (nav titles name plan pages; say so in a comment), skips a file containing `audit-ok: phase-reference-file` (new constant beside `PLAN_SOURCE_FILE_OK`, with a comment on the model of lines 45-53), skips lines containing `audit-ok: phase-reference` (reuse the bare token -- define `PHASE_REFERENCE_TOKEN = 'audit-ok: phase-reference'` and derive `PHASE_REFERENCE_OK` from it so the HTML-comment form used by `plan-phase-references` is unchanged), strips inline backtick spans as `PlanPhaseReferences` does (`plans.py:1065`), and matches a new `PLAN_HISTORY_RE` that extends `PHASE_REFERENCE_RE` with `\bphase-\d+\b`, `\bdecisions?\s+\d+(\.\d+)?\b` and `\bsteps?\s+\d+[a-z]\b`, case-insensitive. Note the file-marker token contains the line-marker token as a prefix; check the file marker first and make sure a line carrying only the file marker is not mistaken for anything else. Report N/A when no source file exists; ok as "No plan history references in source"; fail as "N plan history reference(s) in source (write the reason the code is this way instead of citing the plan step that produced it): file:line, ..." capped at ten shown, like its neighbours. Register it in `CHECKS` in `scripts/audit/registry.py` beside `plan-source-references`; write `docs/audits/plan-history-in-source.md` following `docs/audits/README.md`'s structure and linking `compliance.md#plan-history-in-source`, covering what it matches, the four exemptions and why generated files are not one, and how it composes with `plan-source-references` and `plan-phase-references`; add its row to `docs/audits/README.md`; add it to `FROZEN_METADATA` and `FROZEN_ISSUE_TITLES` in `scripts/tests/test_metadata.py`; add `CheckTestCase` tests to `scripts/tests/test_plans.py` for pass, fail (each of the four shapes), N/A, a `two-phase commit` non-match, a bare `step 3` non-match, a backticked match ignored, both markers, `mkdocs.yml` skipped, and a plans directory skipped. Then add `audit-ok: phase-reference-file` with a one-sentence reason to `scripts/tests/test_plans.py` and `scripts/tests/test_markdown.py`, and line markers to the remaining pattern-describing lines in `plans.py`, `files.py` and `markdown.py`, so `python3 scripts/audit-check.py --repo-path . --repo-name development` reports the new check compliant. `pre-commit run --all-files` must pass. |
| 2d | high | opus | none | Measure the blast radius. Fresh-clone (or `git fetch` and check out the default branch of) every repository in the audit matrix into the scratchpad, run `scripts/audit-check.py` for the new check against each, and record a table in this section: repository, verdict, hit count, and a sampled false-positive rate (read ten hits per failing repository and count those that are "phase" in its ordinary sense or otherwise not plan history). Run `audit-manage-issues.py --dry-run` over the result and record how many issues it would file. Commit the table to this plan. |
| 2e | - | - | - | **Review point.** The operator reads 2d's table before merge. If the false-positive rate anywhere is high enough that the issue would be argued with rather than worked, the pattern or the exemptions change and 2d re-runs; if the issue count is unacceptable, open question 2 is reopened. Do not merge without the operator's go-ahead. |

### Phase 3: push audit

Planning effort: medium.

Run `PUSH-AUDIT.md` over the merge commits of phases 1 and 2,
recorded in the Execution table, per `plan-push-audit-phase`. The
grep phase 1 added is part of wave 1 and should find nothing in
this plan's own diff outside plan documents. Close the plan per
`plan-phase-landing`, and close #239 from the pull request that
lands phase 2.

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

**In this repository.** A brief that names four of the
criterion's five files is the characteristic defect here, and the
frozen tables are the one most often left out; step 2c names all
five. A check that does not apply reports `not_applicable` with a
reason rather than being omitted, because an omitted check renders
as `unknown`.

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
* `PUSH-AUDIT.md`'s wave 1 greps an audited diff for added plan
  references in code, and `docs/audits/push-audit.md` tells
  adopting repositories to do the same.
* `plan-history-in-source` is registered, has all five of its
  files in step (class, `CHECKS` entry, spec, `docs/audits/README.md`
  row, frozen metadata and issue title), and reports this
  repository compliant.
* `plan-source-references` behaves exactly as before; its tests
  pass unmodified after the walk is extracted.
* The fleet-wide verdicts, issue count and sampled false-positive
  rate the criterion produces were recorded in this plan and
  accepted by the operator before merge.
* No `consistency-audit` marker block has been added to a
  criterion specification by hand, and the compliance tables in
  `docs/audits/compliance.md` have not been hand-edited.
* Python is wrapped at 120 characters, single quotes for strings
  and double quotes for docstrings, and no script has grown a
  dependency outside the standard library.

### Documentation index maintenance

This plan's row in `docs/plans/index.md` was added with the plan.
Update its status as phases land.

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

* Offer the wave 1 grep as a ready-made line in the fleet's
  `PUSH-AUDIT.md` files directly, rather than through guidance in
  `docs/audits/push-audit.md`, if the runbooks converge enough to
  share wave 1.
* A pull-request-time CI check for added plan references, if the
  push-audit grep turns out not to be enough on its own.
* shakenfist/actions#148 removes five references in `actions`; the
  criterion will measure whatever remains there.

Bugs fixed during this work: #239, nothing enforced
`plan-references-in-code`.
