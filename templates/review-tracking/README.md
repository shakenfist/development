# Review tracking templates

The CI half of whole-codebase review tracking, for a repository that
has adopted it. On the default branch, after every push and once a
day, the workflow prunes review marks made stale by a change and
imports reviews of byte-identical files from this repository, then
commits both back in one commit, `Prune and import review marks.`.
The commit is shakenfist-bot's; the push is made by the
`shakenfist-review-marks` GitHub App where the default branch's
ruleset requires a pull request, and by `GITHUB_TOKEN` elsewhere.
The design, and the argument for why an unsigned bot commit may do
either, is in
[code review tracking](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md).

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `prune-reviews.yml` | `.github/workflows/prune-reviews.yml` | Runs the script on push, daily, and on dispatch, on the default branch only |
| `ci-prune-reviews.sh` | `tools/ci-prune-reviews.sh` | Installs gitsign, clones this repository, runs `prune` then `import` against the branch tip, and lands the result, regenerating it on a new tip if a push is rejected; if `import` fails, it lands `prune`'s result alone and then fails the run |
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
  so that the bot's commit does not start the expensive CI lanes. It
  lists the review state only, never `.vscode/review-scope.toml`.
- **A `review-marks` environment with the default-branch policy**,
  before the workflow's first run, in every adopting repository,
  whether or not it will hold a key. The job names the environment,
  and GitHub creates one it cannot find on first reference, with no
  branch policy: a key added to it later would be readable by a
  workflow on any branch. The default-branch policy means exactly
  this, and nothing looser: the environment's
  `deployment_branch_policy` is `{"protected_branches": false,
  "custom_branch_policies": true}`, and its
  `deployment-branch-policies` hold exactly one entry, with
  `type: branch` and `name` equal to the default branch, containing
  no glob characters. "Admits the default branch" is not the test. A
  `protected_branches: true` policy admits any protected branch, a
  branch name is a glob, and a policy can be `type: tag`, so a set
  such as {`develop`, tag `*`} admits the default branch and also
  lets any writer push a tag at a commit with an edited workflow and
  read the key. A branch policy rather than required reviewers,
  unlike the pin-indirect-dependencies template, because this job
  runs unattended and a reviewer gate would stall every run.
