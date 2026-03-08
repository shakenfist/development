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

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | non-compliant | |
| client-python | non-compliant | |
| clingwrap | non-compliant | |
| cloudgood | N/A | - |
| imago | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | |
| occystrap | non-compliant | |
| ryll | N/A | - |
| shakenfist | compliant | - |

N/A: Not a Python project with `pyproject.toml`, or not released
to PyPI.
