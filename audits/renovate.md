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
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
