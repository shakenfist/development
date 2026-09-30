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

None: copy the file verbatim. The triggers name both `main` and
`develop`, so the same bytes serve either default branch, and a
branch a repository does not have is simply never matched.

## Behaviour

| Event | `check_paths` | `Analyze` |
|-------|---------------|-----------|
| Same-repository pull request touching code | runs, `code_changed=true` | runs |
| Same-repository pull request touching only review state and `docs/` | runs, `code_changed=false` | skipped (satisfies a required check) |
| Fork pull request | runs | skipped by the fork guard |
| Push to `main`/`develop` with code | runs, filter step skipped, `code_changed=true` | runs |
| Push touching only review state and `docs/` | workflow does not start | workflow does not start |
| Weekly schedule | runs, filter step skipped, `code_changed=true` | runs |
| `check_paths` fails | fails | runs (fails open) |

Three properties of that table matter:

- **Review-only pull requests skip at the job level.** `Analyze` is
  a required status check in some repositories (hunkydory), so a
  trigger-level `paths-ignore` on `pull_request` would leave it
  pending forever. A `check_paths` job decides instead, and a job
  skipped by `if:` reports as skipped, which satisfies the check.
  `push` carries no required check and filters at the trigger. The
  pattern, and why each detail of it matters, is step 8 of
  "Adopting a repository" in
  [docs/code-review-tracking.md](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md).
  The review paths are listed twice, in the push trigger and in the
  filter; keep them in step.
- **Fork pull requests are not scanned before merge.** Autobuild
  runs the pull request's build on the shared static runners, and
  running a stranger's build there is the worse risk. The skipped
  job satisfies a required check, so a fork's change can merge
  unscanned; the push after merge scans it. A fork's token cannot
  hold `security-events: write`, so a pre-merge analysis would have
  nowhere to upload in any case.
- **Only pull request runs cancel in progress.** Push and scheduled
  runs upload the branch's standing results, so a later run never
  cancels an earlier one mid-analysis.

## Repositories with their own copy

Two repositories keep a documented per-repository copy rather than
this file, and a fleet-wide comparison should expect them to differ:

- **kerbside** pins `languages`, because autobuild fails on its C
  file.
- **shakenfist/actions** runs a language matrix (`actions` and
  `python`) with no autobuild step.

Both should still carry the same review-path handling, fork guard,
timeout and pull-request-only cancellation as this template. A
`languages` parameter read from a repository variable was
considered and rejected: whether an unset variable behaves exactly
like an absent input is unverified, and a security scanner is the
wrong place to find out.

## Permissions

The workflow uses restrictive permissions:

- Top-level `permissions: {}` (no default permissions)
- `check_paths`: `pull-requests: read` only (it has no checkout)
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
