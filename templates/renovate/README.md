# Renovate Templates

These templates set up Renovate for automated dependency updates
in Shaken Fist projects.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `renovate.yml` | `.github/workflows/renovate.yml` | Hourly workflow |
| `renovate.json` | `renovate.json` (repo root) | Renovate config |

## Placeholders

Replace when copying:

| Placeholder | Example | Description |
|-------------|---------|-------------|
| `{{GITHUB_REPO_NAME}}` | `agent-python` | GitHub repository name |

Only `renovate.yml` has a placeholder (the autodiscover filter).
`renovate.json` can be copied directly, then customised with
project-specific package grouping rules.

## The pre-commit manager

`renovate.json` enables renovate's `pre-commit` manager. That manager
ships disabled, so a repository that copies an older config keeps
`.pre-commit-config.yaml` outside renovate's view entirely -- the hook
revisions never appear on the dependency dashboard, and nothing about
the repository looks wrong while they age.

This is worth being deliberate about because of what those hooks are.
They are the linters that gate every commit, so the file nobody is
watching is the one deciding whether everything else is acceptable.
It was found in `instar`, where `actionlint` sat four months behind
while cargo, dockerfile and github-actions dependencies stayed current.

Any of the three supported forms satisfies the audit:

```json
{"pre-commit": {"enabled": true}}
{"enabledManagers": ["pre-commit", "..."]}
{"extends": [":enablePreCommit"]}
```

Repositories with no `.pre-commit-config.yaml`, or whose config only
declares `repo: local` hooks, have nothing to bump and are not checked.

## Customisation

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

- A `RENOVATE_TOKEN` secret with repository write access
- Self-hosted runners with the `static` label

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
