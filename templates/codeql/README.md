# CodeQL Analysis Template

This template sets up GitHub CodeQL code scanning for Python
projects.

**Important:** CodeQL code scanning requires GitHub Advanced
Security (GHAS) for private repositories. Since we don't have
GHAS, this template is only for **public** repositories. Private
repos (e.g. `imago`) should **not** include a CodeQL workflow.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `codeql-analysis.yml` | `.github/workflows/codeql-analysis.yml` | CodeQL scanning workflow |

## Customisation

The template targets the `develop` branch. If your project uses
a different default branch, update the `branches:` lists in the
`push` and `pull_request` triggers.

## Permissions

The workflow uses restrictive permissions:

- Top-level `permissions: {}` (no default permissions)
- Job-level `actions: read` (required for workflow run telemetry)
- Job-level `contents: read` (required to checkout code)
- Job-level `security-events: write` (required to upload results)

The `actions: read` permission is required for CodeQL to access
workflow run information. Without it, you'll see "Resource not
accessible by integration" errors.

## Prerequisites

- The repository must be **public** (or have a GHAS license)
- Self-hosted runners with the `static` label

## Projects using this template

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live |
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
| [agent-python](https://github.com/shakenfist/agent-python) | Added |
