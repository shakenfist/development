# Renovate Templates

These templates set up Renovate for automated dependency updates
in Shaken Fist projects.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `renovate.yml` | `.github/workflows/renovate.yml` | Hourly workflow |
| `renovate.json` | `renovate.json` (repo root) | Extends the fleet preset |

## This template is being retired

The fleet is moving to a single workflow that runs Renovate over every
repository from one place, which makes the per-repository copy of
`renovate.yml` that this template produces redundant. That workflow,
what it decides and what it means for these copies are described in the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md#the-central-runner),
and deliberately not repeated here. The per-repository copies are
still present and still running, which is why the template and the
rest of this page remain.

## Copying these files

`renovate.yml` has no placeholders and is copied verbatim: its
autodiscover filter is `${{ github.repository }}`, which the runtime
supplies, so nothing changes per repository. The audit holds every
copy to the template apart from the versions Renovate bumps in each
repository: the value of each `PINNED_*_VERSION` variable (Renovate's
and hashin's) and the ref of each action it uses.

`renovate.json` is just `$schema` and
`"extends": ["local>shakenfist/development:renovate-fleet"]`. The
fleet policy lives once, in that preset, `renovate-fleet.json` at the
root of shakenfist/development, and a repository adds only what is
particular to it: project-specific package grouping rules, a Python
version constraint, or a range strategy, as the sections below
describe. That makes it not byte-identical between repositories once
customised, and the audit does not expect it to be. Whatever else it
carries, it must keep the `extends`: the preset is what enables the
`pre-commit` manager and extends `customManagers:githubActionsVersions`,
which lets Renovate see its own version pin (below).

### The fleet preset

The preset carries the whole fleet policy, so a repository that
extends it needs nothing of its own: the author and assignee
Renovate uses, the dependency dashboard, rollback pull requests,
`minimumReleaseAge`, the `pre-commit` manager,
`customManagers:githubActionsVersions`, the timezone and the cadence,
the rule that merges nothing automatically, the two rules that keep
`node-version` and the pinned Renovate in step, and the
`shellcheck-py/shellcheck-py` versioning rule. What each of those is
set to is listed once, in the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md),
and deliberately not repeated here: a policy written out twice drifts,
and the copy that drifts unnoticed is the one in a template.

One part of it is worth stating twice, because it is the one place
the cadence can cost a repository a security fix. The preset sets no
`vulnerabilityAlerts`, so Renovate's defaults apply: a fix for a
vulnerability Dependabot reports ignores the schedule,
`minimumReleaseAge` and grouping, and arrives at once rather than
waiting for Saturday. One rule is an exception. A `packageRules` entry
with `enabled: false` suppresses even a vulnerability fix, and the
preset's Node-major disable rule is one, so Node majors in the
Renovate workflows are the one update type the immediate security
path does not reach.

Renovate reads the preset from the default branch of
shakenfist/development, so a change to it cannot be tried from a pull
request branch: it takes effect for the whole fleet the moment it
merges. The only check before that is pre-commit's
`renovate-config-validator`, which that repository's
`.pre-commit-config.yaml` aims at the preset by name and runs with
`--no-global`. See the
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md)
for why.

## Renovate runs from npm, not Docker

`renovate.yml` does not use `renovatebot/github-action`. That action
runs Renovate as the `ghcr.io/renovatebot/renovate` container, so it
needs a Docker daemon on the runner, and the static pool is gaining
ephemeral runner pods on a Kubernetes cluster that have none. Instead
the workflow installs Node with `actions/setup-node`, installs a
pinned Renovate with `npm`, and runs it. That works the same on a VM
and on a pod.

