# Consistency Audit Specifications

This directory contains one file per audit criterion. Each file defines
what we check, links to the relevant template, and tracks per-project
compliance status.

## How audits work

Each audit file is independently checkable. An agent can be spawned per
file to check all projects against that single criterion, enabling
parallel audits.

## How to add a new audit

1. Create a new `.md` file in this directory following the structure
   below.
2. Fill in the "What we check" section with the criterion description.
3. Link to the template directory if one exists.
4. Populate the projects table with current compliance status.
5. Create GitHub issues (labelled `consistency`) on non-compliant
   target projects linking back to the audit spec and template.

## File structure

Each audit file follows this structure:

```markdown
# Audit: <name>

## What we check
<concise description of the audit criterion>

## Template
Template: `templates/<name>/`
See: `templates/<name>/README.md`

## Projects
| Project | Status | Issue |
|---------|--------|-------|
| shakenfist | compliant | - |
| imago | compliant | - |
| occystrap | non-compliant | #42 |
```

## In-scope projects

The following projects are subject to consistency audits:

- agent-python
- client-python
- clingwrap
- cloudgood
- imago
- kerbside
- kerbside-patches
- library-utilities
- occystrap
- ryll
- shakenfist

See `PROJECT-CONSISTENCY-AUDITS.md` for the list of excluded projects
(internal tooling and historical archives).

## Audit index

| File | Criterion |
|------|-----------|
| [llm-tooling.md](llm-tooling.md) | AGENTS.md, ARCHITECTURE.md, Claude skills |
| [release-process.md](release-process.md) | pyproject.toml, release.yml, RELEASE-SETUP.md |
| [ci-review-automation.md](ci-review-automation.md) | Automated review, developer automation workflows |
| [renovate.md](renovate.md) | Renovate for dependency bumps |
| [pin-indirect-dependencies.md](pin-indirect-dependencies.md) | Pinning transitive dependencies |
| [export-repo-config.md](export-repo-config.md) | Repo configuration export |
| [default-branch-naming.md](default-branch-naming.md) | Default branch conventions |
| [github-security.md](github-security.md) | Dependabot, secret scanning, CodeQL |
| [security-sanitization.md](security-sanitization.md) | HTTP header and file path sanitization |
| [workflow-standards.md](workflow-standards.md) | Permissions, naming, linting, PIPESTATUS, flake8wrap |
| [console-logging.md](console-logging.md) | Console script logging setup |
| [python-version.md](python-version.md) | Python version targeting and type hints |
| [test-coverage.md](test-coverage.md) | Unit and functional test coverage |
