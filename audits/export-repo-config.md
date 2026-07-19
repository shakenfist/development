# Audit: Exporting repo configuration changes

## What we check

* `.github/workflows/export-repo-config.yml` exists.
* The workflow delegates to the shared reusable workflow in
  `shakenfist/actions` and runs daily at 00:30 UTC.
* The workflow is project-agnostic and can be copied directly with
  no modifications.

## Template

Template: `templates/export-repo-config/`
See: `templates/export-repo-config/README.md`

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-19T08:23:16.060743+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | compliant | - |
| client-python | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#3 |
| divergulent | non-compliant | shakenfist/divergulent#39 |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#951 |
| library-utilities | non-compliant | shakenfist/library-utilities#35 |
| occystrap | compliant | - |
| ryll | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **cloudgood** (Status): Missing .github/workflows/export-repo-config.yml
- **divergulent** (Status): Missing .github/workflows/export-repo-config.yml
- **kerbside-patches** (Status): Missing .github/workflows/export-repo-config.yml
- **library-utilities** (Status): Missing .github/workflows/export-repo-config.yml
<!-- consistency-audit:end -->
