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

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | compliant | - |
| client-python | non-compliant | |
| clingwrap | compliant | - |
| cloudgood | non-compliant | |
| imago | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | non-compliant | |
| library-utilities | non-compliant | |
| occystrap | compliant | - |
| ryll | non-compliant | |
| shakenfist | compliant | - |
