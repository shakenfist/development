# Audit: Pinning indirect dependencies

## What we check

Python projects with `pyproject.toml` should have:

* `.github/workflows/pin-indirect-dependencies.yml` -- runs daily,
  installs the project, compares `pip freeze` against
  `pyproject.toml`, and creates a PR to pin new indirect
  dependencies.
* `# END_OF_INDIRECT_DEPS` marker in `pyproject.toml` (without
  this marker the `sed` command silently does nothing).

### Application projects (shakenfist, kerbside)

Hard-pin indirect dependencies in `[project] dependencies`. Safe
because we control the runtime environment.

Template: `pin-indirect-dependencies.yml`

### Library projects (agent-python, client-python, clingwrap, occystrap, library-utilities)

Record indirect dependencies in `[project.optional-dependencies]
pinned` section. Users can install with `pip install package[pinned]`
for exact tested versions.

Template: `pin-indirect-dependencies-library.yml`

### Common requirements

Both variants need a `DEPENDENCIES_TOKEN` repository secret with
push and PR permissions.

## Template

Template: `templates/pin-indirect-dependencies/`
See: `templates/pin-indirect-dependencies/README.md`

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-08T08:33:30.675321+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#80 |
| client-python | non-compliant | shakenfist/client-python#339 |
| clingwrap | non-compliant | shakenfist/clingwrap#87 |
| cloudgood | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#38 |
| imago | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | shakenfist/library-utilities#34 |
| occystrap | non-compliant | shakenfist/occystrap#66 |
| ryll | N/A | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
- **client-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
- **clingwrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
- **divergulent** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
- **library-utilities** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
- **occystrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml
<!-- consistency-audit:end -->
