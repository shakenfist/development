# Shared blocks

A shared block is a paragraph of canonical wording that is embedded
verbatim in files across many repositories -- for example the README
discipline instructions inside each repository's `PUSH-AUDIT.md`.
Shared prose drifts: each repository mutates its copy slightly, and
improvements to the wording never propagate. Shared blocks fix that
with the same begin/end-marker discipline the consistency-audit
compliance tables already use, plus a version number.

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

`scripts/audit-check.py` (the `push-audit` check) verifies that an
embedded block:

- exists where it is required (`readme-discipline` and
  `comment-proportion` in `PUSH-AUDIT.md`);
- carries the current version number; and
- matches the canonical wording exactly (modulo trailing
  whitespace).

## Updating a block

1. Edit the canonical file here.
2. Bump the version number in its begin marker.
3. Commit. The next daily consistency-audit run marks every
   repository carrying the old version non-compliant and files
   issues automatically -- no per-repository chasing needed.

Never edit an embedded copy directly; fix the canonical file and
re-copy it.
