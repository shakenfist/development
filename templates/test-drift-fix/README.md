# Test Drift Fix Templates

These templates set up Claude Code-powered automatic test fixing
for Shaken Fist projects. This is most useful for projects with
large test suites or test data where tests can drift over time
(e.g. imago, occystrap).

Simple projects with small, stable test suites typically don't
need this automation.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `pr-fix-tests.yml` | `.github/workflows/pr-fix-tests.yml` | Bot trigger workflow |
| `test-drift-fix.yml` | `.github/workflows/test-drift-fix.yml` | Test fixer implementation |

## How it works

1. A maintainer comments `@shakenfist-bot please attempt to fix`
   on a PR with failing tests
2. `pr-fix-tests.yml` checks authorization and delegates to
   `test-drift-fix.yml` via `workflow_call`
3. `test-drift-fix.yml` runs the tests, feeds failures to Claude
   Code, verifies fixes, and pushes commits

`test-drift-fix.yml` can also be triggered manually via
`workflow_dispatch` for periodic test suite maintenance.

## Customisation required

`pr-fix-tests.yml` can be copied directly with no modifications.

`test-drift-fix.yml` requires heavy project-specific customisation.
Search for `{{PLACEHOLDER}}` markers and comments explaining what
to replace:

- **Install dependencies** -- add your project's dependency
  installation steps
- **Test command** -- replace with your test runner
  (e.g. `tox -epy3`, `stestr run`)
- **Claude prompt** -- customise with project context, test
  commands, and references to `AGENTS.md` / `ARCHITECTURE.md`

## Prerequisites

- Self-hosted runners with `claude-code` and `static` labels
- Claude Code CLI installed and authenticated on `claude-code`
  runners
- The shared action
  [shakenfist/actions/pr-bot-trigger](https://github.com/shakenfist/actions)

## Projects using these templates

| Project | Status |
|---------|--------|
| [imago](https://github.com/shakenfist/imago) | Live (original) |
| [occystrap](https://github.com/shakenfist/occystrap) | Live |
