# This file lists features we expect to see in all Shaken Fist projects

## Exceptional cases

The following projects are **excluded** from these rules due to being
**internal only tooling** or **historical archive repositories**:

* actions
* ansible-modules
* client-js
* client-go
* client-python-ova
* deploy
* development
* images
* imago-testdata
* imago-testdata-quarantine
* jenkins-private
* loadtest
* occystrap-testdata
* ostrich
* performance
* private-ci
* reproducables
* sonobouy
* symbolicmode
* terraform-provider-shakenfist
* uefi-latency-guest
* website

## LLM tooling

Every project should have an `AGENTS.md`, and `ARCHITECTURE.md`. Operations
which have been historically repetitive should be covered by a Claude skill
if they would benefit from it. Things that are likely to need a skill
include remembering to write unit or functional tests, updating documention
for user visible changes, and so forth.

## Release process

There is no `release.sh` in the project directory. All Shaken Fist projects
that release to pypi should now be using `pyproject.toml` instead of this
shell script. Similarly we don't use `requirements.txt` and
`test-requirements.txt`, we manage our dependencies in `pyproject.toml`.
If `pyproject.toml` is missing, use the ones in `kerbside`, `occystrap`, and
`shakenfist` as examples of our implementation style.

We now push releases using github signed tags. Ensure there is a
`.github/workflows/release.yml` workflow for all projects with a
`pyproject.toml`. There should also be a `RELEASE-SETUP.md` in the project
directory explaining setup.

**Templates:** Use the templates in
[`templates/release-automation/`](templates/release-automation/) as the
canonical starting point. These contain `release.yml` and
`RELEASE-SETUP.md` with placeholders for project-specific values. See
[docs/release-automation.md](docs/release-automation.md) for full details
on the release pipeline and setup steps.

## Claude Code for automated review in CI

We run Claude Code for automated review in CI. The automated reviewer
only runs once all other CI tests have passed to avoid being wasteful
with LLM credits.

**All projects must use the shared action
`shakenfist/actions/review-pr-with-claude@main`** for automated
reviews. Do not use per-project `tools/review-pr-with-claude.sh`
scripts -- the shared action is the canonical implementation and
contains the structured JSON review format, issue creation, and
markdown rendering. Using the shared action ensures all projects
benefit from improvements in one place.

The shared action produces structured JSON reviews that are:
- Validated against a schema
- Used to create GitHub issues for actionable items (`fix`/`document`)
- Rendered to markdown with embedded JSON for the `address-comments`
  automation to parse

The automated reviewer job must have `pull-requests: write` and
`issues: write` permissions so it can post review comments and
create issues. If the workflow's top-level permissions are
restrictive (e.g. `contents: read`), add job-level permissions
to the reviewer job:

```yaml
  automated_reviewer:
    permissions:
      contents: read
      pull-requests: write
      issues: write
```

There is also another job called `pr-re-review.yml` that triggers a
re-review on a PR, as by default each PR only gets one review due to
cost limitations. We should include that job in every project too.
That workflow needs top-level `permissions` including
`pull-requests: write` and `issues: write`.

**Templates:** Use the templates in
[`templates/ci-review-automation/`](templates/ci-review-automation/) as
the canonical starting point. These contain the bot-triggered workflows
and instructions for adding the automated reviewer job to your CI
workflow. See [docs/ci-review-automation.md](docs/ci-review-automation.md)
for full details on the automation system and setup steps.

## Developer automation

In addition to automated review, projects should include bot-triggered
workflows for common developer tasks:

- `pr-address-comments.yml` -- "@shakenfist-bot please address
  comments" triggers Claude Code to address review comments
- `pr-retest.yml` -- "@shakenfist-bot please retest" triggers a
  re-run of functional tests

These are available as templates in
[`templates/ci-review-automation/`](templates/ci-review-automation/).

### Test drift fixing (optional)

Projects with large test suites that are prone to drift (e.g. imago,
occystrap) should also add:

- `pr-fix-tests.yml` -- "@shakenfist-bot please attempt to fix"
  triggers Claude Code to fix CI failures
- `test-drift-fix.yml` -- implementation for the test fixer
  (requires project-specific customisation)

These are in a separate template set at
[`templates/test-drift-fix/`](templates/test-drift-fix/). Simple
projects with small, stable test suites don't need these.

## Renovate for dependency bumps

We use renovate for dependency bumps. Each project needs:

- `.github/workflows/renovate.yml` -- workflow that runs renovate
  hourly on a self-hosted runner. Only the
  `RENOVATE_AUTODISCOVER_FILTER` value changes per repo.
- `renovate.json` -- renovate configuration with package grouping
  rules and scheduling.

**Templates:** Use the templates in
[`templates/renovate/`](templates/renovate/) as the canonical
starting point. See `agent-python` for a complete example including
Python version constraints.

### Python version constraints for end-user tooling

Projects that must support multiple Linux distributions should set
`constraints.python` in `renovate.json` to match the oldest Python
version they support. This prevents renovate from proposing
dependency updates that are incompatible with older distros.

At the moment the projects which need to meet this requirement are:
agent-python; and occystrap.

