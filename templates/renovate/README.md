# Renovate template

This template sets up Renovate for automated dependency updates in a
Shaken Fist project. Renovate itself runs centrally, from the fleet
runner in shakenfist/development, so a repository carries only its
configuration. Everything about the runner -- how it is dispatched,
where it runs, what it installs and how its own pins are kept current
-- is in the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-central-runner),
and deliberately not repeated here.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `renovate.json` | `renovate.json` (repo root) | Extends the fleet preset |

## Copying the file

`renovate.json` is just `$schema` and
`"extends": ["local>shakenfist/development:renovate-fleet"]`. The
fleet policy lives once, in that preset, `renovate-fleet.json` at the
root of shakenfist/development, and a repository adds only what is
particular to it: project-specific package grouping rules, a Python
version constraint, or a range strategy, as the sections below
describe. That makes it not byte-identical between repositories once
customised, and the audit does not expect it to be.

Whatever else it carries, it must keep the `extends`, and it must not
set at its top level any key the preset sets, other than `$schema`,
`extends` and `packageRules`: a repository's own value replaces the
preset's, so the fleet's policy would quietly stop applying. The
audit fails a repository for either, and for keeping a Renovate
workflow of its own. The
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#what-we-check)
gives the reasons.

### The fleet preset

The preset carries the whole fleet policy, so a repository that
extends it needs nothing of its own: the author and assignee
Renovate uses, the dependency dashboard, rollback pull requests,
`minimumReleaseAge`, the `pre-commit` manager,
`customManagers:githubActionsVersions`, the timezone and the cadence,
the rule that merges nothing automatically, the two rules that keep
the fleet runner's `node-version` and its pinned Renovate in step,
and the `shellcheck-py/shellcheck-py` versioning rule. What each of
those is set to is listed once, in the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-fleet-preset),
and deliberately not repeated here: a policy written out twice drifts,
and the copy that drifts unnoticed is the one in a template.

One part of it is worth stating twice, because it is the one place
the cadence could cost a repository a security fix. The preset sets
no `vulnerabilityAlerts`, so Renovate's defaults apply: a fix for a
vulnerability Dependabot reports ignores the schedule,
`minimumReleaseAge` and grouping, and arrives at once rather than
waiting for Saturday. The one rule that suppresses even a security
fix applies only to the fleet runner's own workflow, which no other
repository has.

Renovate reads the preset from the default branch of
shakenfist/development, so a change to it cannot be tried from a pull
request branch: it takes effect for the whole fleet the moment it
merges. The only check before that is pre-commit's
`renovate-config-validator`, which that repository's
`.pre-commit-config.yaml` aims at the preset by name and runs with
`--no-global`. See the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-fleet-preset)
for why.

## The pre-commit manager

The fleet preset enables renovate's `pre-commit` manager, which ships
disabled, so every repository that extends the preset has its hook
revisions on the dependency dashboard with nothing of its own to add.
Why that matters more than the usual stale dependency is in the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-pre-commit-manager).

### shellcheck-py's four-part tags

Enabling the manager is not enough for the shellcheck hook. Upstream
tags a fourth, packaging, component (`v0.11.0.1`), which renovate's
default versioning cannot parse, so the lookup fails with "Can't find
version matching v0.11.0.1" on the dependency dashboard and the hook is
never bumped. That is the same silent ageing the manager was enabled to
prevent, with a warning only the dashboard shows. The fleet preset's
`shellcheck-py/shellcheck-py` package rule sets a regex versioning
that reads all four components, so a repository that extends the
preset needs nothing of its own; the rule is inert in one that does
not use the hook. `kerbside` found this first, and its
`renovate.json` additionally groups the hook
with the `tox.ini` pin of the same tool.

## Customisation

Everything below is something a repository adds to its `renovate.json`
beside the `extends` of the fleet preset, not a replacement for it.
None of it is a top-level key the preset sets. A repository that needs
a different policy from the fleet's says so in a `packageRules` entry
scoped to the packages or files it is about, not by overriding the
preset's value.

### Python version constraints

Projects that support multiple Linux distributions should add a
`constraints.python` field to `renovate.json` matching the oldest
supported Python version. This prevents renovate from proposing
dependency updates incompatible with older distros:

```json
{
  "constraints": {
    "python": ">=3.8"
  }
}
```

Document the supported platforms in `ARCHITECTURE.md` and add
comments in `pyproject.toml` pointing back to that table. See
`agent-python` for a complete example.

### Package grouping

Add `packageRules` entries to group tightly coupled dependencies
so they are bumped together. Common examples:

```json
{
  "packageRules": [
    {
      "description": "Group grpc packages together",
      "matchPackagePatterns": ["^grpcio", "^protobuf"],
      "groupName": "grpc packages"
    }
  ]
}
```

See `shakenfist/renovate.json` for a more complex example with
multiple groups (pydantic, grpc, zope, etc).

### Range strategy for client/library projects

Client and library projects use relaxed dependency ranges (`>=`)
rather than exact pins so they work across many distributions and
Python versions. For these projects, grpc packages should use
`rangeStrategy: "widen"` so renovate only creates PRs when a new
major version falls outside the existing range:

```json
{
  "description": "Group grpc packages together with widen strategy",
  "matchPackagePatterns": [
    "^grpcio",
    "^googleapis-common-protos",
    "^protobuf"
  ],
  "groupName": "grpc packages",
  "rangeStrategy": "widen"
}
```

Server projects (shakenfist, kerbside) use exact pins and the
default range strategy. The
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md)
has the full rationale.

## Prerequisites

The repository needs no secret, runner or workflow of its own for
Renovate. It needs only to be one the fleet runner can reach: the
runner's token has to be able to push to it, and the runner checks
that before every pass, as the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-central-runner)
describes. Security pull requests also need Dependabot alerts enabled
in the repository's settings, since those are what Renovate reads.

## Projects using these templates

| Project | Status | Range strategy |
|---------|--------|----------------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live | default (pin) |
| [occystrap](https://github.com/shakenfist/occystrap) | Live | widen |
| [imago](https://github.com/shakenfist/imago) | Live | default (pin) |
| [agent-python](https://github.com/shakenfist/agent-python) | Live | widen |
| [client-python](https://github.com/shakenfist/client-python) | Live | widen |
| [client-python-k3s](https://github.com/shakenfist/client-python-k3s) | Live | widen |
| [clingwrap](https://github.com/shakenfist/clingwrap) | Live | widen |
