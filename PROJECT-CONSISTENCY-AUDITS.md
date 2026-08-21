# This file lists features we expect to see in all Shaken Fist projects

## Exceptional cases

The following projects are **excluded** from these rules due to being
**internal only tooling** or **historical archive repositories**:

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

The `actions` repository used to be on that list. It is now audited: the
whole fleet depends on it for its composite actions and reusable
workflows, so it should be held to the same standards as anything else.
Two rules do not apply to it -- it has no Python to package, and it keeps
`main` as its default branch because every consumer pins to `@main`.

`private-ci` stays on that list, but is audited for one thing. It
vendors sfui, and a vendored copy is the one kind of problem an
exclusion cannot make safe: nothing in the consumer fails when the copy
falls behind, or when someone edits it in place and the next sync
discards the edit. So the `sfui-vendor` check runs against it and every
other check reports N/A, via the `only_checks` scoping in
`REPO_OVERRIDES`. Exclusion still means what it says for the rest --
`private-ci` is not expected to grow a `pyproject.toml`, a renovate
config, release workflows, or a `develop` branch.

## LLM tooling

Every project should have an `AGENTS.md`, and `ARCHITECTURE.md`. Operations
which have been historically repetitive should be covered by a Claude skill
if they would benefit from it. Things that are likely to need a skill
include remembering to write unit or functional tests, updating documention
for user visible changes, and so forth.

## AGENTS.md and ARCHITECTURE.md are a summary and an index

Those two files are a **summary and an index into `docs/`**, not reference
manuals. `AGENTS.md` is a working guide: the conventions, invariants and
gotchas an agent cannot infer by reading the code. `ARCHITECTURE.md` is a
map: the component inventory, how data moves between components, and why
the shape is the way it is. Anything longer than that -- a deep dive on one
subsystem, a configuration reference, a runbook, a wire-protocol spec --
belongs in `docs/`, where humans benefit from it as well.

One canonical home per fact. If `docs/` covers something, point at it
rather than restating it, and the same rule applies between the two files.

The automated check enforces measurable proxies: `AGENTS.md` is at most 300
lines and 2500 words, `ARCHITECTURE.md` at most 500 lines and 4000 words,
each file points at a page under `docs/` when `docs/` holds any (a link or
a backticked path both count -- these files are read on GitHub and by
agents, not rendered off-site), no `##` heading appears in both files, and
no heading names a page `docs/` already has. The judgment half is enforced at push time by the `llm-doc-discipline`
shared block in each repository's `PUSH-AUDIT.md` (see below).

`AGENTS.md` gets the tighter cap because it is loaded into every session:
its whole length is a fixed tax on every task, whether or not the task
touches the subject.

This rule exists because these two files accreted the same way the READMEs
did -- ryll's reached 1015 and 2262 lines, around 21,000 words, while
restating `docs/configuration.md`, restating `docs/control-socket-protocol.md`
in a section that names that file as the canonical source, and duplicating a
`## Code Organisation` section between the two files.

## Agent context is linted