The constraint should match the `requires-python` value in
`pyproject.toml`. Both values are derived from the oldest supported
distribution's system Python.

```json
{
  "constraints": {
    "python": ">=3.8"
  }
}
```

For projects with a supported platforms matrix, document the distro
list and Python versions in `ARCHITECTURE.md` and add comments in
both `pyproject.toml` and `renovate.json` pointing back to that
table. When dropping a distribution, update:

1. The supported platforms table in `ARCHITECTURE.md`
2. `requires-python` in `pyproject.toml`
3. `constraints.python` in `renovate.json`

CI should test on the oldest supported Python version to catch any
dependency bumps that break compatibility.

### Package grouping

Projects with tightly coupled dependencies (e.g. the grpc stack)
should group them in `renovate.json` so they are bumped together:

```json
{
  "packageRules": [
    {
      "description": "Group grpc packages together",
      "matchPackagePatterns": [
        "^grpcio",
        "^googleapis-common-protos",
        "^protobuf"
      ],
      "groupName": "grpc packages"
    }
  ]
}
```

## Exporting repo configuration changes

We archive github repo configuration changes using `export-repo-config.yml`.
The workflow delegates to the shared reusable workflow in
`shakenfist/actions` and runs daily at 00:30 UTC.

**Templates:** Use the template in
[`templates/export-repo-config/`](templates/export-repo-config/) as
the canonical starting point. The workflow is project-agnostic and
can be copied directly with no modifications.

## Default branch naming

**Standard:** All active repositories should use `develop` as the default branch.

Exceptions are allowed for:
- Documentation-only repos (may use `main`)
- GitHub Actions repos (conventionally use `main`)
- Archived/deprecated repos (listed in Exceptional cases above)

### Repos not using `develop` that need fixing

| Repository | Current | Action Needed |
|------------|---------|---------------|
| imago | main | Change to `develop` |
| cloudgood | main | Change to `develop` |

To change the default branch:
```bash
# Rename branch locally
git branch -m main develop
git push -u origin develop
# In GitHub UI: Settings > General > Default branch > change to develop
# Then delete old branch
git push origin --delete main
```

## GitHub security settings

All active repositories should have these settings enabled in
Settings > Code security and analysis:

| Setting | Recommended | Notes |
|---------|-------------|-------|
| Dependabot security updates | Enabled | Automatic PRs for vulnerable dependencies |
| Secret scanning | Enabled | Free for public repos |
| Secret scanning push protection | Enabled | Prevents accidental secret commits |

Additionally, these repository settings are recommended:

| Setting | Recommended | Notes |
|---------|-------------|-------|
| Delete branch on merge | Enabled | Keeps repo clean |
| Allow auto-merge | Enabled | Useful with required checks |

### Current security state (2026-02-08)

| Repository | Dependabot | Secret Scanning | Notes |
|------------|------------|-----------------|-------|
| shakenfist | Enabled | Disabled | Enable secret scanning |
| occystrap | Disabled | Disabled | Enable both |
| imago | N/A | N/A | Enable Advanced Security first |
| kerbside | Disabled | Disabled | Enable both |
| client-python | Enabled | Disabled | Enable secret scanning |
| agent-python | Disabled | Disabled | Enable both |

## GitHub CodeQL advanced security

All **public** projects should have a GitHub Advanced Security CodeQL actions
workflow.

**Templates:** Use the template in
[`templates/codeql/`](templates/codeql/) as the canonical starting
point. Update the `branches:` lists if your project doesn't use
`develop` as the default branch.

**Private repos are excluded:** CodeQL code scanning requires a paid GitHub
Advanced Security (GHAS) license for private repositories. Without GHAS,
CodeQL workflows will fail with "Advanced Security must be enabled for this
repository to use code scanning." Since we don't have GHAS, private repos
(e.g., `imago`) should **not** include a CodeQL workflow.

**Important:** The CodeQL workflow must have a job-level permissions block:

```yaml
jobs:
  analyze:
    permissions:
      actions: read      # Required for workflow run telemetry
      contents: read
      security-events: write
```

The `actions: read` permission is required for CodeQL to access workflow run
information. Without it, you'll see "Resource not accessible by integration"
errors.

## GitHub Actions workflow permissions

All GitHub Actions workflows must have a top-level `permissions` block
to restrict the default `GITHUB_TOKEN` scope. This is flagged by GitHub
Advanced Security if missing. Best practice is to set the most
restrictive top-level permissions needed (often `contents: read`), and
override at the job level where individual jobs need more (e.g.
`security-events: write` for CodeQL, `id-token: write` for releases).
Workflows where every job only reads should use
`permissions: contents: read`. Workflows with mixed needs should use
`permissions: {}` at the top level and declare per-job permissions.

## Linting for CI jobs

Please ensure we have `actionslint`, `shellcheck`, and a git precommit
that runs them setup as well. You can find examples in `kerbside`, and
`kerbside-patches`.

Additionally, we have some rules of our own:

* Workflow and job display names should always be English sentences with
  correct capitalization. No kebab case! The **id** of the job can be
  something more machine friendly, but we talk English to the humans in
  the GitHub user interface please.
