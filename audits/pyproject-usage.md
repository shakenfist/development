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
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
