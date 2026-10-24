# Audit: Release process

## Where it applies

The criterion applies to a repository with a `pyproject.toml` or a
`.github/workflows/release.yml`, and to nothing else. It has two halves
with different scopes:

* The **packaging** rules need a `pyproject.toml`, because they are
  about how a Python project is laid out for release.
* The **workflow-safety** rules need a `release.yml`, because they are
  about what that workflow does. A Rust or TypeScript release signs and
  publishes the same way a Python one does, so hunkydory, ryll, instar
  and andris are measured by this half even though they have no
  `pyproject.toml` at the top of the tree.

## What we check

The packaging rules, where there is a `pyproject.toml`:

* There is no `release.sh` in the project directory.
* All Python projects use `pyproject.toml` (not `requirements.txt`
  or `test-requirements.txt`).
* There must be a `.github/workflows/release.yml` and a
  `RELEASE-SETUP.md`.
* Releases use GitHub signed tags and Sigstore signing.

The workflow-safety rules, where there is a `release.yml`:

* A release job which attaches assets downloads them to a named
  destination (`name:` or `merge-multiple: true`) and sets
  `fail_on_unmatched_files: true`, so that a glob matching nothing
  fails the job instead of publishing an empty release. A quoted
  `"true"` is the same value: an action receives every input as a
  string.
* Where `release.yml` offers `workflow_dispatch`, its publishing jobs
  are confined to a pushed tag with
  `if: github.event_name == 'push' && startsWith(github.ref,
  'refs/tags/v')`. A dispatch aimed at a branch would otherwise have
  `sign-tag` force-push a `refs/tags/refs/heads/<branch>` tag; one aimed
  at an existing tag would re-sign and force-push it. Build jobs are
  exempt: running them is what the dispatch is for.
* A job which downloads artifacts and does not start from a cleaned
  checkout downloads into `${{ runner.temp }}`. Such a job inherits
  whatever the previous job left on that runner, and
  `download-artifact` adds to a directory rather than replacing it. A
  checkout earns the exemption only when it cleans, so `clean: false`
  does not count. `packages-dir` is not judged: the PyPI publish runs
  in a container that sees only the workspace, so that input must be
  relative.
* The steps reading the distribution (`subject-path` on the attestation,
  `files` on the release) name the directory the download actually
  filled. Moving one and not the other leaves a step globbing an empty
  directory. A glob is compared by the directory it reads, so
  `dist/*.tar.gz` agrees with a download to `dist/`. A block scalar
  (`files: |`, one path per line) is not read, and so is not judged.
* A job running a known container action (`pypa/gh-action-pypi-publish`)
  takes its paths relative to the workspace, and therefore checks out.
  The runner does mount its directories into the container, but not
  where the workflow expressions point: `RUNNER_TEMP` appears at
  `/github/runner_temp` while `${{ runner.temp }}` expands to the host
  path. An absolute path is rejected even inside the workspace, because
  host and container disagree about where that is. This criterion takes
  precedence over the one above, which is why such a job must check out
  rather than use `runner.temp`.
* **A release waits for CI.** Exactly one job runs
  `shakenfist/actions/require-ci-success`, which fails unless the
  named CI workflows succeeded on the tagged commit. Every job that
  names an environment is downstream of it through `needs:`, because
  those are the jobs that sign and publish, and an environment's
  reviewer checks that a release is wanted, not that it was tested.
  Being downstream is not enough when a job's `if:` uses `always()` or
  `!cancelled()`: such a job runs whatever became of the jobs it
  needs. So every job between the gate and an environment that uses
  either one also requires `needs.<job>.result == 'success'` for each
  job it needs.
  The template's `build` guard also lets a skipped gate through, so
  that a `workflow_dispatch` dry run still builds. That is accepted
  only where it cannot reach a release. The condition must also say
  `github.event_name != 'push'`, and every environment job below it
  must be confined to a pushed tag. A pushed tag is the one event on
  which the gate always runs. The conditions are not parsed as
  boolean expressions, so these clauses are looked for rather than
  proved to be joined by `&&`.
  A `needs:` that names a job which does not exist, or a set of jobs
  that need each other in a loop, is reported as a finding. GitHub
  will not run such a workflow, and which jobs wait for CI cannot be
  read from it.
* **The workflows the gate names can give it a run.** Each file in the
  gate's `workflows:` input exists under `.github/workflows/`. Each
  triggers on `merge_group`, or on a push that reaches `develop`, the
  two events whose runs the gate counts. A workflow on neither has no
  run on any merged commit, so every release fails at the gate, after
  the tag is pushed.
  Each trigger brings its own way to pass a commit that was not
  tested, and the rule checks both:
  - **On a push**, a concurrency group keyed on `github.ref`, at the
    workflow or a job, puts every commit on `develop` in one group. A
    later run then cancels an earlier one, and a cancelled run is not
    a success. `cancel-in-progress: false` does not prevent this: a
    group holds one running and one pending run, so a third arrival
    cancels the pending one. The group has to branch on
    `github.event_name == 'push'` and key by sha there. The key the
    merge queue needs falls back to `github.ref`, so on a push
    workflow it fails here. How merge-group runs cancel is
    [merge-group-cancellation](merge-group-cancellation.md)'s to
    judge, and is not repeated here.
  - **On a merge group**, every `dorny/paths-filter` step in a
    workflow triggered by `merge_group` either passes a `base:` that
    references `github.event.merge_group.base_sha`, or is confined to
    `github.event_name == 'pull_request'` by its own `if:` or its
    job's. A filter that diffs a merge group against the branch name
    finds no changes on a re-run after the entry has merged. It then
    passes untested, and the gate counts that run.
* **Every wheel a release uploads has been through `check-dist`.**
  `shakenfist/actions/check-dist` fails a wheel with a member under a
  directory named `tests` or `*_tests`. The release copy is the one
  that stops a bad wheel, because it checks the artefact that is
  uploaded. A job handles wheels if it builds with `python -m build`
  or `PyO3/maturin-action`, or uploads a path naming `*.whl`. In such
  a job, a `check-dist` step comes after every build step it
  recognises and before every `actions/upload-artifact` step. Its
  `path:` (`dist/` by default) names the directory each upload reads,
  compared as the distribution paths above are.

What these rules cannot see:

* **A named workflow whose runs conclude `skipped`.** The gate counts
  only a run that concluded `success`. So a workflow whose jobs all
  skip on a merge group fails every release, even though it triggers
  correctly. kerbside's `direct-qemu-functional.yml` does this on
  every merge group, which is why kerbside's gate names
  `functional-tests.yml` alone. Whether a run skips is decided by its
  jobs' conditions at run time, and the trigger rule does not read
  that.
* **A path filter written in shell.** The merge-group rule reads
  `dorny/paths-filter` steps only. A script that runs `git diff` is
  not told apart from any other script, and no gated workflow in the
  fleet has one.
* **A wheel job that builds through a script and uploads a bare
  directory.** It shows neither signal the `check-dist` rule looks
  for, so the rule does not judge it. ryll and andris build through
  scripts but upload `*.whl`, so they are judged; nothing in the fleet
  has the other shape today.

## Template

Template: `templates/release-automation/`
See: `templates/release-automation/README.md`
Docs: `docs/release-automation.md`

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](compliance.md#release-process).
