---
name: standards-alignment
description: Bring a Shaken Fist repository up to the project consistency standards, one update per commit. Use when onboarding a repo to the daily consistency audit, fixing consistency audit issues filed against a repo, or when a repo has drifted from how the rest of the fleet is packaged and automated.
---

# Standards Alignment Skill

Use this skill to bring a Shaken Fist repository into line with the
project consistency standards defined in this repository, and to keep
it there by adding it to the daily audit fleet.

## Sources of truth

Do not work from a memorised checklist -- the standards move. The
authority is:

- `docs/audits/` -- one specification per standard, including the
  ones no script checks (functional test coverage, credential
  handling). `docs/audits/README.md` indexes them and says which
  repositories are in scope or excluded.
- `scripts/audit-check.py` -- exactly what the daily audit verifies,
  and therefore the definition of "would not create issues".
- `templates/` -- canonical starting points for most of the files the
  audit expects. Copy templates rather than improvising; several
  encode hard-won fixes.

Start and finish by running the audit against the target clone:

```bash
python3 scripts/audit-check.py \
    --repo-path /path/to/target --repo-name <repo> | python3 -m json.tool
```

The first run is your work list; the final run is your proof. The
GitHub-API-backed checks (default branch, security settings, delete
branch on merge) query the live repo, so they are accurate even when
run against a local clone.

## Ground rules

- **One update per commit.** Each standard lands as its own commit
  with a message explaining which audit it addresses.
- Branch from `develop` in the target repo. Never create the PR
  yourself; Michael does that.
- Fix any real bug that prompted the work first, as its own commit,
  before the standards commits.
- Run `pre-commit run --all-files` before every commit (once the
  target has a config; add it early if practical).

## Recommended order

Packaging first, because several checks key off the presence of
`pyproject.toml`; the rest are independent:

1. **pyproject.toml conversion** (if still on pbr/setup.py):
   model on `client-python`'s. setuptools_scm with `write_to`, the
   generated `_version.py` gitignored and never tracked. Replace
   runtime `pbr.version.VersionInfo` uses with `importlib.metadata`.
   Delete `requirements.txt`, `setup.py`, `setup.cfg`, `release.sh`.
   Declare everything the code imports directly (do not rely on
   transitive dependencies). Verify with `python3 -m build --wheel`
   and inspect the wheel's entry points.
2. **AGENTS.md and ARCHITECTURE.md** -- written from actual knowledge
   of the repo, not boilerplate.
3. **Release automation** -- `templates/release-automation/`,
   substituting the three placeholders. Remind Michael about the
   one-time PyPI trusted publisher and `release` environment setup.
4. **Lint and pre-commit** -- copy `clingwrap`'s
   `.pre-commit-config.yaml`, `.github/actionlint.yaml`,
   `tools/flake8wrap.sh` and a trimmed `tox.ini`; add a `test` extra
   with tox and flake8. Fix existing flake8 findings in the same
   commit so the tree lints clean from the start.
5. **CI + review automation** -- a `functional-tests.yml` with the
   project's real test jobs gating an `automated_reviewer` job that
   calls the reusable workflow
   `shakenfist/actions/.github/workflows/pr-auto-review.yml@main`,
   plus the three `pr-*.yml` templates from
   `templates/ci-review-automation/` copied verbatim.
6. **Renovate** -- `templates/renovate/`; client/library projects use
   `rangeStrategy: "widen"` for grpc packages, applications use exact
   pins.
7. **export-repo-config** and **CodeQL** -- verbatim template copies
   (CodeQL only for public repos; check visibility, not a hunch).
8. **Secret scanning** -- a gitleaks job, following ryll's
   `ci.yml` (see gotchas below).
9. **README as a pitch** -- <= 150 lines / 1200 words, absolute links
   only, links into `docs/` if that directory exists. Verify any
   command examples against the actual CLI option names.
10. **Repo settings** via the GitHub API (see below).
11. **Add the repo to the audit matrix** in this repository's
    `.github/workflows/consistency-audit.yml`, as its own branch and
    commit here. Land it after the target repo's branch, so the first
    daily audit does not file issues against work still in flight.
    The per-audit status tables in `docs/audits/*.md` regenerate daily --
    never hand-edit them.

## Repo settings commands

```bash
gh api -X PATCH repos/shakenfist/<repo> \
    -F delete_branch_on_merge=true -F allow_auto_merge=true
gh api -X PATCH repos/shakenfist/<repo> --input - <<'EOF'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}}}
EOF
gh api -X PUT repos/shakenfist/<repo>/automated-security-fixes
```

## Gotchas that have already burned time

- The audit greps workflows for `review-pr-with-claude@main`, but the
  modern reviewer is the *reusable workflow* `pr-auto-review.yml`.
  Copying `pr-re-review.yml` from the templates satisfies the grep;
  do both, not one.
- The reviewer's `permissions:` block goes on the **calling** job (a
  cross-repo reusable workflow cannot exceed its caller's token
  scope), and the calling job cannot set `runs-on:` or
  `timeout-minutes:`. Do **not** add `secrets: inherit`: the reviewer
  chain reads no secret and authenticates with `github.token` from
  that same `permissions:` block, so inheriting buys nothing while
  handing every secret the repository holds to a workflow living in
  another repository. `smoke-cluster.yml` callers are the exception --
  that one does read secrets. The `ci-review-automation` audit checks
  for this.
- `functional-tests.yml` must keep a `workflow_dispatch:` trigger or
  the `pr-retest.yml` bot automation cannot re-run it.
- gitleaks: invoke the binary directly (`gitleaks-action@v2` refuses
  organization repos without a paid licence) and run on `debian-13`
  (bookworm does not package gitleaks). Add `debian-13` to
  `.github/actionlint.yaml`'s runner labels or actionlint fails.
  Pass `--log-opts="HEAD"`: the default scans every ref, including a
  `gh-pages` copy of the project's own documentation, and 8.16
  misattributes those findings to unrelated merge commits. Do not let
  the job consume a docs-only path filter.
- Static runners advertise exactly `[self-hosted, static]`; adding a
  size, `vm`, or an OS label to a `static` job means it never
  schedules.
- Every workflow needs a top-level `permissions:` block, even
  `permissions: {}`.
- If a job points pip at the devpi cache (192.168.1.15:3141), it must
  also set `PIP_EXTRA_INDEX_URL: https://pypi.org/simple/` in the
  same env block.
- A repo with no `.gitignore` at all has happened; check rather than
  assume.
- None of the `@shakenfist-bot` triggers work on the PR that
  introduces them: `issue_comment`-triggered workflows run from the
  **default branch**, so pr-re-review and pr-retest only go live once
  the PR merges. Do not burn time posting trigger comments at the
  alignment PR itself, and expect the automated reviewer's findings on
  that PR to need manual addressing.

## What the audit does not check

Passing `audit-check.py` is necessary, not sufficient. Read the
specifications in `docs/audits/` for the judgment-call standards --
most importantly functional test coverage ("a test for everything
exposed on the command line") -- and report remaining gaps to Michael
honestly rather than declaring the repo done.
