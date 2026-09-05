# Audit: Release process

## What we check

* There is no `release.sh` in the project directory.
* All Python projects use `pyproject.toml` (not `requirements.txt`
  or `test-requirements.txt`).
* If `pyproject.toml` exists, there must be a
  `.github/workflows/release.yml` and a `RELEASE-SETUP.md`.
* Releases use GitHub signed tags and Sigstore signing.
* A release job which attaches assets downloads them to a named
  destination (`name:` or `merge-multiple: true`) and sets
  `fail_on_unmatched_files: true`, so that a glob matching nothing
  fails the job instead of publishing an empty release.

## Template

Template: `templates/release-automation/`
See: `templates/release-automation/README.md`
Docs: `docs/release-automation.md`

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](compliance.md#release-process).
