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

Last regenerated: 2026-07-24T08:30:28.387058+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#80 |
| client-python | non-compliant | shakenfist/client-python#339 |
| clingwrap | non-compliant | shakenfist/clingwrap#87 |
| cloudgood | N/A | - |
| divergulent | non-compliant | shakenfist/divergulent#38 |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#159 |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | shakenfist/library-utilities#34 |
| occystrap | non-compliant | shakenfist/occystrap#66 |
| ryll | N/A | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3482 |

Details for non-compliant projects:

- **agent-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **client-python** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **clingwrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **divergulent** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **kerbside** (Status): Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **library-utilities** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **occystrap** (Status): Missing .github/workflows/pin-indirect-dependencies.yml; Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing # END_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
- **shakenfist** (Status): Missing # START_OF_INDIRECT_DEPS marker in pyproject.toml; Missing tools/pin-indirect-dependencies.sh (reconciler script from the template)
<!-- consistency-audit:end -->
