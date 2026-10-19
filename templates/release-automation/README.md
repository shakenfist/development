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
| `{{CI_WORKFLOWS}}` | `ci.yml` | The CI workflows the release gate requires |

The `release.yml` workflow uses `{{PROJECT_DISPLAY_NAME}}` (in the
header comment) and `{{CI_WORKFLOWS}}` (in the `ci-gate` job). Apart
from those the workflow is project-agnostic, since PyPI trusted
publishers and GitHub environments handle the per-project binding.

`{{CI_WORKFLOWS}}` is one or more bare workflow file names, such as
`ci.yml`, not paths, one per line under `workflows: |`. When there is
more than one, put each on its own line at the same indentation:

```yaml
        with:
          workflows: |
            python-unit-tests.yml
            functional-tests.yml
```

Name every workflow that gives a merged commit its test result, and
no others: see "Prerequisites for gated CI" below.

## The CI gate

`ci-gate` fails the release unless the named CI workflows succeeded
on the tagged commit. It sits upstream of every other job: `build`
needs it, and the publishing jobs need `build`. A failure therefore
means that nothing was signed or published. The job is skipped on a
`workflow_dispatch` run, which is how a dry run still exercises the
build, and `build` carries a guard that accepts a skipped gate only
on a run that publishes nothing.

The gate has no override, deliberately: an input that skipped the
check would be one more thing the reviewer approving the `release`
environment has to notice, on a page that does not show CI status.

How the gate decides is documented with the action, not here, and
that page is the authority:
[`require-ci-success`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#require-ci-success).
In outline:

- What counts as CI is a run of a named workflow on the commit, from
  a `push` to the default branch or from that branch's merge queue.
  Every other event is ignored.
- The tagged commit must be on the default branch's first-parent
  history.
- Every counted run must have succeeded.
- A commit with no CI run is crossed to its first parent only if it
  changed nothing but the review marks (`REVIEWS.md` and the
  `.vscode/*.weaudit` files) that `prune-reviews` commits after a
  merge. A merge commit is never crossed.

The same page documents the wheel check. The `check-dist` step in
`build` fails the release if the wheel holds a test directory, which
is nearly always `include-package-data` offering every tracked file
as package data. Its default path of `dist/` suits this template's
build. See
[`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist).

### Recovering from a gate failure

A gate failure means nothing was published, so every case is
recoverable. Which fix applies depends on why the gate failed:

| Why the gate failed | What to do |
|---------------------|------------|
| The commit was merged by bypassing the merge queue, so it is a merge commit with no queue run | Delete the tag and tag a later commit that went through the queue |
| The tag is not on the default branch's first-parent history | Delete the tag and tag a commit that is: the merge commit that brought the change in, or a later one |
| The commit changed only files that `paths-ignore` skips, such as docs, so CI never ran | Delete the tag and move it to the last commit CI did run on |
| A CI run failed for a reason unrelated to the change, such as a flaky test | Re-run the failed CI runs until they succeed, then use "Re-run jobs" on the release's `ci-gate` job |
| A CI run is still going | Wait for it, then re-run the `ci-gate` job |

"Re-run jobs" replays the original tag push event, so the guards
still pass, and the gate reads each run's newest attempt. Every
failed run on the commit needs re-running, not only the newest.

Commits made after a bypass merge, including the review-mark commits
that `prune-reviews` adds on top of it, fail for the same reason as
the bypass itself, because the gate does not cross a merge commit.

## The sign-tag runner

`sign-tag` installs gitsign with a `sudo mv` into `/usr/local/bin` and
writes global git config, so it mutates the runner and cannot use the
shared static pool, which grants no passwordless sudo and would carry
that global git config into whatever else lands on the same machine
next. The template runs it on `[self-hosted, vm, debian-13, s]`.

The `vm` label is what makes that a requirement. GitHub gives a job
to any runner carrying every label it lists, so a job asking only for
`[self-hosted, debian-13, s]` can land on a vm runner (they carry
`debian-13` and `s`) but equally on any other runner that happens to
carry those two labels. Naming `vm` is the only way to insist on a
throwaway runner. kerbside moved its `sign-tag` job to a vm runner for
the same reason in commit 631a936 ("Run the release signing job on a
vm runner"). ryll reaches the same throwaway property a different
way, on a GitHub-hosted `ubuntu-latest` runner with an
`audit-ok: github-hosted-runner` marker -- a legitimate
per-repository choice, not something to converge on.

## Prerequisites

The target project must:

- Use `pyproject.toml` (not `setup.py` or `setup.cfg`)
- Use `setuptools_scm` or similar for version detection from git tags
- Have no `release.sh` (remove it first)

### Prerequisites for gated CI

The gate can only pass if CI leaves a result it can count on the
commit that is tagged. For each workflow named in `{{CI_WORKFLOWS}}`:

- It runs on `push` to the default branch, with a `branches:` filter
  so that a pushed tag starts no CI of its own, or it runs on
  `merge_group`.
- With a merge queue, the run that counts is the queue's: the
  `merge_group` run on the commit is the one the gate finds. The
  workflow therefore needs a `merge_group` trigger, and a pull
  request run does not count, because its head is never the merged
  commit.
- It must not conclude `skipped` on the event that counts. A
  workflow whose every job is skipped on `merge_group` concludes
  `skipped`, which is not success, so naming it fails every release.
  Name the workflows that actually run there.
- A `concurrency` group must never cancel a push run. On a push the
  ref is the same for every commit, so a group keyed on `github.ref`
  with `cancel-in-progress` lets the next merge cancel the previous
  commit's run, and a cancelled run fails the gate. Even
  `cancel-in-progress: false` is not enough, because a group holds
  one running and one pending run and a third arrival cancels the
  pending one. Key the group by commit on a push instead, and keep
  cancelling on pull requests and merge groups, where a newer run
  does supersede the older:

  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.event_name == 'push' && github.sha || github.ref }}-lint
    cancel-in-progress: true
  ```

  Each job with its own group changes the trailing job name.
- A `paths-ignore` on the trigger means a commit it skips has no
  run. A tag on that commit fails the gate, which is the intended
  result: it is never released. Use the recovery table above. If
  workflows named together have different `paths-ignore` lists, a
  commit one of them ran on and another skipped fails as only
  part-tested, so name workflows whose filters match.

## Note for converting the template

If this template is converted to a reusable workflow, or otherwise
changed in how it is called, the converted workflow must still run
`ci-gate` upstream of `build`, or releases will no longer be gated.

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
