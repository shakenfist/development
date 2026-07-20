# Audit: pyproject.toml usage

## What we check

All Python projects must use `pyproject.toml` for packaging and
dependency management:

* Any repository containing tracked Python files must have a
  `pyproject.toml` at the repository root.
* Legacy packaging files (`setup.py`, `setup.cfg`) must not exist
  alongside it.

Repositories where Python is incidental are excluded: Rust projects
(any Python present is helper scripts), docs-only repositories, and
`kerbside-patches` (a patch archive with Python helper scripts, not a
Python project -- excluded via `REPO_OVERRIDES` in
`scripts/audit-check.py`).

Note that `requirements.txt` / `test-requirements.txt` removal is
covered by the [release process audit](release-process.md), and the
generated version file rules are covered by the
[generated version file audit](version-file-gitignore.md).

## Template

No template -- use the `pyproject.toml` in `kerbside`, `occystrap`,
and `shakenfist` as examples of our implementation style.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-20T09:17:47.894467+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | compliant | - |
| client-python | compliant | - |
| clingwrap | compliant | - |
| cloudgood | N/A | - |
| divergulent | compliant | - |
| instar | N/A | - |
| kerbside | compliant | - |
| kerbside-patches | N/A | - |
| library-utilities | compliant | - |
| occystrap | compliant | - |
| ryll | N/A | - |
| shakenfist | compliant | - |
<!-- consistency-audit:end -->
