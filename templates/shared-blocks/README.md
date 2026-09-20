# Shared blocks

A shared block is a paragraph of canonical wording that is embedded
verbatim in files across many repositories -- for example the README
discipline instructions inside each repository's `PUSH-AUDIT.md`.
Shared prose drifts: each repository mutates its copy slightly, and
improvements to the wording never propagate. Shared blocks fix that
with the same begin/end-marker discipline the consistency-audit
compliance page already uses, plus a version number.

## Format

Each file in this directory holds one canonical block, markers
included:

```markdown
<!-- shared-block: <name> v<N> -->
...canonical wording...
<!-- shared-block-end -->
```

The `<name>` must match the filename (`<name>.md`). Repositories
embed the whole block -- markers and all -- verbatim.

## How the audit uses these

`scripts/audit-check.py` (the `push-audit` and `plan-template`
checks) verify that an embedded block:

- exists where it is required -- `readme-discipline`,
  `llm-doc-discipline`, `comment-proportion`,
  `plan-phase-references` and `source-file-size` in
  `PUSH-AUDIT.md`;
  `plan-file-conventions`, `plan-status-vocabulary`,
  `subagent-execution-model`, `plan-planning-effort`,
  `subagent-step-guidance`, `subagent-model-roster`,
  `plan-review-checklist`, `plan-closeout-sections` and
  `plan-push-audit-phase` in `PLAN-TEMPLATE.md`;
- carries the current version number; and
- matches the canonical wording exactly (modulo trailing
  whitespace).

`plan-phase-landing` is canonical here and embedded in this
repository's `PLAN-TEMPLATE.md`, but is deliberately **not** in
`PLAN_TEMPLATE_BLOCKS` in `scripts/audit/checks/plans.py` yet. Adding
it there marks every repository carrying `PLAN-TEMPLATE.md` that does
not embed the block non-compliant at once, and files an issue against
each -- ten repositories are applicable today and eight of them would
newly fail. The plan-template section of `docs/audits/compliance.md`
is regenerated daily and is the current figure. The point of the
block is to reduce documentation round trips rather than to generate
a fresh batch of them, so add it to that list when there is appetite
for the sweep; it is a one-line change and nothing else depends on
the timing.

Read `plan-phase-landing` together with `plan-push-audit-phase`: the
first says the push-audit phase records no `Merged` cell, which is
the one exception to the second's account of that column, and the
fleet carries the second without the first until the sweep lands.

## Updating a block

1. Edit the canonical file here.
2. Bump the version number in its begin marker.
3. Commit. The next daily consistency-audit run marks every
   repository carrying the old version non-compliant and files
   issues automatically -- no per-repository chasing needed.

Never edit an embedded copy directly; fix the canonical file and
re-copy it.
