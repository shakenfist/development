# Rust Release Automation Template

This template is the release process for Shaken Fist's Rust
repositories: ryll, instar and andris. A version bump goes through the
merge queue as a pull request, the merged commit is tagged, and the tag
starts a workflow that checks CI on that commit, builds, waits for a
reviewer, signs the tag with Sigstore and publishes.

Python projects use
[`templates/release-automation/`](https://github.com/shakenfist/development/tree/main/templates/release-automation)
instead. The two share their CI gate, their signing and their guards,
and this README links to that one where the text would be the same.

`release.yml` has two kinds of job. The fixed jobs (`ci-gate`,
`check-version`, `sign-tag` and `github-release`) are the same in every
Rust repository, and the invariants below are written against them. The
build section, between the `BEGIN` and `END` comments, holds the
repository's own build and publish jobs. Those differ too much between
the three repositories to template, so the invariants constrain how the
section connects to the fixed jobs, not what it builds.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `release.yml` | `.github/workflows/release.yml` | The release workflow |
| `propose-release.sh` | `tools/propose-release.sh` | Bumps the version and opens the release pull request |
| `tag-release.sh` | `tools/tag-release.sh` | Tags the merged release commit and pushes the tag |
| `release-version.sh` | `tools/release-version.sh` | Reads, sets and checks the version; `check-version` runs it |
| `sign-release-tag.sh` | `tools/sign-release-tag.sh` | Signs and force-pushes the tag; `sign-tag` runs it |

The four scripts must be executable. Copy them with their mode, or run
`chmod +x tools/*.sh` after copying. A repository may add Makefile
wrappers, such as `make propose-release X.Y.Z`, as ryll and andris do.

The one-time GitHub setup is the same as for the Python template:
follow steps 2 to 4 of its
[`RELEASE-SETUP.md`](https://github.com/shakenfist/development/blob/main/templates/release-automation/RELEASE-SETUP.md)
for the `release` environment, the tag ruleset and the merge queue. Registry setup, such
as a crates.io token or a PyPI trusted publisher, differs per
repository and belongs in its own `docs/releasing.md`.

## Placeholders

| Placeholder | File | Example | Description |
|-------------|------|---------|-------------|
| `{{PROJECT_DISPLAY_NAME}}` | `release.yml` | `Ryll` | Human-readable project name, in the header comment |
| `{{CI_WORKFLOWS}}` | `release.yml` | `ci.yml` | The CI workflows the release gate requires |
| `{{CARGO_TOML}}` | `release-version.sh` | `Cargo.toml` | The `Cargo.toml` whose version is the release's version |
| `{{CARGO_LOCK}}` | `release-version.sh` | `Cargo.lock` | The committed lockfile, or empty if there is none |
| `{{CRATE_NAME}}` | `release-version.sh` | `ryll` | The crate whose `Cargo.lock` entry must carry the version, or empty |

`{{CI_WORKFLOWS}}` follows the same rules as in the Python template:
bare file names, one per line. See its
[Placeholders](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#placeholders)
section.

Where the crates inherit one version from `[workspace.package]`,
`{{CARGO_TOML}}` is the workspace's root `Cargo.toml`, and
`release-version.sh` reads its `[workspace.package]` version.
Otherwise it is the main crate's own `Cargo.toml`, and the script reads
its `[package]` version.

`propose-release.sh` also has three functions to adapt, in its marked
per-repository section:

- `bump_version` sets the version everywhere the repository records
  it, `Cargo.lock` included. The default sets the version file and
  runs `make lock`.
- `run_tests` runs the tests on the bumped tree before it is
  committed. The default is `make test`.
- `check_unpublished` fails if a registry already has the version.
  The default checks nothing, which suits a repository that publishes
  only to GitHub Releases.

## The release process

1. **Propose.** From a clean checkout of the default branch, in step
   with `origin`, run `tools/propose-release.sh X.Y.Z`. It creates a
   `release-X.Y.Z` branch, bumps the version, refuses to go on if the
   bump left an untracked file, runs pre-commit over the bumped tree,
   the workflow's own version check and the tests, and asks before it
   commits `Release X.Y.Z.`, pushes the branch and opens a pull
   request. It never tags. On `N`, or on any failure before the push,
   it deletes the branch and leaves the checkout as it was.
2. **Merge through the queue.** Get the pull request reviewed and land
   it through the merge queue like any other change. The queue may
   merge, rebase or squash: the next step finds `Release X.Y.Z.` as
   written, or with the ` (#N)` a squash merge appends. The queue's run
   on the merged commit is the CI result the release gate requires. A
   merge that bypasses the queue leaves a commit with no such run, and
   the gate refuses to release it.
3. **Tag.** Run `tools/tag-release.sh X.Y.Z`. It finds the commit
   that merged `Release X.Y.Z.`, which is the queue's merge commit
   rather than the default branch's tip. It checks that this commit
   is on the default branch's first-parent history and carries the
   version, and lists what merged after it, which is not in the
   release. Then it asks, creates an annotated tag `vX.Y.Z`, pushes it
   and watches the workflow run for that commit. If the push fails it
   deletes the local tag, so the script can be run again.
   `tools/tag-release.sh X.Y.Z COMMIT` tags a later commit instead,
   for recovery.
4. **Approve.** The workflow checks CI on the tagged commit, checks the
   version and builds. It then waits at `sign-tag` for a reviewer to
   approve the `release` environment. A publishing job that names the
   environment itself, as a PyPI trusted publisher requires, asks a
   second time.

Running the workflow by hand (**Actions -> Release -> Run workflow**)
on any branch is a dry run. It runs `check-version` and the build jobs,
and `ci-gate`, `sign-tag`, `github-release` and every publishing job
show as skipped.

## The CI gate

`ci-gate` and `check-version`'s guard are the Python template's
`ci-gate` and `build` guard, with `check-version` as the root of the
build graph. Everything the Python template's README says about them
applies here unchanged:

- [The CI gate](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#the-ci-gate):
  what counts as CI, the review-mark walk, and why there is no
  override.
- [Recovering from a gate failure](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#recovering-from-a-gate-failure):
  what to do for each reason the gate fails.
- [Prerequisites for gated CI](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#prerequisites-for-gated-ci):
  what the named workflows' triggers, concurrency groups and
  `paths-ignore` must be for the gate to pass.
- [The sign-tag runner](https://github.com/shakenfist/development/blob/main/templates/release-automation/README.md#the-sign-tag-runner):
  why `sign-tag` must run on a throwaway runner.

Where that recovery table says to move the tag, the commands here are:

```bash
git push origin :refs/tags/vX.Y.Z   # needs the tag ruleset's bypass
git tag -d vX.Y.Z
tools/tag-release.sh X.Y.Z <commit that went through the queue>
```

A `check-version` failure is recovered the same way when the version
was wrong at the tagged commit: fix it with a new release pull
request, then delete the tag and run `tools/tag-release.sh` again.
Nothing has been signed or published at that point.

## Invariants

Every Rust release keeps these. Each one can be checked by reading
`.github/workflows/release.yml` or the repository's files. "The push
and tag guard" is
`if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')`,
exactly. "The skipped-gate guard" is
`${{ !cancelled() && (needs.ci-gate.result == 'success' || (github.event_name != 'push' && needs.ci-gate.result == 'skipped')) }}`.
A job "needs" another if it lists it in `needs:`, or needs a job that
does.

1. `on:` has exactly two triggers: `push`, with `tags: ['v*']` and
   no `branches`, and `workflow_dispatch`, with no `inputs`.
2. The workflow sets `permissions: {}` at the top level, and every
   job has a `permissions:` block of its own.
3. The workflow sets a top-level `concurrency` whose `group` contains
   `github.ref`, with `cancel-in-progress: false`.
4. A job named `ci-gate` has the push and tag guard, no `needs`,
   `runs-on: [self-hosted, static]`, and permissions of exactly
   `actions: read` and `contents: read`. Its one step uses
   `shakenfist/actions/require-ci-success@main`, sets
   `timeout-minutes`, and gives a `workflows` input of one or more
   bare file names, each of which exists in `.github/workflows/`.
5. `ci-gate` is the only job with no `needs`. A job named
   `check-version` has `needs: ci-gate` and nothing else, the
   skipped-gate guard, and permissions of exactly `contents: read`,
   and runs `tools/release-version.sh check "${GITHUB_REF}"`. Every
   job other than those two needs `check-version`.
6. No job-level `if:` contains `always()`, and `check-version`'s is the
   only job-level `if:` that contains `!cancelled()`.
7. Every job with an effect outside the run has the push and tag
   guard. Such a job names an `environment`, or holds
   `contents: write` or `id-token: write`, or reads a secret other than
   `GITHUB_TOKEN`.
8. A job named `sign-tag` names `environment: release`, has the push
   and tag guard, has permissions of exactly `contents: write` and
   `id-token: write`, and runs `tools/sign-release-tag.sh`. Its
   `runs-on` includes the `vm` label, or is a single GitHub-hosted
   label with an `audit-ok: github-hosted-runner` comment on the line
   above it.
9. `sign-tag` needs every job that has an `actions/upload-artifact`
   step.
10. Every job named under invariant 7, apart from `ci-gate` and
    `sign-tag`, needs `sign-tag`.
11. Only `sign-tag` and `github-release` hold `contents: write`.
    `id-token: write` is held only by `sign-tag` and by jobs that name
    `environment: release`.
12. A job named `github-release` needs `sign-tag` and has the push and
    tag guard, with permissions of exactly `contents: write`. It
    downloads artifacts with `pattern: release-*` into a path under
    `${{ runner.temp }}`, and creates the release with
    `softprops/action-gh-release` and `fail_on_unmatched_files: true`.
13. Every job whose `actions/upload-artifact` step uploads a path
    naming `*.whl` has a step using `shakenfist/actions/check-dist@main`
    before it, whose `path` is the directory holding those wheels.
14. `tools/propose-release.sh`, `tools/tag-release.sh`,
    `tools/release-version.sh` and `tools/sign-release-tag.sh` exist,
    are executable, and contain no `{{`. `tag-release.sh` and
    `sign-release-tag.sh` are identical to this template's copies.
    `propose-release.sh` and `release-version.sh` are identical to them
    outside the lines between their `--- per-repository settings ---`
    and `--- end of per-repository settings ---` markers.
15. No Makefile, shell script or workflow in the repository, other
    than `tools/tag-release.sh` and `tools/sign-release-tag.sh`,
    contains a `git tag` command. A release tag is only ever made by
    the script that tags a merged commit.

## Filling the build section

Every job in the build section needs `check-version`, and `sign-tag`
lists every one of them in `needs`. An artifact named `release-*` is
attached to the GitHub release, and any other artifact is not, which
is how a repository keeps its wheels or its intermediate files off the
release page. `github-release` may gain steps before its release step
that check or add to what it downloaded, as long as it keeps the shape
invariant 12 requires.

`github-release` needs only `build` and `sign-tag`, so the release page
can appear while a registry publish is still running, or after one has
failed. That suits a repository whose GitHub release is its primary
artefact, as instar's is. A repository whose registry is primary adds
its publishing jobs to `github-release`'s `needs`; a repository whose
publishing job reads the GitHub release, as andris's `publish-pypi`
does, keeps it the other way round.

The three repositories fill it like this:

| | ryll | instar | andris |
|---|---|---|---|
| Build jobs | `build-linux` (vm runner, `make release`, `make test`, `make package-check`, `.deb` and `.rpm`); `build`, a GitHub-hosted matrix for Linux aarch64, macOS and Windows; `build-ryll-wheels`, maturin wheels for x86_64 and aarch64 | one `build` job: `make instar`, a glibc-floor check, a tarball, `.deb` and `.rpm` | `build-wheels`, maturin wheels for x86_64 and aarch64, each smoke-tested |
| Publishing jobs | `publish-crates` (crates.io token); `publish-ryll-pypi` (trusted publisher, names `release`); `update-homebrew` (tap token) | none beyond `github-release` | `publish-pypi` (trusted publisher, names `release`) |
| GitHub release | `.deb`, `.rpm`, tarball and zip; artifacts already named `release-*` | tarball, `.deb` and `.rpm`; its `binary-*` artifacts are renamed `release-*` | the two wheels plus `SHA256SUMS`, written by an extra step in `github-release` |
| `check-dist` | in `build-ryll-wheels`, on `target/wheels/` | none; it builds no wheel | in `build-wheels`, on its wheel output directory |
| Version | `[workspace.package]` in `Cargo.toml`; crate `ryll` in `Cargo.lock` | `[package]` in `src/vmm/Cargo.toml`; every other crate carries its own copy, so `bump_version` sets each; no committed `Cargo.lock` | `[workspace.package]` in `Cargo.toml`; crate `andris` in `Cargo.lock` |
| `check_unpublished` | crates.io, for each workspace crate | nothing | PyPI |

Each also has fixed-job changes to make on converging, so the table is
not the whole of each diff:

- **ryll** has no `workflow_dispatch` and no guards. It gains both, so
  that its publishing jobs and `github-release` take the push and tag
  guard. Its `check-version` stops filing an issue on a mismatch, so
  it drops `issues: write`: the run already fails before anything is
  signed, and `tag-release.sh` is watching it. Its `sign-tag` stays on
  `ubuntu-latest` with its `audit-ok` marker, which invariant 8
  allows.
- **instar** releases today with `make release`, which commits the
  bump and tags it locally for a direct push to the default branch.
  That target goes, replaced by the two scripts. Its `build` drops
  `always()` for `needs: check-version`. Its `check-version` drops
  its tag-only `if:` and its issue filing. Its `sign-tag` moves to a
  `vm` runner, and its `sign-tag` and `github-release` gain the guard.
- **andris** already has the dispatch trigger, the guards and the
  concurrency group. Its `scripts/` release scripts become this
  template's `tools/` copies.

## Repositories that publish wheels

A repository whose build section builds Python wheels, with maturin or
otherwise, adds a `check-dist` step to each job that builds one,
after the build and before the `actions/upload-artifact` step. That
way a wheel carrying a test directory stops the release before
anything is signed. The step is commented out in the template's
`build` job:

```yaml
      - name: Check the wheel contents
        timeout-minutes: 2
        uses: shakenfist/actions/check-dist@main
        with:
          path: target/wheels/
```

Set `path` to the directory the job uploads from: the action checks
every `*.whl` directly in it, and fails if there is none. The action
needs `python3` on the runner, which the vm runners and GitHub-hosted
Linux runners have. See
[`check-dist`](https://github.com/shakenfist/actions/blob/main/docs/actions.md#check-dist)
for what it rejects.

## Converging an existing release

Adapt the repository's `release.yml` to this template by hand. Never
copy the template over the file, which would lose the build section.
Move the existing build and publish jobs into the marked section, make
the fixed jobs match the template, copy the four scripts into `tools/`
and fill in their settings, then read the result against the
invariants above.
