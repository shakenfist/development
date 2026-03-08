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

| Project | Permissions | Linting | flake8wrap | Issue |
|---------|-------------|---------|------------|-------|
| agent-python | compliant | compliant | needs SC2086 | shakenfist/agent-python#82 |
| client-python | non-compliant | non-compliant | needs SC2086 | shakenfist/client-python#323 |
| clingwrap | compliant | compliant | compliant | - |
| cloudgood | N/A | compliant | N/A | - |
| imago | compliant | compliant | N/A | - |
| kerbside | compliant | compliant | needs SC2086 | shakenfist/kerbside#59 |
| kerbside-patches | non-compliant | compliant | N/A | shakenfist/kerbside-patches#953 |
| library-utilities | N/A | non-compliant | needs cleanup | shakenfist/library-utilities#37 |
| occystrap | compliant | compliant | needs cleanup | shakenfist/occystrap#67 |
| ryll | N/A | compliant | N/A | - |
| shakenfist | compliant | compliant | needs SC2086 | shakenfist/shakenfist#3057 |

N/A for permissions: no workflows directory.
N/A for flake8wrap: project does not have a flake8wrap.sh script.
