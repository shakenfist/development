# Shaken Fist Development Documentation

This repository contains development documentation, guides, and learnings from
the Shaken Fist project.

## Documentation

- [Automated PR Review with Claude Code](docs/automated-pr-review.md) -- How we use
  Claude Code to automate code review and address review comments
- [CI Review Automation](docs/ci-review-automation.md) -- Workflow templates
  for bot-triggered reviews, retests, test fixing, and comment addressing
- [Release Automation](docs/release-automation.md) -- Automated PyPI releases
  with Sigstore signing and trusted publishers

## Templates

Standardised configuration files for rolling out infrastructure
to Shaken Fist projects:

- [`templates/release-automation/`](templates/release-automation/) --
  `release.yml` workflow and `RELEASE-SETUP.md` for PyPI releases
- [`templates/ci-review-automation/`](templates/ci-review-automation/) --
  Bot-triggered workflows for PR review, retest, and comment addressing
- [`templates/test-drift-fix/`](templates/test-drift-fix/) --
  Automatic test fixing with Claude Code (for projects with large
  test suites)
- [`templates/renovate/`](templates/renovate/) --
  Renovate dependency updater workflow and configuration
- [`templates/export-repo-config/`](templates/export-repo-config/) --
  Daily repository configuration export and drift detection
- [`templates/codeql/`](templates/codeql/) --
  CodeQL code scanning for public repositories

## Shared GitHub Actions

The [shakenfist/actions](https://github.com/shakenfist/actions) repository
contains reusable GitHub Actions used across Shaken Fist projects:

- **pr-bot-trigger** - Handles `@shakenfist-bot` trigger comments on PRs
  (permission checks, reactions, messages)
- **review-pr-with-claude** - Runs automated code reviews using Claude Code

These actions reduce duplication and ensure consistent behavior across projects.

## Projects Using These Automations

The following projects have Claude Code-powered automation:

| Project | Automated Review | Test Fixer | Comment Addresser | Retest |
|---------|------------------|------------|-------------------|--------|
| [imago](https://github.com/shakenfist/imago) | Yes | Yes | Yes | Yes |
| [occystrap](https://github.com/shakenfist/occystrap) | Yes | Yes | Yes | Yes |
| [agent-python](https://github.com/shakenfist/agent-python) | Yes | No | Yes | Yes |

## About Shaken Fist

Shaken Fist is a deliberately minimal cloud orchestration system. For more
information, visit [shakenfist.com](https://shakenfist.com).

## License

Apache 2.0
