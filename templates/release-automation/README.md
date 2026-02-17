# Release Automation Templates

These templates set up automated PyPI releases with Sigstore signing
for Shaken Fist Python projects.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `release.yml` | `.github/workflows/release.yml` | GitHub Actions workflow |
| `RELEASE-SETUP.md` | `RELEASE-SETUP.md` (repo root) | One-time setup guide |

## Placeholders

Replace the following placeholders when copying to a project:

| Placeholder | Example | Description |
|-------------|---------|-------------|
| `{{PROJECT_DISPLAY_NAME}}` | `Occy Strap` | Human-readable project name |
| `{{PYPI_PACKAGE_NAME}}` | `occystrap` | Package name on PyPI |
| `{{GITHUB_REPO_NAME}}` | `occystrap` | GitHub repository name |

The `release.yml` workflow only uses `{{PROJECT_DISPLAY_NAME}}` (in the
header comment). The workflow itself is project-agnostic since PyPI
trusted publishers and GitHub environments handle the per-project
binding.

## Prerequisites

The target project must:

- Use `pyproject.toml` (not `setup.py` or `setup.cfg`)
- Use `setuptools_scm` or similar for version detection from git tags
- Have no `release.sh` (remove it first)

## Quick Start

```bash
# From the target project root:
cp /path/to/development/templates/release-automation/release.yml \
    .github/workflows/release.yml
cp /path/to/development/templates/release-automation/RELEASE-SETUP.md \
    RELEASE-SETUP.md

# Edit placeholders
sed -i 's/{{PROJECT_DISPLAY_NAME}}/My Project/g' \
    .github/workflows/release.yml
sed -i 's/{{PYPI_PACKAGE_NAME}}/my-project/g' RELEASE-SETUP.md
sed -i 's/{{GITHUB_REPO_NAME}}/my-project/g' RELEASE-SETUP.md

# Then follow RELEASE-SETUP.md for the one-time GitHub/PyPI setup
```

## Projects Using This Template

| Project | Status |
|---------|--------|
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
| [kerbside](https://github.com/shakenfist/kerbside) | Live |
| [agent-python](https://github.com/shakenfist/agent-python) | Added |
