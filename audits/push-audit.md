# Audit: Pre-push audit file

## What we check

Repositories that carry a pre-push audit runbook must:

* name it **`PUSH-AUDIT.md`** -- the historical name
  `PUSH-TEMPLATE.md` is flagged as legacy (the file is a runbook the
  operator follows before pushing, not a template that gets copied,
  and the `-TEMPLATE` suffix is reserved for true templates like
  `PLAN-TEMPLATE.md`);
* embed the current **`readme-discipline` shared block** in its
  documentation-review section; and
* keep every embedded shared block verbatim and at the current
  version.

Repositories with no pre-push audit file at all are reported as N/A:
whether every project should have one is a separate decision, not
smuggled in here.

### Shared blocks

A shared block is canonical wording embedded verbatim across
repositories, delimited by versioned markers:

```markdown
<!-- shared-block: <name> v<N> -->
...canonical wording...
<!-- shared-block-end -->
```

Canonical copies live in `templates/shared-blocks/<name>.md` in this
repository (markers included); see
`templates/shared-blocks/README.md` for the mechanism. The check
fails when an embedded block is missing where required, carries a
stale version, has drifted from the canonical wording, is unknown
(no canonical file), or is missing its end marker.

To update shared wording: edit the canonical file, bump its version,
and commit. The next daily audit run marks every repository carrying
the old version non-compliant and files issues automatically.

This audit exists because the pre-push audit files drifted
independently in each repository -- several still instructed the
documentation reviewer that "`README.md` reflects any new features",
which is the exact feedback loop that bloats READMEs (see the
`readme-structure` audit for the policy those instructions now
enforce instead).

## Template

Template: `templates/shared-blocks/`
See: `templates/shared-blocks/README.md`

To fix a non-compliant repository: rename `PUSH-TEMPLATE.md` to
`PUSH-AUDIT.md` (updating references in `AGENTS.md`,
`MERGE-TEMPLATE.md`, `tools/audit/`, and plan documents), and paste
the current contents of
`templates/shared-blocks/readme-discipline.md` verbatim into the
documentation-review section, replacing any older README guidance it
contradicts.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-29T08:43:42.897373+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | N/A | - |
| client-python | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#463 |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | non-compliant | shakenfist/occystrap#100 |
| ryll | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **instar** (Status): legacy filename PUSH-TEMPLATE.md (rename to PUSH-AUDIT.md and update references); missing shared block readme-discipline (copy it verbatim from templates/shared-blocks/readme-discipline.md in the development repository)
- **occystrap** (Status): legacy filename PUSH-TEMPLATE.md (rename to PUSH-AUDIT.md and update references); missing shared block readme-discipline (copy it verbatim from templates/shared-blocks/readme-discipline.md in the development repository)
<!-- consistency-audit:end -->
