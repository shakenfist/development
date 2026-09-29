# Release Automation Templates

These templates set up automated PyPI releases with Sigstore signing
for Shaken Fist Python projects.

`release.yml` is a per-repository adaptation, not expected to be
byte-identical between repositories. Deployments differ in how many
jobs they run and how each publishes -- kerbside adds a Rust wheel
build, ryll signs the tag on a GitHub-hosted runner rather than a
self-hosted `vm` (both throwaway, for the same reason: see "The
sign-tag runner" below), and hunkydory runs no `sign-tag` job at all,
so its release tags are unsigned. A fleet-wide comparison of copies
against this template should expect drift here rather than report it.

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

## The sign-tag runner

`sign-tag` installs gitsign with a `sudo mv` into `/usr/local/bin` and
writes global git config, so it mutates the runner and cannot use the
shared static pool, which grants no passwordless sudo and would carry
that global git config into whatever else lands on the same machine
next. The template runs it on `[self-hosted, vm, debian-13, s]`.
Phase 3 of the review import plan
(`docs/plans/PLAN-review-import.md`) found the template missing the
`vm` label -- `[self-hosted, debian-13, s]` names neither `static` nor
`vm`, so it matched no runner and the job queued forever -- the same
class of bug kerbside hit and fixed in commit 631a936 ("Run the
release signing job on a vm runner"), there against
`[self-hosted, debian-12, static]`. ryll reaches the same throwaway
property a different way, on a GitHub-hosted `ubuntu-latest` runner
with an `audit-ok: github-hosted-runner` marker -- a legitimate
per-repository choice, not something to converge on.

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
