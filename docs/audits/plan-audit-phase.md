# Audit: Push audit phase in master plans

## What we check

Every master plan ends with a phase that runs the repository's
`PUSH-AUDIT.md` over the accumulated diff of the whole plan. The rule
is the `plan-push-audit-phase` shared block, and this criterion is what
holds it in place: the phase reached the fleet's plans as a hand-driven
sweep, and until this check existed nothing stopped the next plan from
omitting it.

For every master plan `docs/plans/index.md` links whose status is not
terminal -- that is, anything other than `Complete`, `Abandoned` or
`Superseded`:

* the plan **names `PUSH-AUDIT.md`**, and
* the **last** of its phases is the push audit phase.

Last is the part of the rule that matters and the part that rots. An
audit scheduled in the middle of a plan is outrun by the phases that
follow it, and the plan reaches `Complete` with work nothing ever
audited -- which is not hypothetical: the check finds plans whose audit
phase was overtaken by phases appended after it.

Phases are read from the plan file, never from the index. Index layouts
differ across the fleet by design -- one repository carries a phase
count column, another an inline list of phase names, this one no phase
column at all -- so anything keyed on an index column would be
unimplementable in half of it. What the check reads from the index is
the pair every layout agrees on: which plans it links, and what each
one's status cell says.

A phase is a row of a table whose first column is `Phase`, or a
numbered heading -- `### Phase 5: Push audit`, or `### 5. Push audit`
inside a section headed execution, implementation, phases or
workstreams. Both shapes are read where a plan carries both, keyed by
phase number rather than by position, because the numbers agree where
document order does not.

Repositories with no `docs/plans/index.md` are N/A. Whether every
project should plan this way is a separate decision, made by the
`plan-index` criterion rather than here.

## What this deliberately does not cover

* **Whether the audit was run.** A plan can carry the phase, never run
  it, and stay green. The check measures presence, which is all a grep
  can measure. What catches an unrun audit is the plan not being
  markable `Complete`, which is a human gate and stays one.
* **Plans with a terminal status that do not carry the phase.** They
  pass. This is the shared block's carve-out, not an oversight: a plan
  whose work has landed, or that was deliberately dropped or replaced,
  is not reopened to acquire a phase that would audit a diff nobody is
  going to write. It is also the difference between a check that names
  the handful of plans still able to act on a finding and one that
  files an issue against every plan the fleet has ever closed.

  The block words the carve-out as `Complete` alone. The check applies
  it to all three terminal terms of the status vocabulary, because
  `Abandoned` and `Superseded` are terminal for the same reason and
  the block's silence about them is a gap rather than a decision.
  Rewording the block bumps its version and stales every embedded copy
  across the fleet, which is a sweep; it is scheduled as one, in
  `docs/plans/PLAN-push-audit-phase.md`.
* **Plans with a terminal status that do carry the phase.** Not
  inspected either. Whether the audit ran is a judgement about the
  plan's own record, and the presence of a heading cannot settle it.
* **Plans with no phases the check can read.** Follow-up lists, issue
  trackers and single-commit plans are written without an Execution
  table. There is no last phase for the rule to bind, and inventing
  one would report work nobody ever phased.
* **Phases filed under a heading the check does not recognise.** A
  plan can carry phases the check cannot see, and such a plan passes.
  The list of phase-bearing headings is empirical rather than
  principled: it is the set the fleet has been observed to write, not
  a rule anybody agreed to in advance. divergulent's
  `PLAN-release-1.0.md` is the case that taught us this -- eight
  numbered sections under `## Must-do workstreams`, tracked as phases
  in that repository's index, invisible to the check until
  `workstreams` was added to the list. There will be another shape.

  This is why both the pass and the fail message *name* the plans the
  check declined to judge rather than counting them. Failing them is
  not the answer -- it would fail ryll's standalone issue-tracking
  plans, which are legitimately unphased -- but a verdict that hides
  them is how the next `PLAN-release-1.0.md` stays hidden. The names
  are there so a person can read the handful by hand.
* **Repositories with no plan practice.** No index, no finding.

## Template

No template of its own. The canonical wording of the phase is the
`plan-push-audit-phase` shared block in
`templates/shared-blocks/plan-push-audit-phase.md`, which every
`PLAN-TEMPLATE.md` must carry -- that is the `plan-template`
criterion's business, so a repository whose template lacks the block
and whose plans lack the phase is told both things once each, by the
criterion that owns each.

To fix a finding where the plan has no audit phase at all, add one as
the last row of the plan's Execution table (or as its last phase
section), and say in it that it runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in the plan against the default branch.

Where the plan has an audit phase that later phases have overtaken,
which fix applies depends on whether that audit has run, and the
finding says which:

* **The audit has not run** -- its own status is not terminal. The
  phases are simply in the wrong order: move the audit phase after the
  ones it must audit, leaving one audit phase.
* **The audit ran and the plan was then reopened** -- its status is
  terminal, and phases were appended after it. Append a *new* audit
  phase covering the reopened work. Moving the finished phase to the
  end would claim it audited work that landed after it, which is a
  false record of what was audited; the plan ends up with two audit
  phases, and that is correct, because there were two bodies of work.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](compliance.md#plan-audit-phase).