* We strongly prefer `self-hosted` runners if possible.
* Claude code automation jobs can only run on `claude` runners, as the
  others are not authenticated with Anthropic.
* Small jobs which do not change the state of the runner should run on
  a `self-hosted` `static` runner -- the startup cost of the ephemeral
  runners does not make them a good choice for small jobs like linting.
* Functional testing is always in a GitHub actions workflow called
  "functional-test.yml". This matters because some of the developer
  automations need to know the name of the workflow to function.

## Piped commands in GitHub Actions must check PIPESTATUS

When piping a command through `tee` (or any other filter) in a GitHub
Actions `run:` step, the exit code of the upstream command can be
silently swallowed. Although GitHub Actions sets `pipefail` by default,
self-hosted runners may not propagate this correctly. Always use the
explicit `${PIPESTATUS[0]}` pattern:

```yaml
- name: Run something
  run: |
    set +e
    make something 2>&1 | tee output.txt
    exit_code=${PIPESTATUS[0]}
    set -e
    if [ ${exit_code} -ne 0 ]; then
      echo "Command failed with exit code ${exit_code}"
      exit ${exit_code}
    fi
```

**Common mistakes:**
- Using `$?` after a pipeline only captures the *last* command's exit
  code (i.e. `tee`, which always succeeds). You must use
  `${PIPESTATUS[0]}` to get the first command's exit code.
- Relying on `set -eo pipefail` alone -- self-hosted runners may not
  honour pipefail correctly.

The only exception is when failure is intentionally ignored (e.g.
`command | tee log.txt || true`), or when the upstream command cannot
fail (e.g. `echo ... | tee`).

## Linting for helper bash scripts

For projects that have helper shell scripts, we should include a shellcheck
precommit to ensure they're not bonkers.

## Developer automation

`imago` is the most mature example here, although `shakenfist` also has a
minor example for exceptions found in functional tests. `imago` has a
series of GitHub workflow automations that respond to comments from
authorised users to perform common actions:

* "@shakenfist-bot please attempt to fix" causes a Claude Code automation
  that attempts to fix failures found in a CI run.
* "@shakenfist-bot please address comments" causes a Claude Code automation
  that attempts to address review comments from the automated reviewer
  to run.
* "@shakenfist-bot please re-review" causes the automated reviewer to run
  again, as normally automated reviews are limited to a single review per
  PR.
* "@shakenfist-bot please retest" causes the functional tests to be rerun.
  This is needed because automated commits (for example from the automated
  review comment fixer) do not cause new CI runs.

We should move the implementation for these automations into the `actions`
repository and then progressively roll them out to all projects.

Additionally, the automated reviewer's prompt should ensure that it checks
that documentation in the docs/ directory has been updated for any user
visible changes.

## Console script logging setup

Projects that use `shakenfist_utilities.logs.setup_console()` in their
CLI entry point must also configure the root logger so that INFO messages
from **all** module loggers are visible. `setup_console(name)` only adds
a handler to the named logger; other module loggers (inputs, outputs,
filters, etc.) propagate to root, which has no handler by default. This
causes all their INFO messages to be silently dropped.

The fix is to call `logging.basicConfig(level=logging.INFO)` after
`setup_console()`, and set `propagate = False` on the main logger to
avoid duplicate output:

```python
LOG = logs.setup_console(__name__)
logging.basicConfig(level=logging.INFO)
logging.getLogger(__name__).propagate = False
```

When `--verbose` is used, update the root handler level rather than
calling `logging.basicConfig()` again (which is a no-op after the
first call):

```python
if verbose:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        handler.setLevel(logging.DEBUG)
    LOG.setLevel(logging.DEBUG)
```

## Python version

For `shakenfist`, we should always target the newest Python version packaged
by our supported host operating systems -- currently Debian 12, and
Ubuntu 24.04. For all other Python projects, we should target the oldest
system Python from the list of supported client operating systems, which are
those listed at https://images.shakenfist.com/README.

We should always use mypy type hints, although `shakenfist` has been
going through a staged rollout and should be excluded from a strict
interpretation of this requirement for now.

Specific features of modern Python that we like if available to us include:

* The walrus operator.
* f-strings.

## Unit test coverage

We should have solid unit test coverage. I don't want to put a specific
number of coverage because that seems inflexible, but whenever we see
something which should be covered by tests and isn't, we should make a
note to fix that.

## Functional test coverage

Despite believing in unit testing, we are **obsessed** with functional
testing. We've invested a lot at this point in functional testing, and
the gold standard for testing should ultimately be "do we run the code
to do the real thing and does it work as intended". Sadly, I don't have
a good way to measure functional test coverage, but at a high level the
goal is to have a test for **everything** exposed on the command line
or via an API. This is still a journey for `shakenfist`, but for the
smaller projects we should be there now and any gap is a bug to be
closed.

## Pride in our work

Finally, we should be proud of our shared work. A regular holistic review
of each project should ask what we could improve or tighten up with a
refactor. We should not be scared of large refactors if they deliver
large benefits, but we should also avoid change solely for change's
sake.