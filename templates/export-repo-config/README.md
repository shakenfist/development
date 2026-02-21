# Export Repository Configuration Template

This template sets up daily export of GitHub repository configuration
(settings, rulesets, branch protection) to version control. When
configuration changes are detected, a PR is created for review.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `export-repo-config.yml` | `.github/workflows/export-repo-config.yml` | Daily export workflow |

## How it works

The workflow delegates to the shared reusable workflow in
[shakenfist/actions](https://github.com/shakenfist/actions). It
runs daily at 00:30 UTC and can be triggered manually.

When repository configuration has changed since the last export,
the shared workflow creates a PR with the updated settings for
review.

## Customisation

This workflow is project-agnostic and can be copied directly
with no modifications.

## Prerequisites

- The shared workflow in `shakenfist/actions`
- The caller workflow must set `permissions: contents: write`
  and `pull-requests: write` so the default token can push
  branches and create PRs

## Projects using this template

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live (full implementation) |
| [imago](https://github.com/shakenfist/imago) | Live |
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
| [agent-python](https://github.com/shakenfist/agent-python) | Added |
