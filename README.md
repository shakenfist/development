# Shaken Fist Development Documentation

This repository contains development documentation, guides, and learnings from
the Shaken Fist project.

## Documentation

- [Automated PR Review with Claude Code](docs/automated-pr-review.md) - How we use
  Claude Code to automate code review and address review comments

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

## About Shaken Fist

Shaken Fist is a deliberately minimal cloud orchestration system. For more
information, visit [shakenfist.com](https://shakenfist.com).

## License

Apache 2.0
