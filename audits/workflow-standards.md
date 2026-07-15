# Audit: Workflow standards

## What we check

### Workflow permissions

All GitHub Actions workflows must have a top-level `permissions`
block to restrict the default `GITHUB_TOKEN` scope. This is flagged
by GitHub Advanced Security if missing.

* Read-only workflows: `permissions: contents: read`.
* Mixed-need workflows: `permissions: {}` at top level with per-job
  overrides.

### Workflow and job naming

* Display names should be English sentences with correct
  capitalisation. No kebab case.
* The **id** of the job can be machine-friendly.

### Runner preferences

* Use `self-hosted` runners except under exceptional circumstances.
  GitHub-provided runner minutes are limited per month, so jobs must
  not leak onto GitHub-hosted runners without a documented reason.
* Legitimate exceptions (e.g. Windows and macOS builds where we own
  no suitable hardware, as in ryll) must be marked with an
  `audit-ok: github-hosted-runner` comment on -- or immediately
  above -- the line referencing the GitHub-hosted label, ideally
  with a reason:

  ```yaml
      # audit-ok: github-hosted-runner -- no self-hosted macOS hardware
      runs-on: macos-latest
  ```

* The automated check flags any workflow line referencing a
  GitHub-hosted runner label (`ubuntu-latest`, `windows-2022`,
  `macos-15`, etc.) without an exception marker, including matrix
  values that feed `runs-on: ${{ matrix.os }}`.
* Claude Code automation jobs: `claude` runners only.

### Static runners for small, non-mutating jobs

* Small jobs that do not change the state of the runner -- linting,
  metadata checks, audits, anything that does not install OS packages
  or otherwise mutate the machine -- should run on a `static` runner
  (`runs-on: [self-hosted, static]`). Static runners are a
  pre-provisioned, long-lived pool, so they are much cheaper to start
  than a fresh VM runner. Reserve `vm` runners for jobs that genuinely
  need a throwaway machine (for example because they install packages
  or need root that would dirty the host).
* A static runner advertises exactly the `self-hosted` and `static`
  labels. A job that requests a `static` runner must therefore ask for
  those two labels **only** -- `runs-on: [self-hosted, static]`.
  Adding an impossible extra label such as a size (`s`, `l`), `vm`, or
  an operating system (`debian-12`) asks for a runner that does not
  exist, so the job is never scheduled and waits forever. The
  automated check flags any `runs-on:` that combines `static` with
  additional labels.

### Functional test workflow naming

* Functional testing must be in `functional-test.yml`.
* Must include `workflow_dispatch` as a trigger (needed by
  `pr-retest.yml`).

### PIPESTATUS for piped commands

When piping through `tee` in a `run:` step, always use the
`${PIPESTATUS[0]}` pattern to capture the upstream exit code.
Do not rely on `$?` (captures last command only) or `set -eo
pipefail` alone (unreliable on self-hosted runners).

### flake8wrap.sh correctness

Projects with `tools/flake8wrap.sh` must not quote
`${filtered_files}` on the diff/flake8 invocation line. Add
`shellcheck disable=SC2086` with an explanatory comment. The
script should also filter to `.py` files, skip `_pb2` generated
files, and handle deleted files.

### CI linting

* `actionlint`, `shellcheck`, and `.pre-commit-config.yaml` that
  runs them.
* Helper shell scripts should have shellcheck pre-commit hooks.

### PyPI caching

Self-hosted runners should use the devpi PyPI cache at
`http://192.168.1.4:3141` to reduce network load.

## Template

No single template -- these are standards applied across all
workflow files. See `templates/ci-review-automation/` for examples
of correctly structured workflows.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-15T08:15:40.094463+00:00

| Project | Permissions | Linting | flake8wrap | Runners | Static tags | Issue |
|---------|--------|--------|--------|--------|--------|--------|
| agent-python | compliant | compliant | non-compliant | non-compliant | compliant | shakenfist/agent-python#105, shakenfist/agent-python#82 |
| client-python | compliant | compliant | compliant | compliant | compliant | - |
| clingwrap | compliant | compliant | compliant | compliant | compliant | - |
| cloudgood | N/A | compliant | N/A | N/A | N/A | - |
| divergulent | compliant | compliant | compliant | compliant | compliant | - |
| instar | compliant | compliant | N/A | compliant | compliant | - |
| kerbside | non-compliant | compliant | non-compliant | compliant | compliant | shakenfist/kerbside#59, shakenfist/kerbside#94 |
| kerbside-patches | non-compliant | compliant | N/A | non-compliant | compliant | shakenfist/kerbside-patches#1446, shakenfist/kerbside-patches#953 |
| library-utilities | compliant | compliant | compliant | compliant | compliant | - |
| occystrap | compliant | compliant | non-compliant | compliant | compliant | shakenfist/occystrap#67 |
| ryll | compliant | compliant | N/A | compliant | compliant | - |
| shakenfist | compliant | compliant | non-compliant | non-compliant | compliant | shakenfist/shakenfist#3057, shakenfist/shakenfist#3376 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **agent-python** (Runners): 3 unmarked GitHub-hosted runner reference(s): functional-tests.yml:153 (ubuntu-latest), functional-tests.yml:188 (ubuntu-latest), functional-tests.yml:199 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **kerbside** (Permissions): 5 workflow(s) missing top-level permissions: direct-qemu-functional.yml, functional-tests.yml, pin-indirect-dependencies.yml, release.yml, rust.yml
- **kerbside** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-patches** (Permissions): 8 workflow(s) missing top-level permissions: auto-retry-infra-failures.yml, ci-reporting.yml, daily-rebase-checks.yml, functional-tests.yml, heal-data-prs.yml, local-container-builds.yml, rebase-tests.yml, trigger-downstream.yml
- **kerbside-patches** (Runners): 2 unmarked GitHub-hosted runner reference(s): auto-retry-infra-failures.yml:18 (ubuntu-latest), heal-data-prs.yml:22 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (Runners): 4 unmarked GitHub-hosted runner reference(s): functional-tests.yml:333 (ubuntu-2404), functional-tests.yml:334 (ubuntu-2404), scheduled-tests.yml:58 (ubuntu-2404), scheduled-tests.yml:59 (ubuntu-2404). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
<!-- consistency-audit:end -->
