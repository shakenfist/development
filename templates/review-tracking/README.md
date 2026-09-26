# Review tracking templates

The CI half of whole-codebase review tracking, for a repository that
has adopted it. On the default branch, after every push and once a
day, the workflow prunes review marks made stale by a change and
imports reviews of byte-identical files from this repository, then
commits both back as shakenfist-bot in one commit,
`Prune and import review marks.`. The design, and the argument for
why an unsigned bot commit may do either, is in
[code review tracking](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md).

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `prune-reviews.yml` | `.github/workflows/prune-reviews.yml` | Runs the script on push, daily, and on dispatch, on the default branch only |
| `ci-prune-reviews.sh` | `tools/ci-prune-reviews.sh` | Installs gitsign, clones this repository, runs `prune` then `import` against the branch tip, and lands the result, regenerating it on a new tip if a push is rejected |
| `review-tracking.sh` | `tools/review-tracking.sh` | The by-hand wrapper: finds a clone of this repository and passes its arguments to `scripts/review-tracking.py` |

All three copy directly, with no per-project substitution, and must
stay byte-identical to the template in every adopted repository
except shakenfist/development, whose wrapper is described below.
That is not only tidiness: a
review mark attests to a blob SHA, so an identical copy picks up this
repository's review of the template through `import`, and a copy
that differs by a branch name has to be reviewed again in every
repository that carries it. Nothing in them names a repository or a
branch. The default branch comes from the workflow event
(`github.event.repository.default_branch`, or the ref of a scheduled
run, which is always the default branch) and reaches the script as
`DEFAULT_BRANCH`.

## What the adopting repository needs

These are steps of
[Adopting a repository](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md#adopting-a-repository);
the workflow does not work without them.

- **`.vscode/review-scope.toml`**, defining what is in review scope
  (step 4).
- **The `.gitignore` exception** for `.vscode/*.weaudit-shas.json`
  (step 1), if the repository ignores `.vscode/*`. The imports file,
  `.vscode/imports.weaudit-shas.json`, is covered by it; without it
  git cannot see what the import wrote, so the script fails the run
  rather than import the same reviews every day and commit none.
- **The `paths-ignore` block** in the code-shaped workflows (step 8),
  so that the bot's commit does not start the expensive CI lanes.
- **Optionally, a `DEPENDENCIES_TOKEN` secret.** A repository whose
  default branch ruleset requires a pull request needs it: GitHub
  does not accept the built-in Actions app as a bypass actor, so the
  push authenticates as shakenfist-bot with that token instead. A
  repository without the secret pushes with `GITHUB_TOKEN`, for which
  the job asks `contents: write`. The checkout uses
  `secrets.DEPENDENCIES_TOKEN || github.token`, so one file serves
  both.

## gitsign

`import` verifies the signature on each source commit it points at,
and without gitsign on `PATH` it imports nothing (by design; it warns
and still exits zero). The static runners do not carry gitsign, so
the script downloads a pinned `sigstore/gitsign` release's
`linux_amd64` binary into `RUNNER_TEMP` on every run. It checks the
download twice before running it: the release's `checksums.txt` must
list the digest pinned beside the version in the script, and the
binary must hash to that digest. The pinned digest is what actually
fixes what runs -- `checksums.txt` comes from the same place as the
binary -- and the `checksums.txt` check catches a version bumped
without its digest.

Renovate does not read a release out of a shell script, so bump
`GITSIGN_VERSION` and `GITSIGN_SHA256` together by hand, taking the
digest from the new release's `checksums.txt`.

Verification also needs egress to Rekor and to Sigstore's TUF root.
If a runner cannot reach them, each import is skipped with a warning
naming the commit that did not verify, and the run still succeeds;
`timeout-minutes` allows for gitsign's per-commit timeout. The
workflow never passes `--no-verify`.

## The development clone

The script clones this repository with full history into
`RUNNER_TEMP` and exports `SHAKENFIST_DEVELOPMENT`, which is the first
place the wrapper looks. Full history rather than depth 1, because a
copy in a target often matches an older revision of a file reviewed
here, and only the review state's history still records that review.
The clone is about 16 MB.

## This repository

This repository runs the same workflow and script:
`.github/workflows/prune-reviews.yml` and `tools/ci-prune-reviews.sh`
here are byte-identical copies of the files in this directory, and
`ReviewTrackingDeploymentTest` in `scripts/tests/test_docs_content.py`
asserts it, and `scripts/tests/test_ci_prune_reviews.py` runs the
template script against a throwaway origin. shellcheck lints the
template scripts in place, so identity is not about lint coverage:
an identical copy inherits this repository's review of the template
through `import`, and a copy that drifted means the file shipped is
not the file run here. Sync from here rather than editing either
copy in place.

Its `tools/review-tracking.sh` deliberately differs. It runs
`scripts/review-tracking.py` from its own tree and must not search for
a clone: the search order above would find a sibling clone and run
that one's copy of the script, which is the wrong answer in the one
repository where the right answer is certain (step 5 of
[Adopting a repository](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md#adopting-a-repository)).
It therefore ignores the `SHAKENFIST_DEVELOPMENT` clone the script
makes, which here costs an unused 16 MB clone per run, and `import`
run against this repository is a no-op that says so and exits zero.

## Prerequisites

- Self-hosted runners with the `static` label, `x86_64`, with `curl`
  and egress to github.com.
- Repositories adopted into review tracking; see the steps above.

## Projects using these templates

The repositories adopted into review tracking. The Templates
column says whether a repository runs these files yet, or still
carries its earlier, per-repository copies pending the rollout
[PLAN-review-import.md](https://github.com/shakenfist/development/blob/main/docs/plans/PLAN-review-import.md)
describes.

| Project | Default branch | Token | Templates |
|---------|----------------|-------|-----------|
| [actions](https://github.com/shakenfist/actions) | `main` | `GITHUB_TOKEN` | pending |
| [development](https://github.com/shakenfist/development) | `main` | `GITHUB_TOKEN` | adopted |
| [hunkydory](https://github.com/shakenfist/hunkydory) | `develop` | `DEPENDENCIES_TOKEN` | pending |
| [kerbside](https://github.com/shakenfist/kerbside) | `develop` | `DEPENDENCIES_TOKEN` | pending |
| [ryll](https://github.com/shakenfist/ryll) | `develop` | `DEPENDENCIES_TOKEN` | pending |
