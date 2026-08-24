<!-- shared-block: plan-push-audit-phase v2 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase: a plan already `Complete` when the
  phase reached its repository is not reopened to acquire one, and
  a plan that has the phase runs it even if it reaches `Complete`
  before the phase does.
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
  the plan file appeared" is far too wide. It has to be recorded.
  As each phase lands, the commit that put it on the default branch
  goes into the plan -- the merge commit of its pull request, or the
  phase's last commit where it landed directly. Where the Execution
  phases are a table that is a `Merged` column, added last so that a
  row which omits it still reaches `Status`; where they are prose
  sections it is a `Merged:` line in the phase's own section. The
  `Status` column keeps its single vocabulary term and nothing else
  (see `plan-status-vocabulary`).
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank: recover what you can from
  `gh pr list --state merged`, `git log --merges --first-parent` and
  the plan file's own history, record every commit a phase landed
  under rather than forcing one, and say in the plan that the range
  was reconstructed. Where a phase accreted over months of unrelated
  commits and no range is recoverable, say that instead and name the
  paths the audit read -- an audit that says what it could not scope
  is a result; one that silently audits nothing is not.
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