`AGENTS.md`, `CLAUDE.md`, skills, plugins, hooks and MCP configuration
are code an agent executes against, and until now nothing checked them.
Every repository with agent context runs [skillsaw](https://skillsaw.org/),
and the audit reports its error-severity findings. See
[audits/llm-context-lint.md](audits/llm-context-lint.md).

Only the error tier counts. skillsaw's warning and info tiers carry style
opinions -- unlinked path references alone run to dozens per repository --
so reporting them would cost more time than it saves. The error tier is
structural and security: malformed frontmatter and manifests, credentials
in instruction files, Trojan Source unicode, hooks and settings that
execute arbitrary commands.

The audit also reports markdown that will never load as a skill. A skill
is `<skills dir>/<name>/SKILL.md`; a bare markdown file in
`.claude/skills/`, or a subdirectory with no `SKILL.md`, is inert. Worse,
skillsaw cannot report it either, because such a file is never discovered
as a skill -- the repository lints clean while its skills do nothing.
Twelve local checkouts were in that state when this was written, several
of them with an `AGENTS.md` asserting that their skills cover the
repetitive work.

The daily audit is a backstop, not a feedback loop. Each repository must
also run skillsaw in its own pre-commit **and** in CI, alongside
actionlint and shellcheck, so a bad skill is caught by the commit that
introduces it. Pre-commit alone is advisory -- `--no-verify` skips it, and
a fresh clone never ran `pre-commit install`. CI alone is slow. See
[audits/llm-context-lint-ci.md](audits/llm-context-lint-ci.md) for the
config to copy.

## Python packaging with pyproject.toml

All Python projects must use `pyproject.toml` for packaging and dependency
management. Legacy packaging files (`setup.py`, `setup.cfg`) should not
exist alongside it. Repositories where Python is incidental -- Rust
projects with helper scripts, docs-only repositories, the `actions`
library of composite actions and reusable workflows, and the
`kerbside-patches` patch archive -- are exempt.

### Generated version files

Our `pyproject.toml` files configure `setuptools_scm` to write a generated
version file (conventionally `<package>/_version.py`, via the `write_to`
setting) into the source tree at build time. That file must **never** be
committed to git:

* The configured version file path must be covered by `.gitignore`.
* No `_version.py` file may be tracked by git. If one has been
  accidentally committed, remove it with `git rm --cached <path>` and add
  the `.gitignore` entry.

A tracked copy shadows the build-time version, causing stale or wrong
version numbers in releases. This has happened in practice on a
`client-python` pull request.

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

## README links must be absolute

Every link in a project's **top-level `README.md`** must be absolute.
The top-level README is rendered *off* the repository landing page --
as the PyPI long description, on crates.io, and on README mirrors --
and in those contexts a relative link (`docs/x.md`, `../x`, `/x`)
resolves against the wrong base and silently 404s. Acceptable targets
are scheme-qualified URLs (`https://`, `mailto:`, ...),
protocol-relative `//host` URLs, and pure in-page `#anchor` links. For
links to other files in the same repository, use
`https://github.com/<org>/<repo>/blob/<default-branch>/<path>`, which
renders correctly off-site and still resolves on GitHub.

Only the top-level `README.md` is in scope: subdirectory READMEs are
only ever viewed on the GitHub file tree, where relative links work
fine. Links inside fenced code blocks or inline code spans are ignored
(a documented command containing `[x](y)` is sample text, not a
link).

This rule exists because divergulent's first PyPI release rendered
with every relative link broken.

## Links out of docs/ must be absolute

Every relative link in a project's `docs/` tree must resolve to a file
that exists **inside** `docs/`. Anything pointing outside it --
`../README.md`, `../tools/x.sh`, `../../src/app.rs` -- must be an
absolute `https://github.com/<org>/<repo>/blob/<default-branch>/<path>`
URL.

`docs/` is not only rendered on the GitHub file tree. It is
synchronised into `shakenfist/shakenfist` under
`docs/components/<repo>/` and published on shakenfist.com, where the
tree above `docs/` does not exist, so a link that escapes `docs/`
resolves against the wrong base and 404s there while rendering
perfectly on GitHub. Links that stay inside `docs/` should stay
relative: they move with the tree and work in both renderings.

`docs/plans/` is in scope -- plans are published along with the rest
of `docs/`, so a broken link there is broken for a reader whether or
not anyone still maintains the file. Site-root-absolute targets
(`/operator_guide/locks/`) are left alone: they are the mkdocs
convention for another page of the same site.

The automated check also reports relative targets that stay under
`docs/` but resolve to nothing. In practice those are links out of
`docs/` written against the repository root (`src/app.rs` rather than
`../../src/app.rs`) -- the same defect wearing a different spelling,
and dead on GitHub too.

See [audits/docs-external-links.md](audits/docs-external-links.md) for
the full criterion.

## README is a pitch

The top-level `README.md` is a **pitch** aimed at a human landing on
the repository page: what the project is, who it is for, minimal
installation instructions, a small number of usage examples, and
curated links into `docs/`. Feature catalogues, CI workflow tables,
build internals, architecture descriptions, and dependency lists
belong in `docs/` instead -- not in `ARCHITECTURE.md` or `AGENTS.md`,
which are held to their own summary-and-index rule above.

The automated check enforces measurable proxies: `README.md` is at
most 150 lines and 1200 words, and links into `docs/` when a `docs/`
directory exists. The judgment half of the policy is enforced at push
time by the `readme-discipline` shared block in each repository's
`PUSH-AUDIT.md` (see below), which sends new feature documentation to
`docs/` and treats README growth as a finding.

This rule exists because our READMEs accreted a bullet per feature
per push -- ryll's reached 557 lines -- burying the pitch and
duplicating content `docs/` already covers.

## Pre-push audit file and shared blocks

Repositories that carry a pre-push audit runbook must name it
`PUSH-AUDIT.md` (the historical `PUSH-TEMPLATE.md` name is legacy:
the file is a runbook, not a template, and the `-TEMPLATE` suffix is
reserved for true templates like `PLAN-TEMPLATE.md`). Repositories
without a pre-push audit file are exempt.

Canonical wording that must stay identical across repositories --
currently the `readme-discipline`, `llm-doc-discipline` and
`plan-phase-references` instructions in the documentation-review
section of `PUSH-AUDIT.md`, and the `comment-proportion`
instructions in its code-quality section -- is embedded as a
**versioned shared block**:

```markdown
<!-- shared-block: <name> v<N> -->
...canonical wording...
<!-- shared-block-end -->
```

Canonical copies live in `templates/shared-blocks/` in the
development repository. The audit verifies each embedded block is
present where required, carries the current version, and matches the
canonical wording verbatim. To change shared wording: edit the
canonical file, bump its version, and let the daily audit file
issues against every repository still carrying the old version.

`comment-proportion` covers comments and docstrings that are out of
proportion to the code they document -- the multi-paragraph
explanation on a three-line method. There is no honest mechanical
threshold for this (the same docstring is right on a concurrency
contract and wrong on an accessor), so the shared block briefs the
code-quality judgment agent instead: comment blocks longer than the
code they describe are candidates, the finding is advisory, and the
fix is to cut the restatement rather than to delete the comment.
Repositories that want a mechanical prefilter can add a report-only
grep for long runs of added comment lines to their wave-1 sweep and
feed the output to the same agent.

## Plan template and the sub-agent model roster

`PLAN-TEMPLATE.md` is mostly not project-specific. How phase files
are named, that sub-agents do the implementation work, what the
effort levels mean, which models exist and when to reach for each --
all of that was copied between repositories by hand and drifted.
Repositories that carry a `PLAN-TEMPLATE.md` must embed the current
`plan-file-conventions`, `subagent-execution-model`,
`plan-planning-effort`, `subagent-step-guidance`,
`subagent-model-roster`, `plan-review-checklist` and
`plan-closeout-sections` shared blocks, using the same versioned
markers as `PUSH-AUDIT.md`. Repositories without a plan template are
exempt.

The organising rule is that a section of the template is either
wholly shared or wholly project-specific; a generic rule and a local
example are not interleaved within one section. Where a section is
both, the rule is the shared block and the example follows it in an
`!!! note "In this project"` admonition. What stays
project-specific is the `## Prompt` preamble, `### Success
criteria`, the worked examples under planning effort and step-level
guidance, the project's own pre-merge checks, and `### Documentation
index maintenance`.

The admonition is chosen over a repeated heading because master
plans written from the template live in `docs/plans/` and are
published through mkdocs-material, where it renders as a proper
callout -- and a lot of this material does survive into published
plans (85 of shakenfist's 125 plans carry a Back brief, 44 carry
Step-level guidance). A repeated heading would instead mint
duplicate anchors and duplicate table-of-contents entries in each of
them. Only the short runs trailing a shared block are marked;
whole project-specific sections already announce themselves by their
headings, and the `...` placeholders are per-plan rather than
per-project.

`subagent-model-roster` is deliberately a block of its own rather
than part of the step guidance, because it churns on a different
cadence: models ship and retire while the effort ladder sits still.
This is how we manage which models planning sessions may use. To
change the roster fleet-wide -- add a model, retire one, or rewrite
when each should be chosen -- edit
`templates/shared-blocks/subagent-model-roster.md`, bump its
version, and commit. The next daily audit run marks every repository
still carrying the old roster non-compliant and files the issues,
and the issue names the roster rather than the surrounding prose.

## Plan index

Every repository that plans in `docs/plans/` carries an
`index.md` there, and it is the one page that answers what the
repository has planned and what still wants attention. Three
different layouts had grown across the fleet -- a date-first table
of plans, a plan-first table of plans, and a plan-first table of
*phases* -- so anything reading an index had to work out which shape
it was before it could find the status column. Local tooling that
did not silently returned "every plan here is unstarted".

The canonical shape is one table row per plan, led by a `Date`
column and then a `Plan` column, with rows oldest first. Later
columns are the repository's own business; `Intent`, `Status` and
`Phases` are the usual ones, and a table with no `Status` column at
all is fine for standalone plans that are registered but not
tracked. Ordering is per table, so separate master, standalone and
consolidation tables may each start over. Plans must be listed in a
table rather than as prose or a bullet list, and every master plan
file in `docs/plans/` must appear -- a plan the index never links
was drafted and then forgotten. Phase plans are exempt: they are
named after their master plan and tracked inside it.

### The status vocabulary

A status cell holds exactly one of `Proposed`, `Not started`,
`In progress`, `Blocked`, `Complete`, `Abandoned` or `Superseded`,
and nothing else. Matching is case-insensitive; the spelling here is
the one to write.

The "nothing else" is the part that matters, because it is the part
that decayed. Status cells had grown into paragraphs -- `Complete
(phases 1-5 and 2b, 2026-08-15): every merge to develop installs a
freshly built .deb/.rpm on...` -- which is useful writing in the
wrong column. A status is read to decide whether a plan still wants
attention, and when the answer is buried in prose neither a person
scanning the table nor a script can extract it. Dates, phase
arithmetic and accounts of what happened belong in the plan file; a
one-line summary belongs in the `Intent` column.

The vocabulary is the versioned `plan-status-vocabulary` shared
block, required in every `PLAN-TEMPLATE.md`, so plans are written to
it rather than corrected afterwards. It governs the master plan's
own Execution phase table as well as the index row. To change the
vocabulary fleet-wide, edit
`templates/shared-blocks/plan-status-vocabulary.md`, bump its
version, and let the daily audit file the issues.

## Plan references in source still resolve

Every reference to a plan file written into source code or
configuration -- `docs/plans/PLAN-session-001-feedback.md` in a Rust
comment, a plan path in a workflow's rationale comment -- must
resolve in the repository it is written in, or else be an absolute
`https://github.com/<org>/<repo>/blob/<default-branch>/docs/plans/PLAN-foo.md`
URL.

These pointers are the trail from a piece of code to the reasoning
behind it, and they are what a reader follows when they want to
change that code. Unlike a markdown link in `docs/`, nothing renders
them: a path inside a `//` comment or a YAML key is inert text. So
when a plan is renamed, or archived into `docs/plans/completed/`,
the pointer rots silently and stays rotten, and the first person to
notice is someone who went looking for the record and did not find
it. A comment that asserts a record exists is worse than no comment
when the record cannot be reached.

Markdown files are out of scope here -- they are covered by
`docs-external-links`, which sees them as rendered links. A bare
`PLAN-foo.md` with no directory resolves against every plan file at
any depth under `docs/plans/`, so archiving a plan does not break
references that never named a directory. A plan in another
repository has to be an absolute URL, for the same reason a link out
of `docs/` does: a reference read somewhere other than where it
lives cannot be relative.

The remedy is to make the pointer land, not to delete it. If a
reference cannot be made to resolve, replace it with the reasoning
it was standing in for.

See [audits/plan-source-references.md](audits/plan-source-references.md)
for the full criterion.

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

When copying `render-review.py` out of that directory, copy
`review-schema.json` with it and into the same directory. The script
finds its schema next to itself, and a copy without one silently stops
validating: `--validate` accepts a review with an invented category or
action and exits zero, so the repository looks compliant and the gate
in `address-comments-with-claude.sh` passes anything through to Claude
Code. The `ci-review-automation` audit checks for this.

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
- The `pre-commit` manager enabled in `renovate.json`, for any project
  with remote pre-commit hooks.

### The pre-commit manager

Renovate reads cargo, dockerfile, github-actions and the Python
managers by default, but its `pre-commit` manager is opt-in. A project
that never enables it leaves `.pre-commit-config.yaml` outside
renovate's view completely: the hook revisions never reach the
dependency dashboard, and there is no symptom to notice, because the
absence of a bump looks exactly like being up to date.

The consequence is worse than for an ordinary dependency. Pre-commit
hooks are the linters gating every commit, so the one file nobody is
watching is the one deciding whether everything else is acceptable.
`instar` was four months behind on `actionlint` while its cargo,
dockerfile and github-actions dependencies were all current.

Enable it with any of the three forms renovate supports -- a
`"pre-commit": {"enabled": true}` block, `enabledManagers`, or the
`:enablePreCommit` preset. Projects with no `.pre-commit-config.yaml`,
or whose hooks are all `repo: local` and therefore carry no revision
to bump, are not expected to enable it.

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

### Range strategy for client/library projects

Server projects (shakenfist, kerbside) use exact dependency pins
(`==`) and the default renovate range strategy, which bumps those
pins on every new release. This is appropriate because the server
runs on infrastructure we control.

Client and library projects (agent-python, client-python,
client-python-k3s, clingwrap, occystrap) use relaxed dependency
ranges (`>=`) so they work across a wide range of distributions
and Python versions. For these projects, grpc packages should use
`rangeStrategy: "widen"` so that renovate only creates PRs when a
new major version falls outside the existing range (e.g. grpcio
2.x). This avoids unnecessary churn from renovate trying to bump
the floor of a `>=` constraint on every minor release.

The motivation: distributions like Fedora 43 ship Python 3.14,
and older grpcio releases lack pre-built wheels for newer Python
versions. With relaxed `>=` constraints, pip can select whichever
version has wheels for the target platform. With exact `==` pins,
pip falls back to building from source and fails if build tools
(e.g. a C++ compiler) are missing.

The gRPC wire protocol is stable across minor versions, so a
client on grpcio 1.80 communicates with a server on 1.70 without
issues. The protobuf serialization format (proto3) is also stable
within the same major version.

### Package grouping

Projects with tightly coupled dependencies (e.g. the grpc stack)
should group them in `renovate.json` so they are bumped together.

For server projects, use the default range strategy:

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

For client/library projects, add `rangeStrategy: "widen"`:

```json
{
  "packageRules": [
    {
      "description": "Group grpc packages together with widen strategy",
      "matchPackagePatterns": [
        "^grpcio",
        "^googleapis-common-protos",
        "^protobuf"
      ],
      "groupName": "grpc packages",
      "rangeStrategy": "widen"
    }
  ]
}
```

## Pinning indirect dependencies

Renovate handles bumping direct dependencies, but transitive (indirect)
dependencies can silently change version when a direct dependency is
updated. If an indirect dependency releases a broken version it can be
very confusing to debug. To catch this, applications should have a
`pin-indirect-dependencies.yml` workflow that runs daily and
reconciles a pinned block in `pyproject.toml` against what the direct
dependencies actually require -- adding pins for new transitive
requirements and removing pins nothing requires any more.

### This applies to applications, not libraries

Only projects which already exactly pin their own direct dependencies
are in scope, and that test is how the audit tells the two apart: at
least half of the `[project] dependencies` entries must carry a `==`.
Currently that is shakenfist and kerbside.

Pinning a transitive dependency decides, on a consumer's behalf, which
version of a package they get. In an application whose runtime
environment we control that is the point. In a library it is an
imposition: a distribution packager building against the versions
their archive already ships should not have to fight our idea of the
dependency graph. Our libraries constrain loosely (`>=`) on purpose,
and the audit reports them as not applicable.

A library variant which kept the block in a `pinned` optional extra
was tried and withdrawn. The base install stayed unconstrained, but
the pins still shipped in the published metadata and Renovate's pep621
manager tracks `optional-dependencies`, so every recorded version
became another stream of bump pull requests.

### Requirements

1. `pyproject.toml` must contain `# START_OF_INDIRECT_DEPS` and
   `# END_OF_INDIRECT_DEPS` markers delimiting the block, inside the
   `[project] dependencies` list. The script refuses to run without
   exactly one of each.
2. `.github/workflows/pin-indirect-dependencies.yml` must exist.
3. `tools/pin-indirect-dependencies.sh` must exist, copied unchanged
   from the template.
4. A repository secret called `DEPENDENCIES_TOKEN` with push and PR
   permissions. Without it the job runs and prints its diff but never
   opens a PR, which looks exactly like having nothing to do.

```toml
[project]
dependencies = [
    "flask==2.2.5",
    # never-pin: pydantic-core
    # Indirect dependencies, regenerated by the workflow.
    # START_OF_INDIRECT_DEPS
    "markupsafe==3.0.2",
    # END_OF_INDIRECT_DEPS
]
```

**Template:** Use
[`templates/pin-indirect-dependencies/`](templates/pin-indirect-dependencies/)
as the canonical starting point. Replace `{{PROJECT_NAME}}` with the
repository name. Add any project-specific system-level build
dependencies (e.g. MySQL dev headers for kerbside) to the placeholder
step -- the reconcile runs in an isolated venv, so a package can no
longer fall back to a distro build of itself.

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
| Allow auto-merge | Enabled | Useful with required checks |

(Delete branch on merge used to be listed here as recommended; it is
now a required setting with its own audit -- see "Delete branch on
merge" below.)

### Current security state (2026-02-08)

| Repository | Dependabot | Secret Scanning | Notes |
|------------|------------|-----------------|-------|
| shakenfist | Enabled | Disabled | Enable secret scanning |
| occystrap | Disabled | Disabled | Enable both |
| instar (then imago, private) | N/A | N/A | Now public; enable secret scanning |
| kerbside | Disabled | Disabled | Enable both |
| client-python | Enabled | Disabled | Enable secret scanning |
| agent-python | Disabled | Disabled | Enable both |

## Delete branch on merge

**Standard:** All active repositories should have "Automatically
delete head branches" enabled (Settings > General > Pull Requests),
so that a pull request's source branch is deleted automatically when
the PR merges. This keeps repositories free of stale merged branches.

To enable via the CLI:

```bash
gh api -X PATCH repos/shakenfist/<repo> -F delete_branch_on_merge=true
```

## Merge queue reasonability

**Standard:** Repositories that enable a GitHub merge queue on
their default branch must configure it to process entries serially
and merge them individually: `max_entries_to_build: 1` and
`min_entries_to_merge: 1`. Repositories without a merge queue are
out of scope — adopting two-stage CI is a per-project decision.

The rationale (learned on shakenfist/shakenfist, August 2026): with
build concurrency above 1, speculative stacked merge groups are
ejected and rebuilt whenever an entry ahead of them fails, wasting
CI runs and adding cluster load — and load is our dominant merge CI
failure mode, so stacking amplifies the failures that trigger the
rebuilds. Merge batching (`min_entries_to_merge` above 1) idles the
queue for up to the configured wait time while saving no CI, since
the queue runs CI once per entry regardless of how merges land.
See [`audits/merge-queue-config.md`](audits/merge-queue-config.md)
for the full mechanics and the CLI recipe to inspect and fix a
ruleset.

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
should **not** include a CodeQL workflow. The audit checks repository
visibility live via the GitHub API rather than a hardcoded list, so repos
that change visibility are picked up automatically.

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

## HTTP response header sanitization

Projects that use `http.server.BaseHTTPRequestHandler` directly (rather
than a framework like Flask that provides built-in sanitization) must
override `send_header()` to strip `\r` and `\n` characters from header
values. This prevents HTTP response splitting (CWE-113), which CodeQL
flags as `py/http-response-splitting`.

The canonical implementation is `SafeHeaderMixin` in `occystrap/util.py`,
which overrides `send_header()` to call
`str(value).replace('\r', '').replace('\n', '')` before delegating to the
base class. All `BaseHTTPRequestHandler` subclasses must inherit from this
mixin (listed **first** in the class bases for correct MRO).

Projects using Flask (kerbside, shakenfist, agent-python) are already
protected by Werkzeug's `Headers` class, which raises `ValueError` on
header values containing line breaks. When adding new HTTP servers to any
project, prefer Flask. If `http.server` must be used (e.g. for embedded
registries without external dependencies), always use the
`SafeHeaderMixin` pattern.

## File path sanitization

Projects that construct file paths from user-controlled data (image names,
tags, digests, layer paths) must validate that the resulting path stays
within the intended base directory. This prevents path traversal attacks
(CWE-22), which CodeQL flags as `py/path-injection`.

The canonical implementation is `safe_path_join()` in `occystrap/util.py`,
which uses `os.path.realpath()` to resolve the joined path and then
verifies it starts with the base directory. Raises `PathEscapeError` if
the path would escape. Use this instead of bare `os.path.join()` whenever
any component comes from external input.

Projects using web frameworks that serve static files should use the
framework's built-in safe-path utilities (e.g. Flask's `send_from_directory`).
When constructing paths manually from user input in any project, always
validate the result stays within the intended directory.

## Credential handling and leak detection

Every project with CI should run a repository secret scanner on pull
requests and on pushes to the default branch. `gitleaks` is what we use;
`trufflehog` and `detect-secrets` are fine equivalents. This is not the
same thing as the GitHub-hosted secret scanning above: that one knows
about third-party credential formats and wants GitHub Advanced Security
before it will learn a custom pattern, while gitleaks runs locally,
costs nothing, and can be taught our own formats. `ryll`'s
`.github/workflows/ci.yml` is the working example, and it
encodes two things worth not rediscovering: `gitleaks-action@v2`
refuses to run on organization repos without a paid licence, so we
invoke the binary directly, and gitleaks is only packaged from Debian
13 onward.

Scope the scan with `--log-opts="HEAD"`. The default is every ref,
which on any project that publishes a site from a branch means
scanning the built copy of its own documentation -- on Shaken Fist
that was five minutes and 163 findings instead of three seconds and
13, and gitleaks 8.16 attributed the extra findings to unrelated
merge commits, so they could not even be triaged by commit. `HEAD`
still reaches all of the default branch, so nothing is given up.
Pair it with a positive control, and never let the job skip for
docs-only changes: the only leaked key secret in Shaken Fist's
history was published in the user guide. See
[audits/secret-handling.md](audits/secret-handling.md) for the
invocation and for how to accept a finding that cannot be removed.

Separately, and not something a scanner can check for us: credentials
must not be written into logs, audit events, exception messages or
metrics labels. Those all have a wider audience than the credential
does, and they usually leave the machine -- Shaken Fist events go to
syslog *and* Loki, so a token in an event is a token in log
aggregation. That covers bearer tokens (including ones we just minted
and ones we were handed), passwords, stored hashes, and revocation
handles like a token nonce. Log the key name or the account instead,
which is the part that makes an audit trail useful.

The one people miss is generic request tracing that logs whole HTTP
request and response bodies. It gets added for debugging long before
anyone writes a route that carries a credential, and then quietly
records plaintext keys forever. Redact those by route rather than by
field name: a field called `key` is a metadata key name on most
endpoints and a secret on a few, so field matching has to know the
route anyway, and starts leaking the day somebody adds a route it has
not heard of.

Where the language offers a wrapper type that renders as asterisks
instead of its contents, secret fields should use it, so that not
logging the thing is a property of the type rather than something
everyone has to remember. In Python that is `pydantic.SecretStr`; in
Rust it is the `secrecy` crate or a hand-written `Debug`
implementation, because deriving `Debug` on a struct with a secret
field is how this bug is spelled there.

Finally, where we generate a credential rather than accepting one the
user chose, the generated form should carry a short prefix and a
checksum, the way `ghp_`, `glpat-`, `sk_live_` and `xoxb-` do. The
prefix makes it greppable in logs and repositories; the checksum lets
a scanner reject lookalikes without calling an API, which is what
makes scanning at volume tolerable instead of alert spam. This costs
nothing cryptographically -- a bearer token is a random identifier
rather than ciphertext, so a fixed prefix sits beside the random part
without revealing any of it.

See [audits/secret-handling.md](audits/secret-handling.md) for the full
criterion. Only the scanner half is checked automatically; the rest are
review criteria, so a passing check means a scanner is running, not
that a project keeps credentials out of its logs.

## GitHub Action

### Workflow permissions

All GitHub Actions workflows must have a top-level `permissions` block
to restrict the default `GITHUB_TOKEN` scope. This is flagged by GitHub
Advanced Security if missing. Best practice is to set the most
restrictive top-level permissions needed (often `contents: read`), and
override at the job level where individual jobs need more (e.g.
`security-events: write` for CodeQL, `id-token: write` for releases).
Workflows where every job only reads should use
`permissions: contents: read`. Workflows with mixed needs should use
`permissions: {}` at the top level and declare per-job permissions.

### Self-hosted runners

All workflow jobs must run on `self-hosted` runners except under
exceptional circumstances. The amount of time we get on GitHub-provided
runners each month is limited, so jobs that "leak" onto GitHub-provided
runners consume that allowance for no benefit.

The main legitimate exception is builds that need hardware we don't
own -- for example the ryll Windows and macOS builds. Each exception
must be marked with an `audit-ok: github-hosted-runner` comment on the
offending line (or the line immediately above it), ideally with a
reason:

```yaml
    # audit-ok: github-hosted-runner -- no self-hosted macOS hardware
    runs-on: macos-latest
```

The audit flags any workflow line referencing a GitHub-hosted runner
label (`ubuntu-latest`, `windows-2022`, `macos-15`, and so on) that is
not marked as an exception. This includes matrix values that feed
`runs-on: ${{ matrix.os }}`.

### Path filtering for expensive lanes

Ephemeral VM runners (the `vm` label) are the expensive pool: the
lanes that run on them build entire clouds or boot guests. A pull
request or merge queue entry that touches only content no lane
exercises -- the `docs/` directory and the review-tracking state
(`REVIEWS.md`, the `.vscode` weaudit files) -- should not pay for
them. Every workflow that runs `vm`-runner jobs on `pull_request`
or `merge_group` must be path-filtered, and the filter must exclude
`docs/**` where the repository has a `docs/` directory and
`REVIEWS.md` where review tracking is deployed.

The mechanism depends on whether the workflow backs a required
status check. A workflow backing no required check may use
trigger-level `paths:` / `paths-ignore:`; an inclusion-style
`paths:` list excludes everything unlisted by construction. A
workflow backing a required check must use a filter job instead
(`dorny/paths-filter` feeding job-level `if:` conditions, as
kerbside's `check_paths` jobs do), because a required check in a
`paths-ignore`'d workflow never reports on a filtered PR, and a
required check that never reports blocks the merge forever, while
a skipped one satisfies it. Mind dorny/paths-filter's
`predicate-quantifier: 'every'` trap: with the default ANY-match
semantics a `'**'` pattern silently defeats every exclusion.

Dedicated content-scanner workflows (gitleaks, trufflehog,
detect-secrets) are exempt: they exist to read exactly the
human-written text a filter would skip. A workflow mixing scanner
jobs with expensive lanes still needs its filter -- the scanner
jobs just should not consume the filter's output. Other
deliberate exceptions -- a lane that must
run even for docs-only changes -- are marked with an
`audit-ok: no-path-filter` comment in the workflow file, ideally
with a reason. See
[audits/expensive-lane-path-filter.md](audits/expensive-lane-path-filter.md)
for the full criterion.

### Linting for CI jobs

Please ensure we have `actionslint`, `shellcheck`, and a git precommit
that runs them setup as well. You can find examples in `kerbside`, and
`kerbside-patches`.

Additionally, we have some rules of our own:

* Workflow and job display names should always be English sentences with
  correct capitalization. No kebab case! The **id** of the job can be
  something more machine friendly, but we talk English to the humans in
  the GitHub user interface please.
* We use `self-hosted` runners except under exceptional circumstances
  (see [Self-hosted runners](#self-hosted-runners) above).
* Claude code automation jobs can only run on `claude` runners, as the
  others are not authenticated with Anthropic.
* Small jobs which do not change the state of the runner should run on
  a `self-hosted` `static` runner -- the startup cost of the ephemeral
  runners does not make them a good choice for small jobs like linting.
* A job that requests a `static` runner must ask for the `self-hosted`
  and `static` labels **only** -- `runs-on: [self-hosted, static]`.
  The static pool advertises just those two labels, so combining
  `static` with an impossible extra label such as a size (`s`, `l`),
  `vm`, or an operating system (`debian-12`) requests a runner that
  does not exist and the job will never be scheduled. The audit flags
  any `runs-on:` that pairs `static` with additional labels.
* Functional testing is always in a GitHub actions workflow called
  "functional-test.yml". This matters because some of the developer
  automations need to know the name of the workflow to function.
* The functional test workflow **must** include `workflow_dispatch` as
  a trigger. Without it, the `pr-retest.yml` bot automation cannot
  re-run functional tests via `gh workflow run`.

### Piped commands in GitHub Actions must check PIPESTATUS

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

### Linting for helper bash scripts

For projects that have helper shell scripts, we should include a shellcheck
precommit to ensure they're not bonkers.

### flake8wrap.sh correctness

Several projects have a `tools/flake8wrap.sh` script that runs flake8 on
only the files changed in the current commit (invoked via `tox -eflake8
-- -HEAD`). The script builds a space-separated list of filenames in
`filtered_files` and passes it to `diff` and `flake8`.

**The `${filtered_files}` variable must NOT be quoted** on the `diff` /
`$FLAKE_COMMAND` invocation line. Quoting it (i.e. `"${filtered_files}"`)
causes the entire space-separated list to be treated as a single filename
argument, which breaks when more than one Python file is changed.

Correct:

```sh
# shellcheck disable=SC2086
diff -u --from-file /dev/null ${filtered_files} | $FLAKE_COMMAND ${filtered_files}
```

Incorrect:

```sh
diff -u --from-file /dev/null "${filtered_files}" | $FLAKE_COMMAND "${filtered_files}"
```

The `shellcheck disable=SC2086` directive is required because shellcheck
will otherwise flag the intentional word splitting. Add a comment
explaining that the word splitting is deliberate.

Projects with this script should also filter to `.py` files only, skip
`_pb2` generated files, and handle deleted files (files in the diff that
no longer exist on disk).

### Developer automation

Projects should include bot-triggered workflows that respond to
`@shakenfist-bot` comments from authorised users:

* `pr-address-comments.yml` -- "@shakenfist-bot please address
  comments" triggers Claude Code to address review comments from
  the automated reviewer.
* `pr-re-review.yml` -- "@shakenfist-bot please re-review" triggers
  a second automated review (normally limited to one per PR).
* `pr-retest.yml` -- "@shakenfist-bot please retest" re-runs the
  functional tests. Needed because automated commits (e.g. from
  the comment fixer) do not trigger new CI runs.

These workflows use two shared composite actions from the `actions`
repository:

* **`shakenfist/actions/pr-bot-trigger@main`** -- handles the
  common boilerplate: trigger phrase matching, permission checks,
  comment reactions, starting messages, and PR detail extraction.
* **`shakenfist/actions/review-pr-with-claude@main`** -- runs the
  automated review with structured JSON output, issue creation,
  and markdown rendering (used by `pr-re-review.yml`).

**Templates:** Use the templates in
[`templates/ci-review-automation/`](templates/ci-review-automation/)
as the canonical starting point. These contain `pr-re-review.yml`,
`pr-address-comments.yml`, and `pr-retest.yml`.

Additionally, the automated reviewer's prompt should ensure that it
checks that documentation in the `docs/` directory has been updated
for any user-visible changes.

### pypi caching for self-hosted runners

Self hosted runners should use the devpi pypi cache at http://192.168.1.15:3141
in order to reduce network load and increase reliability.

Any job that sets `PIP_INDEX_URL` to the devpi cache must also set
`PIP_EXTRA_INDEX_URL: https://pypi.org/simple/` in the same `env` block.
devpi's `root/pypi` mirror returns an empty index the first time it is asked
for a package it has not cached, so without a pypi fallback pip fails that
cold-cache miss with "from versions: none" (as happened for `bindep` and
`uv` on the kerbside CI). The automated check flags any devpi-backed `env`
block that is missing the fallback.

The devpi cache used to live at `192.168.1.4` but moved to `192.168.1.15`
some time ago; the old address no longer exists, so a job still pointing pip
at `192.168.1.4` fails every install. The automated check flags any workflow
referencing the retired `192.168.1.4` address so it can be updated to
`192.168.1.15`.

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

## Rust unwrap linting

Rust projects must enable clippy's `unwrap_used` lint so that
`.unwrap()` calls in production code are flagged, while test code is
exempted. `unwrap()` converts a recoverable error into a panic, and a
panic on data from outside the process (network input, configuration,
files, other systems) is an outage waiting to happen. This is the
failure mode behind the November 2025 Cloudflare outage, where an
`unwrap()` on a feature file another system had generated too large
panicked their core proxy fleet-wide.

The root `Cargo.toml` should carry the lint (workspace members inherit
it via `[lints] workspace = true`):

```toml
[workspace.lints.clippy]
unwrap_used = "warn"
```

And a `clippy.toml` at the repository root should exempt test code,
where a panic is a test failure and unwrap is fine:

```toml
allow-unwrap-in-tests = true
```

`warn` here plus `-D warnings` in the CI clippy run is the preferred
arrangement; `deny` directly in `Cargo.toml` is also acceptable. We do
not lint `expect_used` -- replacing a provably-infallible `unwrap()`
with `expect("why this cannot fail")` is the sanctioned fix. See
[audits/rust-unwrap-lint.md](audits/rust-unwrap-lint.md) for the full
criterion, including the fuzz harness exemption and guidance on mutex
poisoning panics.

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

## Human review coverage

Repositories that have adopted the whole-file human review tracking
system (see [docs/code-review-tracking.md](docs/code-review-tracking.md),
detected by the presence of `.vscode/review-scope.toml`; currently ryll
and kerbside) should keep the review backlog small: fewer than 5 in-scope files
needing review, where a file needs review if it has never been reviewed
or has changed since its last review. The backlog is recomputed against
HEAD by `scripts/review-tracking.py status` rather than trusted from the
committed `REVIEWS.md`, so a missed prune cannot inflate coverage. See
[audits/review-coverage.md](audits/review-coverage.md) for the full
criterion.

## Pride in our work

Finally, we should be proud of our shared work. A regular holistic review
of each project should ask what we could improve or tighten up with a
refactor. We should not be scared of large refactors if they deliver
large benefits, but we should also avoid change solely for change's
sake.