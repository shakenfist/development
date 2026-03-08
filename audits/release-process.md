# Audit: Release process

## What we check

* There is no `release.sh` in the project directory.
* All Python projects use `pyproject.toml` (not `requirements.txt`
  or `test-requirements.txt`).
* If `pyproject.toml` exists, there must be a
  `.github/workflows/release.yml` and a `RELEASE-SETUP.md`.
* Releases use GitHub signed tags and Sigstore signing.

## Template

Template: `templates/release-automation/`
See: `templates/release-automation/README.md`
Docs: `docs/release-automation.md`

## Projects

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | compliant | - |
| client-python | non-compliant | |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| imago | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | non-compliant | |
| occystrap | compliant | - |
| ryll | N/A | - |
| shakenfist | compliant | - |

N/A: Not a Python package released to PyPI.
