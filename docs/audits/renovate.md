# Audit: Renovate for dependency bumps

## What we check

* `.github/workflows/renovate.yml` exists -- runs renovate hourly
  on a self-hosted runner.
* `renovate.json` exists -- with package grouping rules and
  scheduling.
* Only the `RENOVATE_AUTODISCOVER_FILTER` value changes per repo.
* `renovate.json` enables the `pre-commit` manager, when the
  repository has remote pre-commit hooks to manage.

### The pre-commit manager

Renovate's `pre-commit` manager is opt-in: cargo, dockerfile,
github-actions and the Python managers are on by default, but
`.pre-commit-config.yaml` is not read at all unless the config says
so. A repository can therefore look fully renovate-managed while its
hook revisions age untouched, because nothing reports on a file the
bot was never told to look at.

That matters more than the usual stale-dependency case. Pre-commit
hooks are the linters gating every commit, so an unwatched hook pin
means the thing judging everything else is itself unjudged. `instar`
was four months behind on `actionlint` while its cargo, dockerfile and
github-actions dependencies were current, and the drift was only found
by looking for a trivially small pull request.

Any of renovate's three enabling forms passes:

```json
{"pre-commit": {"enabled": true}}
{"enabledManagers": ["pre-commit", "..."]}
{"extends": [":enablePreCommit"]}
```

The check only applies when there is something to bump. A repository
with no `.pre-commit-config.yaml`, or one whose hooks are all
`repo: local` (a script from the tree, carrying no revision), passes
without the manager.

`sfui` already enables it, and its `"pre-commit": {"enabled": true}`
block is the form the template now carries.

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

Last regenerated: 2026-08-23T06:45:38.740880+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#122 |
| client-python | non-compliant | shakenfist/client-python#362 |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#28 |
| clingwrap | non-compliant | shakenfist/clingwrap#117 |
| cloudgood | non-compliant | shakenfist/cloudgood#2 |
| development | compliant | - |
| divergulent | non-compliant | shakenfist/divergulent#37 |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#1582 |
| library-utilities | non-compliant | shakenfist/library-utilities#33 |
| occystrap | non-compliant | shakenfist/occystrap#112 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3757 |

Details for non-compliant projects:

- **agent-python** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **client-python** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **client-python-k3s** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **clingwrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **cloudgood** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **divergulent** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **kerbside-patches** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **library-utilities** (Status): Missing: .github/workflows/renovate.yml, renovate.json
- **occystrap** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
- **shakenfist** (Status): renovate.json does not enable the pre-commit manager, so the hook revisions in .pre-commit-config.yaml are unmanaged and drift silently
<!-- consistency-audit:end -->
