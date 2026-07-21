# Audit: Pinning indirect dependencies

## What we check

Python projects with `pyproject.toml` should have:

* `.github/workflows/pin-indirect-dependencies.yml` -- runs daily and
  reconciles the pinned indirect dependency block against what the
  direct dependencies actually require, creating a PR when the block
  changed (new transitive dependencies pinned, stale pins removed).
* `tools/pin-indirect-dependencies.sh` -- the reconciler script,
  copied unchanged from the template. It demotes existing pins to pip
  constraints for a fresh resolve; see its header comment for details
  including the `# never-pin: <name>` escape hatch.
* `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS` markers in
  `pyproject.toml` delimiting the block the script regenerates
  (without both markers the script refuses to run).

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

Last regenerated: 2026-07-21T08:32:21.932577+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#80 |
| client-python | non-compliant | shakenfist/client-python#339 |
| clingwrap | non-compliant | shakenfist/clingwrap#87 |
| cloudgood | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#38 |
| instar | N/A | - |
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