The environment is what the action passed into its container:
`RENOVATE_TOKEN` (the action's `token:` input set exactly that),
`RENOVATE_AUTODISCOVER` and `RENOVATE_AUTODISCOVER_FILTER`.
`RENOVATE_BASE_DIR` is new. The container kept its clone and cache in
`/tmp/renovate`, bind-mounted from the runner's `/tmp` and owned by
the container's user. A per-job directory under `runner.temp` gives
every run a clean clone and cache, on a long-lived VM as much as on a
pod. The cost is that Renovate's lookup cache no longer survives
between runs on a VM, so every run repeats its datasource lookups
against GitHub and the registries. If that ever shows up as rate
limiting, an `actions/cache` of the cache directory would bring
persistence back on both kinds of runner. The action's `FORCE_COLOR=3`,
which only coloured the log, is dropped.

### Lock files

The container installed tools such as `cargo` on demand to refresh
lock files (Renovate's `binarySource=install`). Outside the container
Renovate uses whatever is on the runner's `PATH`
(`binarySource=global`), so the workflow has to provide each tool a
lock file needs, or Renovate's pull requests carry an "artifact update
problem" instead of a refreshed lock file. For the lock files the
fleet has:

| Lock file | Tool Renovate runs | Provided by |
|-----------|--------------------|-------------|
| `package-lock.json` | `npm` | `actions/setup-node`, always |
| `Cargo.lock` | `cargo update` | `dtolnay/rust-toolchain@stable`, only when the repository has a `Cargo.lock` |
| `requirements*.txt` | `hashin`, for a file with `--hash` lines | A venv under `runner.temp`, only when the repository has a `requirements*.txt` |

The Rust step installs stable with rustup's minimal profile (`rustc`,
`cargo` and the standard library, no clippy, rustfmt or docs) and
makes it rustup's default. It sets no override, so a repository with a
`rust-toolchain.toml` still gets the toolchain it names: rustup reads
that file from the directory Renovate runs `cargo` in, and installs
the toolchain on first use.

A requirements file without `--hash` lines needs no tool: Renovate
edits the pins as text. One with them needs `hashin`, which Renovate
runs to replace the old version's hashes with every hash PyPI
publishes for the new one. andris's `.devcontainer/wheel/requirements.txt`
was the first such file in the fleet, and its first bump
(shakenfist/andris#69) carried the new pin with the old hashes and an
artifact update problem whose stderr was empty: Renovate runs its
tools without a shell, so a missing `hashin` fails to spawn rather
than printing "not found". `hashFiles()` cannot tell whether a file
has hashes, so the step runs for any `requirements*.txt`; it costs a
venv and a few seconds. Only the `hashin` script is put on `PATH`,
through a directory of its own, so the venv's `python3` and `pip` do
not shadow the runner's for the other tools Renovate runs.

`hashin` is pinned with a `# renovate:` comment like Renovate itself,
so it is bumped the same way. The install step holds no secret, and
Renovate passes its tools only an allowlist of environment variables
(`PATH`, `HOME`, proxy and certificate settings), not
`RENOVATE_TOKEN`, so `hashin` and its dependencies never see the
token. Those dependencies (`packaging` and `pip`) are resolved within
their declared ranges on each run, as Renovate's npm tree is.

A repository that adds another kind of lock file -- `uv.lock`,
`poetry.lock`, `go.sum`, `pnpm-lock.yaml` and so on -- needs a step of
the same shape here, conditional on `hashFiles()` so the template
stays one file for every repository.

### The version pin

The Renovate version is pinned exactly, in an environment variable
with a comment Renovate reads:

```yaml
# renovate: datasource=npm depName=renovate
PINNED_RENOVATE_VERSION: 44.155.1
```

`customManagers:githubActionsVersions` is a preset built into
Renovate. It matches that comment above any `*_VERSION` key in
`.github/workflows`, so Renovate bumps its own pin like any other
dependency. Without the preset nothing reads the comment and the pin
ages silently, which is why the audit checks for it. The variable is
not named `RENOVATE_*`, because Renovate reads those as its own
configuration.

The action floated on the image's `44` tag, so it picked up every new
release unasked. An exact pin trades that for a pull request. It pins
Renovate itself, not its dependency tree: with no lockfile, `npm`
resolves Renovate's dependencies within their declared ranges on each
fresh install. The container image was a fixed tree per tag, although
the floating tag moved it anyway.

### Installing without the token

`RENOVATE_TOKEN` can write to every repository Renovate manages, so
the workflow keeps it out of the install. The "Install Renovate" step
holds no secret: it runs `npm install --ignore-scripts` into a
directory under `runner.temp`, so no package in the freshly resolved
tree runs an install script at all, and then `npm rebuild re2`. `re2`
is the one native dependency Renovate uses. It is Renovate's RE2
regular expression engine, and without its build Renovate falls back
to `RegExp` and warns on every run. The rebuild runs only that
package's scripts, still without the token. The "Self-hosted
Renovate" step holds the token and runs the installed binary, so it
fetches nothing from the registry.

This was `npx --yes renovate@<pin>` in one step that held the token
(shakenfist/development#322). An install script anywhere in the tree,
from a dependency Renovate pulls in within its declared ranges, would
have run with write access to the fleet on the next hourly run.

The split closes install time, not run time. Code from the tree that
Renovate imports still runs in the step that holds the token, so a
compromised dependency release can still reach it that way until the
tree is fixed by a lockfile. `minimumReleaseAge` does not help here:
it governs the pull requests Renovate opens, not what `npm` installs.

`node-version` follows Renovate's `engines.node` (`^24.11.0` for
44.x), and the fleet preset keeps the two in step with two
`packageRules` entries.

- **Renovate majors carry a note.** A Renovate major that raises
  `engines.node` needs `node-version` raised in the same pull request,
  but the bump itself only touches the pin, so the rule's
  `prBodyNotes` puts that check in front of the reviewer.
- **Node majors for this workflow are never proposed.** Renovate's
  github-actions manager proposes Node majors for `setup-node`, and
  one taken before the pinned Renovate's `engines.node` accepts it
  fails every hourly run with "Unsupported node environment
  detected". In October 2026 a Node 26 bump did that to nine
  repositories at once. The rule disables Node majors in
  `.github/workflows/renovate.yml` alone; Node in a repository's other
  workflows is still proposed as usual.

The checkout sets `persist-credentials: false`. Only `hashFiles()`
reads it, and Renovate clones the repository itself with
`RENOVATE_TOKEN`, so nothing in the job needs the job token left in
`.git/config`.

## The pre-commit manager

The fleet preset enables renovate's `pre-commit` manager. That manager
ships disabled, so a repository whose config does not extend the
preset, or enable the manager itself, keeps
`.pre-commit-config.yaml` outside renovate's view entirely -- the hook
revisions never appear on the dependency dashboard, and nothing about
the repository looks wrong while they age.

This is worth being deliberate about because of what those hooks are.
They are the linters that gate every commit, so the file nobody is
watching is the one deciding whether everything else is acceptable.
It was found in `instar`, where `actionlint` sat four months behind
while cargo, dockerfile and github-actions dependencies stayed current.

Any of the three supported forms satisfies the audit, as does
extending the fleet preset, which enables the manager:

```json
{"pre-commit": {"enabled": true}}
{"enabledManagers": ["pre-commit", "..."]}
{"extends": [":enablePreCommit"]}
```

Repositories with no `.pre-commit-config.yaml`, or whose config only
declares `repo: local` hooks, have nothing to bump and are not checked.

### shellcheck-py's four-part tags

Enabling the manager is not enough for the shellcheck hook. Upstream
tags a fourth, packaging, component (`v0.11.0.1`), which renovate's
default versioning cannot parse, so the lookup fails with "Can't find
version matching v0.11.0.1" on the dependency dashboard and the hook is
never bumped. That is the same silent ageing the manager was enabled to
prevent, with a warning only the dashboard shows. The fleet preset's
`shellcheck-py/shellcheck-py` package rule sets a regex versioning
that reads all four components, so a repository that extends the
preset needs nothing of its own; the rule is inert in one that does
not use the hook. `kerbside` found this first, and its
`renovate.json` additionally groups the hook
with the `tox.ini` pin of the same tool.

## Customisation

Everything below is something a repository adds to its `renovate.json`
beside the `extends` of the fleet preset, not a replacement for it.

### Python version constraints

Projects that support multiple Linux distributions should add a
`constraints.python` field to `renovate.json` matching the oldest
supported Python version. This prevents renovate from proposing
dependency updates incompatible with older distros:

```json
{
  "constraints": {
    "python": ">=3.8"
  }
}
```

Document the supported platforms in `ARCHITECTURE.md` and add
comments in `pyproject.toml` pointing back to that table. See
`agent-python` for a complete example.

### Package grouping

Add `packageRules` entries to group tightly coupled dependencies
so they are bumped together. Common examples:

```json
{
  "packageRules": [
    {
      "description": "Group grpc packages together",
      "matchPackagePatterns": ["^grpcio", "^protobuf"],
      "groupName": "grpc packages"
    }
  ]
}
```

See `shakenfist/renovate.json` for a more complex example with
multiple groups (pydantic, grpc, zope, etc).

### Range strategy for client/library projects

Client and library projects use relaxed dependency ranges (`>=`)
rather than exact pins so they work across many distributions and
Python versions. For these projects, grpc packages should use
`rangeStrategy: "widen"` so renovate only creates PRs when a new
major version falls outside the existing range:

```json
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
```

Server projects (shakenfist, kerbside) use exact pins and the
default range strategy. The
[audit spec](https://github.com/shakenfist/development/blob/main/docs/audits/renovate.md)
has the full rationale.

## Prerequisites

- A `RENOVATE_TOKEN` secret with repository write access
- Self-hosted runners with the `static` label. They need outbound
  access to GitHub (`setup-node` downloads Node from its
  `actions/node-versions` releases) and to the npm registry, but no
  Docker daemon.

## Projects using these templates

| Project | Status | Range strategy |
|---------|--------|----------------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Live | default (pin) |
| [occystrap](https://github.com/shakenfist/occystrap) | Live | widen |
| [imago](https://github.com/shakenfist/imago) | Live | default (pin) |
| [agent-python](https://github.com/shakenfist/agent-python) | Live | widen |
| [client-python](https://github.com/shakenfist/client-python) | Live | widen |
| [client-python-k3s](https://github.com/shakenfist/client-python-k3s) | Live | widen |
| [clingwrap](https://github.com/shakenfist/clingwrap) | Live | widen |
