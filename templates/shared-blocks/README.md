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
  `llm-doc-discipline`, `diagram-discipline`,
  `comment-proportion`, `source-file-size`,
  `plan-phase-references`, `path-traversal-review`,
  `python-version-discipline` and `functional-test-coverage` in
  `PUSH-AUDIT.md`;
  `plan-file-conventions`, `plan-status-vocabulary`,
  `subagent-execution-model`, `plan-planning-effort`,
  `subagent-step-guidance`, `subagent-model-roster`,
  `plan-review-checklist`, `plan-closeout-sections`,
  `plan-push-audit-phase` and `plan-phase-landing` in
  `PLAN-TEMPLATE.md`;
- carries the current version number; and
- matches the canonical wording exactly (modulo trailing
  whitespace).

`plan-phase-landing` has been enforced since 2026-09-20, the day
after it was written, rather than left advisory for repositories to
pick up. An advisory rule is adopted by the repositories that were
already going to adopt it, and this one is aimed at a systemic
problem -- merge conflicts in `REVIEWS.md` and plan files, and
round-trip pull requests spent on prose -- so the repositories it
exists for are the ones that would decline it. `source-file-size`
was enforced the same day and for the same reason. The cost was one
morning of issues: eight of the ten repositories carrying a
`PLAN-TEMPLATE.md` newly failed `plan-template`, and seven of the
eleven carrying a `PUSH-AUDIT.md` newly failed `push-audit`. Each
issue is closed by a verbatim copy of the block and names the file
to copy it from; `docs/audits/compliance.md` is regenerated daily
and is the current figure.

Read `plan-phase-landing` together with `plan-push-audit-phase`: the
first says the push-audit phase records no `Merged` cell, which is
the one exception to the second's account of that column.

## Updating a block

1. Edit the canonical file here.
2. Bump the version number in its begin marker.
3. Commit. The next daily consistency-audit run marks every
   repository carrying the old version non-compliant and files
   issues automatically -- no per-repository chasing needed.

Never edit an embedded copy directly; fix the canonical file and
re-copy it.