- **Where the default branch's ruleset requires a pull request, the
  App, its key and the bypass**, created in this order after the
  environment:

  1. the `shakenfist-review-marks` GitHub App installed on the
     repository;
  2. the App's private key as the secret `REVIEW_MARKS_APP_KEY` of
     the `review-marks` environment;
  3. the App added to that ruleset's bypass actors.

  The App's client id is the organisation variable
  `REVIEW_MARKS_APP_CLIENT_ID`, which every repository already sees.
  A repository without such a ruleset needs none of the three:
  GitHub does not accept the built-in Actions app as a bypass actor,
  which is the only reason for the App, and everywhere else the push
  is made with `GITHUB_TOKEN`, for which the job asks
  `contents: write`. Step 7 of
  [Adopting a repository](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md#adopting-a-repository)
  has the commands.

  The key is only ever an environment secret, never a repository or
  organisation one. `secrets.REVIEW_MARKS_APP_KEY` resolves at all
  three levels, and the workflow would mint from a key held at any
  of them just as quietly, but a repository or organisation secret
  is readable by a workflow on any branch, which is what the
  environment's policy exists to prevent.

  The workflow's `Mint the push token` step runs only where the
  environment holds the key. It mints an installation token with
  `actions/create-github-app-token`, pinned to a commit: one per run,
  scoped to the repository, carrying `contents: write` alone, and
  revoked when the job ends. The script step is given
  `steps.app-token.outputs.token || github.token` as `PUSH_TOKEN`,
  so one file serves both kinds of repository; the script gives it to
  its `git push` and nothing else (see "Runner isolation" below). The
  job's `permissions:` block is for `GITHUB_TOKEN` only: the App's
  token carries its own permissions, which that block does not
  constrain. The job's `environment:` sets `deployment: false`, which
  keeps its runs out of the repository's Deployments view while still
  resolving the environment's secrets; should the environment ever
  gain a custom deployment protection rule, the job fails at once.

  The fallback to `GITHUB_TOKEN` says nothing about itself. Where
  that is wrong, it fails rather than landing: a repository whose
  ruleset requires a pull request but whose environment holds no key
  mints nothing and pushes with `GITHUB_TOKEN`, the ruleset refuses
  that push, and the run fails with the refusal in the rule-suite
  history. That failed run is the only signal that the key is
  missing, and nothing lands until the key is added.

## gitsign

`import` verifies the signature on each source commit it points at,
and without gitsign on `PATH` it imports nothing (by design; it warns
and still exits zero). No runner image is relied on to carry gitsign,
so the script downloads a pinned `sigstore/gitsign` release's
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
naming the commit that did not verify, and the run still succeeds.
gitsign waits up to two minutes per commit, so after three timeouts
in one run `import` stops verifying and skips the rest with one
warning saying how many, leaving them for a later run; that keeps a
hung Rekor inside `timeout-minutes` however many commits are waiting.
The workflow never passes `--no-verify`.

## Runner isolation

The job runs on the fleet's ephemeral vm runners
(`[self-hosted, vm, debian-13, s]`), a fresh machine per job, so
nothing an earlier job ran is still there while it holds the App's
key and the token minted from it. The design below assumes a
runner that is long-lived, shares one home directory across jobs and
repositories, and runs pull request code, as the static runners do,
and treats anything an earlier job could write as hostile. On a vm
runner none of that holds, and the design is kept anyway: as defence
in depth, and because the runner is one line of the workflow, after
which every threat below would be live. The script therefore:

- **never touches the workspace.** On a static runner the
  workspace is reused, `.git` included, so hooks an earlier job left
  in `.git/hooks` would run on any git command there --
  `actions/checkout`'s own included, which
  could rewrite the script before it ran -- and keys left in
  `.git/config` -- `core.fsmonitor`, an `include.path`, a `pushurl` or
  `url.<x>.insteadOf` -- would run commands or redirect the push. So
  the workflow has no checkout. Its first step, which has no token,
  clones the default branch at depth 1 into `RUNNER_TEMP` from the URL
  `GITHUB_REPOSITORY` names, which the runner sets, ignoring the
  runner's git configuration, and the second runs the script from that
  clone. The script then lands from a second fresh clone of the same
  kind, fetching, pruning, importing, committing and pushing there.
- **ignores global and system git configuration**, by exporting
  `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1` before its
  first git command, so that a `url.<x>.insteadOf` left in
  `~/.gitconfig` cannot swap either clone for another, and **any
  repository above `RUNNER_TEMP`**, through `GIT_CEILING_DIRECTORIES`,
  so that a `.git` planted in `_work` or the home directory is never
  discovered. It sets the bot's commit identity in the target clone's
  own configuration. A runner that needs a proxy or a private CA must
  supply it through the environment (`HTTPS_PROXY`, `GIT_SSL_CAINFO`),
  which git still reads.
- **gives gitsign a fresh TUF cache** in `RUNNER_TEMP` on every run,
  through `TUF_ROOT`, rather than the shared `~/.sigstore`, where a
  poisoned cache could change which Fulcio roots and Rekor keys
  verification trusts. An empty cache starts from the root embedded in
  the pinned gitsign binary.
- **scopes the push token to the push.** No clone is given
  credentials; fetches need none, since these repositories are public.
  The script takes `PUSH_TOKEN` out of its environment before running
  anything, so curl, gitsign and `review-tracking.py` never see it,
  and passes it to `git push` alone as an
  `http.https://github.com/.extraheader` through `GIT_CONFIG_COUNT`,
  never in argv. The token is still readable, through
  `/proc/<pid>/environ` of the step's shell and the script itself as
  well as of the push, and in the memory of the runner's own worker
  process, which holds every secret the job resolved, by any process
  running as the runner's user, for the whole run: `unset` changes what children inherit, not the
  environment a process was started with. Process memory is readable
  the same way, through ptrace or `/proc/<pid>/mem` as
  `kernel.yama.ptrace_scope` allows, so unsetting earlier would not
  help either. Only a runner user of the job's own, covering the
  worker as well as the steps, would close that. What bounds it is
  the runner killing a job's orphaned processes when the job ends,
  and the fresh clone, which leaves an earlier job's code no hook
  through which to start such a process inside this one.

This narrows what a poisoned runner can do; it does not make a runner
whose user account is compromised safe. Such an account can still
replace binaries on `PATH`, or rewrite the script in `RUNNER_TEMP`
between the step that fetches it and the step that runs it.

## The development clone

The script clones this repository with full history into
`RUNNER_TEMP` and exports `SHAKENFIST_DEVELOPMENT`, which is the first
place the wrapper looks. Full history rather than depth 1, because a
copy in a target often matches an older revision of a file reviewed
here, and only the review state's history still records that review.
The clone is about 16 MB. It is a plain clone, because `import`
walks the clone's remote-tracking ref for this repository's default
branch.

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

- Self-hosted ephemeral vm runners with the labels `vm`, `debian-13`
  and `s`, `x86_64`, with `curl`, `git` and `python3`, and egress to
  github.com.
- Repositories adopted into review tracking; see the steps above.

## Projects using these templates

The repositories adopted into review tracking. The
`review-tracking-templates` consistency audit
([criterion](https://github.com/shakenfist/development/blob/main/docs/audits/review-tracking-templates.md))
fails any of them whose copies are not byte-identical to these files,
so a template change is not finished until every row below is
re-synced.

| Project | Default branch | Pushes as |
|---------|----------------|-----------|
| [actions](https://github.com/shakenfist/actions) | `main` | `GITHUB_TOKEN` |
| [andris](https://github.com/shakenfist/andris) | `develop` | App |
| [development](https://github.com/shakenfist/development) | `main` | `GITHUB_TOKEN` |
| [hunkydory](https://github.com/shakenfist/hunkydory) | `develop` | App |
| [kerbside](https://github.com/shakenfist/kerbside) | `develop` | App |
| [kerbside-client](https://github.com/shakenfist/kerbside-client) | `develop` | `GITHUB_TOKEN` |
| [ryll](https://github.com/shakenfist/ryll) | `develop` | App |
| [sfui](https://github.com/shakenfist/sfui) | `develop` | `GITHUB_TOKEN` |
