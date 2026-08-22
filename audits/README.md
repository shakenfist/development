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
4. Add an empty consistency-audit marker block under "## Projects"
   (see the file structure below). The compliance table between the
   markers is regenerated daily by the consistency-audit workflow
   from `scripts/audit-check.py` results -- never edit it by hand.
5. Add an automated check to `scripts/audit-check.py` and register it
   in `scripts/audit_common.py` (`AUDIT_METADATA` and `ISSUE_TITLES`).
   The workflow then files and closes GitHub issues (labelled
   `consistency`) on target projects automatically.

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

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
```

## In-scope projects

The following projects are subject to consistency audits:

- actions
- agent-python
- client-python
- client-python-k3s
- clingwrap
- cloudgood
- development
- divergulent
- instar
- kerbside
- kerbside-patches
- library-utilities
- occystrap
- ryll
- shakenfist
- sfui

One project is in scope for part of the audit only:

- private-ci -- the `sfui-vendor` check, and nothing else. It is
  internal tooling and excluded from the conventions, but it vendors
  sfui and a vendored copy drifts silently. The scoping lives in
  `REPO_OVERRIDES` in `scripts/audit-check.py`; every other check
  reports N/A for it.

See `PROJECT-CONSISTENCY-AUDITS.md` for the list of excluded projects
(internal tooling and historical archives).

## Audit index

| File | Criterion |
|------|-----------|
| [llm-tooling.md](llm-tooling.md) | AGENTS.md, ARCHITECTURE.md, Claude skills |
| [llm-doc-structure.md](llm-doc-structure.md) | AGENTS.md and ARCHITECTURE.md are a summary and an index, detail lives in docs/ |
| [llm-context-lint.md](llm-context-lint.md) | Agent context passes skillsaw at error severity, and every skill actually loads |
| [llm-context-lint-ci.md](llm-context-lint-ci.md) | skillsaw runs in pre-commit and CI, not just in the daily audit |
| [release-process.md](release-process.md) | pyproject.toml, release.yml, RELEASE-SETUP.md |
| [ci-review-automation.md](ci-review-automation.md) | Automated review, developer automation workflows |
| [renovate.md](renovate.md) | Renovate for dependency bumps |
| [pin-indirect-dependencies.md](pin-indirect-dependencies.md) | Pinning transitive dependencies |
| [export-repo-config.md](export-repo-config.md) | Repo configuration export |
| [default-branch-naming.md](default-branch-naming.md) | Default branch conventions |
| [github-security.md](github-security.md) | Dependabot, secret scanning, CodeQL |
| [delete-branch-on-merge.md](delete-branch-on-merge.md) | Branches are deleted automatically when a PR merges |
| [merge-queue-config.md](merge-queue-config.md) | Merge queues process entries serially, without speculative stacking or merge batching |
| [merge-group-cancellation.md](merge-group-cancellation.md) | Superseded merge group runs are cancelled, not left building clouds |
| [security-sanitization.md](security-sanitization.md) | HTTP header and file path sanitization |
| [workflow-standards.md](workflow-standards.md) | Permissions, naming, self-hosted runners, static runner tags, devpi cache fallback, devpi cache address, linting, PIPESTATUS, flake8wrap |
| [expensive-lane-path-filter.md](expensive-lane-path-filter.md) | Expensive PR lanes skip docs-only and review-marks-only changes |
| [console-logging.md](console-logging.md) | Console script logging setup |
| [python-version.md](python-version.md) | Python version targeting and type hints |
| [pyproject-usage.md](pyproject-usage.md) | Python projects use pyproject.toml |
| [version-file-gitignore.md](version-file-gitignore.md) | Generated version files are gitignored |
| [rust-unwrap-lint.md](rust-unwrap-lint.md) | Rust projects lint against production unwrap() |
| [readme-absolute-links.md](readme-absolute-links.md) | Top-level README.md links are absolute |
| [docs-external-links.md](docs-external-links.md) | Links out of docs/ resolve inside docs/, or else are absolute |
| [readme-structure.md](readme-structure.md) | Top-level README.md is a pitch, detail lives in docs/ |
| [plan-phase-references.md](plan-phase-references.md) | Docs describe current behaviour, not plan phase history |
| [plan-source-references.md](plan-source-references.md) | Plan references in source and configuration still resolve |
| [plan-index.md](plan-index.md) | docs/plans/index.md layout, date ordering, plan coverage and the status vocabulary |
| [push-audit.md](push-audit.md) | PUSH-AUDIT.md naming and versioned shared blocks |
| [plan-template.md](plan-template.md) | PLAN-TEMPLATE.md shared blocks, including the sub-agent model roster |
| [test-coverage.md](test-coverage.md) | Unit and functional test coverage |
| [secret-handling.md](secret-handling.md) | Secret scanner in CI, credentials kept out of logs |
| [review-coverage.md](review-coverage.md) | Human review backlog stays under threshold in repos with review tracking |
| [sfui-vendor.md](sfui-vendor.md) | Vendored sfui copies are verbatim and current |
