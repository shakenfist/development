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

## Prerequisites

- A `RENOVATE_TOKEN` secret with repository write access
- Self-hosted runners with the `static` label

## Projects using these templates

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live |
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
| [imago](https://github.com/shakenfist/imago) | Live |
| [agent-python](https://github.com/shakenfist/agent-python) | Added |
