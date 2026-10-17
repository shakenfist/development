# Renovate Templates

These templates set up Renovate for automated dependency updates
in Shaken Fist projects.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `renovate.yml` | `.github/workflows/renovate.yml` | Hourly workflow |
| `renovate.json` | `renovate.json` (repo root) | Renovate config |

## Copying these files

`renovate.yml` has no placeholders and is copied verbatim: its
autodiscover filter is `${{ github.repository }}`, which the runtime
supplies, so nothing changes per repository.

`renovate.json` is a per-repository adaptation, not expected to be
byte-identical between repositories. It is copied as a starting point
and then customised with project-specific package grouping rules, a
Python version constraint, or a range strategy, as the sections below
describe. Whatever else it carries, it must keep
`"extends": ["customManagers:githubActionsVersions"]`, which is what
lets Renovate see its own version pin (below).

## Renovate runs from npm, not Docker

`renovate.yml` does not use `renovatebot/github-action`. That action
runs Renovate as the `ghcr.io/renovatebot/renovate` container, so it
needs a Docker daemon on the runner, and the static pool is gaining
ephemeral runner pods on a Kubernetes cluster that have none. Instead
the workflow installs Node with `actions/setup-node` and runs a pinned
Renovate with `npx`. That works the same on a VM and on a pod.

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
| `requirements*.txt` | nothing | Renovate edits the pins as text. Only a file with `--hash` lines needs a tool (`hashin`), and the fleet has none |

The Rust step installs stable with rustup's minimal profile (`rustc`,
`cargo` and the standard library, no clippy, rustfmt or docs) and
makes it rustup's default. It sets no override, so a repository with a
`rust-toolchain.toml` still gets the toolchain it names: rustup reads
that file from the directory Renovate runs `cargo` in, and installs
the toolchain on first use.

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
Renovate itself, not its dependency tree: with no lockfile, `npx`
resolves Renovate's dependencies within their declared ranges on each
fresh install, and their install scripts run in the step that holds
`RENOVATE_TOKEN`. The container image was a fixed tree per tag,
although the floating tag moved it anyway.

`node-version` follows Renovate's `engines.node` (`^24.11.0` for
44.x). Renovate's github-actions manager also proposes Node majors for
`setup-node`. Do not take one until the pinned Renovate's
`engines.node` accepts it. A Renovate major that raises
`engines.node` needs `node-version` raised in the same pull request.
`renovate.json` carries a `packageRules` entry for Renovate majors
whose `prBodyNotes` puts that check in front of the reviewer, since
the bump itself only touches the pin. Keep it when adapting the file.

The checkout sets `persist-credentials: false`. Only `hashFiles()`
reads it, and Renovate clones the repository itself with
`RENOVATE_TOKEN`, so nothing in the job needs the job token left in
`.git/config`.

## The pre-commit manager

`renovate.json` enables renovate's `pre-commit` manager. That manager
ships disabled, so a repository that copies an older config keeps
`.pre-commit-config.yaml` outside renovate's view entirely -- the hook
revisions never appear on the dependency dashboard, and nothing about
the repository looks wrong while they age.

This is worth being deliberate about because of what those hooks are.
They are the linters that gate every commit, so the file nobody is
watching is the one deciding whether everything else is acceptable.
It was found in `instar`, where `actionlint` sat four months behind
while cargo, dockerfile and github-actions dependencies stayed current.

Any of the three supported forms satisfies the audit:

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
prevent, with a warning only the dashboard shows. The template's
`shellcheck-py/shellcheck-py` package rule sets a regex versioning
that reads all four components. Keep it when adapting the template:
it is inert in a repository that does not use the hook. `kerbside`
found this first, and its `renovate.json` additionally groups the hook
with the `tox.ini` pin of the same tool.

## Customisation

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
