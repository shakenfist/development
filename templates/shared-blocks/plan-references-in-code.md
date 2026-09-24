<!-- shared-block: plan-references-in-code v1 -->
Plan references in code (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-references-in-code.md`):

- Code, comments, docstrings, test names, fixture descriptions and
  configuration describe the software as it is now. Which plan,
  phase, step or decision produced a line is history, and the plan
  and the commit log already keep it. Do not write "added in phase
  5", "per decision 3", "pending step 5f" or "the phase-4 leaks
  pass": a reader of the code has not read the plan, and the
  number tells them nothing.
- Where a comment cites a plan to explain why the code is the way
  it is, the explanation belongs in the comment. Write the reason
  -- the constraint, the measurement, the failure it prevents --
  and drop the citation. A pointer standing in for the reasoning
  costs every reader a detour, and rots when the plan is archived
  or renumbered.
- A plan link is acceptable only for work that is not built yet: a
  deliberate gap or refusal whose lifting is planned, where
  "deferred; see `PLAN-foo.md`" tells the reader the gap is known.
  The link comes out when the work lands. Prefer an issue link
  where one exists, and write a plan in another repository as an
  absolute URL; the `plan-source-references` audit checks that
  these links resolve.
- Plan documents and commit messages may cite phases and decisions
  freely; recording that history is their job.
- "Phase" in its ordinary sense -- a two-phase commit, a compiler's
  link phase -- is not a plan reference.
- A plan reference a diff adds to code is a finding to fix before
  pushing. References on lines the diff does not touch are backlog,
  not findings against the change.
<!-- shared-block-end -->
