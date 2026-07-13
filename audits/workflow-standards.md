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
* Small non-mutating jobs: `self-hosted` `static` runners.

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

Last regenerated: 2026-07-13T09:32:24.778377+00:00

| Project | Permissions | Linting | flake8wrap | Runners | Issue |
|---------|--------|--------|--------|--------|--------|
| agent-python | compliant | compliant | non-compliant | non-compliant | shakenfist/agent-python#105, shakenfist/agent-python#82 |
| client-python | compliant | compliant | compliant | compliant | - |
| clingwrap | compliant | compliant | compliant | compliant | - |
| cloudgood | N/A | compliant | N/A | N/A | - |
| divergulent | compliant | compliant | compliant | non-compliant | shakenfist/divergulent#46 |
| instar | compliant | compliant | N/A | compliant | - |
| kerbside | non-compliant | compliant | non-compliant | compliant | shakenfist/kerbside#59, shakenfist/kerbside#94 |
| kerbside-patches | non-compliant | compliant | N/A | non-compliant | shakenfist/kerbside-patches#1446, shakenfist/kerbside-patches#953 |
| library-utilities | compliant | compliant | compliant | compliant | - |
| occystrap | compliant | compliant | non-compliant | compliant | shakenfist/occystrap#67 |
| ryll | compliant | compliant | N/A | non-compliant | shakenfist/ryll#155 |
| shakenfist | compliant | compliant | non-compliant | non-compliant | shakenfist/shakenfist#3057, shakenfist/shakenfist#3376 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **agent-python** (Runners): 3 unmarked GitHub-hosted runner reference(s): functional-tests.yml:153 (ubuntu-latest), functional-tests.yml:188 (ubuntu-latest), functional-tests.yml:199 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **divergulent** (Runners): 1 unmarked GitHub-hosted runner reference(s): build-classification.yml:34 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **kerbside** (Permissions): 5 workflow(s) missing top-level permissions: direct-qemu-functional.yml, functional-tests.yml, pin-indirect-dependencies.yml, release.yml, rust.yml
- **kerbside** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-patches** (Permissions): 8 workflow(s) missing top-level permissions: auto-retry-infra-failures.yml, ci-reporting.yml, daily-rebase-checks.yml, functional-tests.yml, heal-data-prs.yml, local-container-builds.yml, rebase-tests.yml, trigger-downstream.yml
- **kerbside-patches** (Runners): 2 unmarked GitHub-hosted runner reference(s): auto-retry-infra-failures.yml:18 (ubuntu-latest), heal-data-prs.yml:22 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **ryll** (Runners): 22 unmarked GitHub-hosted runner reference(s): ci.yml:36 (ubuntu-latest), ci.yml:74 (ubuntu-latest), ci.yml:125 (ubuntu-latest), ci.yml:129 (ubuntu-24.04-arm), ci.yml:133 (macos-latest), ci.yml:137 (windows-latest), ci.yml:141 (windows-11-arm), manual-build.yml:26 (ubuntu-latest), manual-build.yml:43 (ubuntu-latest), manual-build.yml:49 (ubuntu-24.04-arm), manual-build.yml:55 (macos-latest), manual-build.yml:61 (windows-latest), manual-build.yml:72 (windows-11-arm), release.yml:13 (ubuntu-latest), release.yml:76 (ubuntu-latest), release.yml:80 (ubuntu-24.04-arm), release.yml:84 (macos-latest), release.yml:88 (windows-latest), release.yml:92 (windows-11-arm), release.yml:210 (ubuntu-latest), release.yml:275 (ubuntu-latest), release.yml:303 (ubuntu-latest). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (Runners): 4 unmarked GitHub-hosted runner reference(s): functional-tests.yml:323 (ubuntu-2404), functional-tests.yml:324 (ubuntu-2404), scheduled-tests.yml:58 (ubuntu-2404), scheduled-tests.yml:59 (ubuntu-2404). Move to a self-hosted runner, or mark deliberate exceptions with an "audit-ok: github-hosted-runner" comment
<!-- consistency-audit:end -->
