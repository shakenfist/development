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

### Range strategy

Client/library projects should use `rangeStrategy: "widen"` for
grpc package groups so renovate only fires on major version
changes. Server projects use the default (pin-bumping) strategy.
See `PROJECT-CONSISTENCY-AUDITS.md` for full rationale.

## Template

Template: `templates/renovate/`
See: `templates/renovate/README.md`

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
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| divergulent | non-compliant | shakenfist/divergulent#37 |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#33 |
| occystrap | compliant | - |
| ryll | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **cloudgood** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **divergulent** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **library-utilities** (Status): Missing: .github/workflows/renovate.yml, renovate.json
<!-- consistency-audit:end -->
