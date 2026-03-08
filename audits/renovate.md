# Audit: Renovate for dependency bumps

## What we check

* `.github/workflows/renovate.yml` exists -- runs renovate hourly
  on a self-hosted runner.
* `renovate.json` exists -- with package grouping rules and
  scheduling.
* Only the `RENOVATE_AUTODISCOVER_FILTER` value changes per repo.

### Python version constraints

Projects supporting multiple Linux distributions should set
`constraints.python` in `renovate.json` to match the oldest Python
version they support (matching `requires-python` in `pyproject.toml`).

Currently required for: agent-python, occystrap.

### Package grouping

Projects with tightly coupled dependencies (e.g. the grpc stack)
should group them in `renovate.json` so they are bumped together.

## Template

Template: `templates/renovate/`
See: `templates/renovate/README.md`

## Projects

| Project | Status | Issue |
|---------|--------|-------|
| agent-python | compliant | - |
| client-python | non-compliant | shakenfist/client-python#319 |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| imago | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#950 |
| library-utilities | non-compliant | shakenfist/library-utilities#33 |
| occystrap | compliant | - |
| ryll | non-compliant | |
| shakenfist | compliant | - |
