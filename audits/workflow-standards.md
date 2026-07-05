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

* Strongly prefer `self-hosted` runners.
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

Last regenerated: 2026-07-05T08:56:49.417207+00:00

| Project | Permissions | Linting | flake8wrap | Issue |
|---------|--------|--------|--------|--------|
| agent-python | compliant | compliant | non-compliant | shakenfist/agent-python#82 |
| client-python | compliant | compliant | compliant | - |
| clingwrap | compliant | compliant | compliant | - |
| cloudgood | N/A | compliant | N/A | - |
| divergulent | compliant | compliant | compliant | - |
| imago | compliant | compliant | N/A | - |
| kerbside | non-compliant | compliant | non-compliant | shakenfist/kerbside#59, shakenfist/kerbside#94 |
| kerbside-patches | non-compliant | compliant | N/A | shakenfist/kerbside-patches#953 |
| library-utilities | compliant | compliant | compliant | - |
| occystrap | compliant | compliant | non-compliant | shakenfist/occystrap#67 |
| ryll | compliant | compliant | N/A | - |
| shakenfist | compliant | compliant | non-compliant | shakenfist/shakenfist#3057 |

Details for non-compliant projects:

- **agent-python** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside** (Permissions): 4 workflow(s) missing top-level permissions: direct-qemu-functional.yml, functional-tests.yml, pin-indirect-dependencies.yml, release.yml
- **kerbside** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **kerbside-patches** (Permissions): 7 workflow(s) missing top-level permissions: auto-retry-infra-failures.yml, ci-reporting.yml, daily-rebase-checks.yml, functional-tests.yml, local-container-builds.yml, rebase-tests.yml, trigger-downstream.yml
- **occystrap** (flake8wrap): Missing shellcheck disable=SC2086 directive
- **shakenfist** (flake8wrap): Missing shellcheck disable=SC2086 directive
<!-- consistency-audit:end -->
