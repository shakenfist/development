# Plan: onboarding hunkydory, and what TypeScript means for the fleet

## Prompt

Before responding to questions or discussion points in this
document, explore this repository thoroughly. Read the relevant
files and ground your answers in what they actually say. Do not
speculate about the repository when you could read it instead.
Flag any uncertainty explicitly rather than guessing.

There is no application code here. The artifacts are the audit
specifications in `docs/audits/`, the tooling in `scripts/` that
measures them, the templates in `templates/` that the rest of the
fleet copies, and the workflows that run all of it every morning
against every Shaken Fist repository.

Consult `AGENTS.md` for the conventions and the invariants that
are not visible in the code, and `ARCHITECTURE.md` for the shape
of the system. `docs/consistency-audits.md` is the reference for
what a daily run does, how to add a criterion, how to bring a
repository into scope, and how to test a change before it reaches
the fleet -- read it before changing anything under `scripts/` or
`docs/audits/`. `docs/code-review-tracking.md` covers the review
tooling, and `PUSH-AUDIT.md` is the pre-push review runbook that
every plan's final phase runs.

This plan is unusual for this repository in that most of its work
lands somewhere else: in `hunkydory`, and in the `33fl` repository
in the other organisation, which owns the static runner fleet. What
lands here is the scope registration, the npm dependency criteria,
and this document.

## Situation

`shakenfist/hunkydory` was created on 2026-09-11. It is a VS Code
extension that keeps the `@@` hunk headers in a patch file correct
while the file is edited, written in TypeScript with no runtime
dependencies. It is the fleet's **first TypeScript repository**, and
nothing in the audit, the templates or the runner images has met the
language before.

Three measurements frame the work.

**The audit already runs against it, and mostly passes.** Running
the checker by hand against a clone, as
`docs/consistency-audits.md` instructs before onboarding:

```
python3 scripts/audit-check.py --repo-path ~/src/shakenfist/hunkydory \
    --repo-name hunkydory --github-org shakenfist
```

reports 51 checks: **6 pass, 9 fail, 36 not applicable**. The nine
failures are all infrastructure rather than anything about the
code:

| Check | Why it fails |
|-------|--------------|
| `pre-commit-config` | No `.pre-commit-config.yaml` |
| `llm-context-lint-ci` | skillsaw runs from neither pre-commit nor CI |
| `renovate` | No `renovate.json`, no `renovate.yml` |
| `ci-review-automation` | No `pr-re-review.yml`, no `pr-retest.yml`, no reviewer action |
| `export-repo-config` | No `export-repo-config.yml` |
| `github-security` | No CodeQL workflow; secret scanning and push protection off |
| `delete-branch-on-merge` | Not enabled on the repository |
| `readme-absolute-links` | Three relative links in `README.md` |
| `docs-external-links` | One relative link in `docs/` leaving `docs/` |

The documentation criteria -- `llm-tooling`, `llm-doc-structure`,
`readme-structure`, `diagram-format`, `plan-phase-references` --
already pass, as does `default-branch-naming`: the repository was
created with `develop` as its default branch.

**Nine of the 36 not-applicable results are not-applicable because
the criterion is Python-specific**, not because the concern does not
exist. `release-process`, `pin-indirect-dependencies`,
`dependency-name-normalization`, `unused-declared-dependency`,
`undeclared-direct-dependency`, `renovate-lockstep-groups`,
`version-file-gitignore` and `python-version-targeting` all key off
`pyproject.toml`, and `pyproject-usage` off the presence of Python.
A TypeScript repository with a `package.json` and a
`package-lock.json` raises the same questions about pinning and
unused dependencies, and today the audit simply does not ask them.

**The runners cannot run npm.** No workflow anywhere in the fleet
uses `setup-node`, `npm ci` or `npm install`; the mermaid-lint
template records the reason, that running it "from the upstream
container keeps chromium and a node toolchain off the runners", and
that jsdom "pulls in an undici that needs a newer node than the
runners carry". The static runners boot `debian:12`
(`33fl/static_runner.yml:229`), which packages `nodejs 18.20.4`.
Debian 13 packages `nodejs 20.19.2`. Both were confirmed against the
archive rather than assumed.

hunkydory itself was verified to build and pass its 20 unit tests on
Debian 12's `nodejs 18.20.4` inside a container, and Biome 2.5.13 was
verified to install and run there too. So node 18 is *sufficient*;
the decision to move to Debian 13 below is taken for other reasons.

## Mission and problem statement

Bring `hunkydory` under the consistency audits and the human review
tracking, and in doing so decide what the fleet's conventions mean
for TypeScript: how npm runs in CI, what lints a TypeScript project,
what a pre-commit configuration looks like for it, and how its
dependencies are audited.

The plan deliberately does not try to make TypeScript a first-class
citizen of every criterion. It answers the questions hunkydory
actually raises, and leaves a second TypeScript repository to
generalise from two examples rather than one.

It also does not cover the extension's own functionality, which is
complete and tested, or its publication to the VS Code Marketplace
beyond the mechanics of a release workflow.

## Decisions

### D1. npm runs on static runners, from Debian packages, on Debian 13

Three options were considered: `actions/setup-node` on the existing
static pool, a pinned `node:20` container on an ephemeral VM runner,
and installing Debian's `nodejs` on the runner image.

`setup-node` is the wrong shape here. The static runners are not
ephemeral -- private-ci describes them as "a static shared runner
rather than an ephemeral per-job VM" -- so `setup-node` leaves a
tool cache under `_work/_tool/node/` that grows without bound and is
shared by every repository using the pool. It also downloads a node
that Debian already packages. The PATH change itself is harmless and
job-scoped, via `GITHUB_PATH`; the disk state is the problem.

The container option touches no shared state at all, and matches
what ryll and mermaid-lint already do, but it spends the scarcest
runner pool (`l`, six workers fleet-wide) on a thirty-second build.

So: `nodejs` and `npm` from Debian, installed on the static runner
image. That makes npm a first-class fleet capability rather than a
hunkydory workaround, keeps the runtime on Debian's security
support, and leaves CI as `npm ci` with nothing to download.

Taken together with the version question, this means moving the
static runners from `debian:12` to `debian:13`. hunkydory runs fine
on Debian 12's node 18.20.4, so this is not forced by hunkydory.
It is taken because node 18 reached upstream end of life in April
2025 and Debian 13's node 20.19.2 both matches what the project is
developed against and buys years rather than months. The conductor
already builds a `debian-13` label from a `debian:13` base image, so
the image is cached on the cluster, which is the precondition
`static_runner.yml` documents at its line 39.

The blast radius is the reason this is its own phase: it re-images
every `static` and `claude-code` runner in **both** the shakenfist
and mach33labs organisations.

### D2. Biome, not ESLint

One devDependency that both lints and formats, against roughly six
for `eslint` + `typescript-eslint` + `prettier` and their configs.
hunkydory has no runtime dependencies and the smaller surface keeps
it that way, and keeps Renovate quiet. Biome 2.5.13 was confirmed to
run on node 18.20.4, so this decision does not depend on D1 landing.

The cost is a smaller rule set and a smaller ecosystem than ESLint,
which is the conventional choice for a VS Code extension. If a
second TypeScript repository wants ESLint specifically, that is the
point to revisit rather than now.

Biome needs a `biome.json` matching the conventions `AGENTS.md`
already states -- 100 character lines, single quotes, semicolons --
because its defaults disagree with all three.

### D3. Publish to the VS Code Marketplace

Rather than deferring releases or attaching a `.vsix` to a GitHub
release, hunkydory publishes with `vsce publish`. The extension is
meant to be installed by people who are not us, and an extension
nobody can install from inside VS Code is one nobody installs.

This is the one decision with a prerequisite outside any repository:
an Azure DevOps publisher account for the `shakenfist` publisher id
already named in `package.json`, and a `VSCE_PAT` repository secret.
Until both exist the release phase cannot be completed, which is why
it is sequenced last and marked `Blocked`.

### D4. Write npm dependency criteria now

The three Python dependency criteria -- `pin-indirect-dependencies`,
`unused-declared-dependency`, `undeclared-direct-dependency` -- get
npm equivalents in this plan rather than being recorded as not
applicable.

The weaker option was available and was rejected: `package-lock.json`
does pin the full transitive tree and `npm ci` does enforce it, so
`pin-indirect-dependencies` is arguably satisfied by construction.
But that reasoning covers one of the three. Nothing checks that a
declared dependency is actually imported, or that an import is not
resting on a transitive pin, and those are the two that catch real
drift. Writing all three keeps the npm story symmetric with the
Python one instead of leaving two thirds of it unmeasured.

The criteria that stay not applicable, each with a stated reason in
`REPO_OVERRIDES`, are the packaging and Python-version ones:
`pyproject-usage`, `python-version-targeting`, `release-process`,
`version-file-gitignore`, `dependency-name-normalization` and
`renovate-lockstep-groups`.

### D5. 33fl is not touched by this plan's author

Another session is editing `33fl` concurrently. The runner phase
below specifies the change and its risks but is not to be executed
until that work has landed and the operator says so. Nothing else in
the plan writes to that repository.

## Execution

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Register hunkydory in the audit scope | Not started | |
| 2. hunkydory adopts the local tooling | Not started | |
| 3. npm dependency criteria | Not started | |
| 4. Static runners gain node | Blocked | |
| 5. hunkydory CI and the fleet workflows | Not started | |
| 6. Human review onboarding | Not started | |
| 7. Marketplace release | Blocked | |
| 8. Push audit | Not started | |

Phase 4 is blocked on D5: `33fl` has another session working in it.
Phase 5 depends on phase 4, because a CI workflow that runs `npm ci`
on a runner without npm is a workflow that fails on arrival. Phase 7
is blocked on the publisher account and `VSCE_PAT` from D3.

Phases 1, 2, 3 and 6 have no such dependency and can proceed in any
order. Phase 1 should go first regardless: see its section.

### 1. Register hunkydory in the audit scope

The `scope-coverage` criterion reconciles the audit lists against
the organisation every morning and reports a repository that appears
in neither the matrix nor the excluded list. hunkydory has existed
since 2026-09-11 and appears in neither, so this criterion is
failing against `development` now, and will keep failing until this
phase lands. That is why it goes first and alone rather than waiting
for the rest of the plan.

Following `docs/consistency-audits.md`:

- Add `hunkydory` to the matrix in
  `.github/workflows/consistency-audit.yml`.
- Add it to the in-scope list in `docs/audits/README.md`.
- Add a `REPO_OVERRIDES` entry in `scripts/audit/repo.py` marking
  the Python packaging criteria from D4 not applicable, each with a
  reason. Not an `only_checks` list: hunkydory is expected to meet
  the conventions, it simply is not a Python project.

The nine failures from the Situation section become nine issues on
`hunkydory` at the next run. That is the intended behaviour -- they
are the backlog the rest of this plan works through -- but it should
be a deliberate choice rather than a surprise, and the phase says so
here so the next reader knows the issues were expected.

### 2. hunkydory adopts the local tooling

Everything that does not need a runner:

- Biome as a devDependency, with `biome.json` set to 100 character
  lines, single quotes and semicolons per D2, and the existing
  source brought into compliance.
- `tools/check-node.sh`, running the build, the tests and Biome,
  following the pattern ryll uses for `scripts/check-rust.sh`: one
  script called by both pre-commit and CI, so the two cannot drift.
- `.pre-commit-config.yaml` with that script as a `language: script`
  local hook, plus shellcheck, gitleaks and skillsaw from the fleet
  configuration. skillsaw here also closes `llm-context-lint-ci`.
- Fix the three relative links in `README.md` and the one in
  `docs/`, closing `readme-absolute-links` and
  `docs-external-links`.
- Align `@types/node` with whatever runtime D1 lands on, and declare
  `engines.node`. Today the package declares `^20` while the runner
  it is destined for would have had 18, which is how a type
  definition ends up describing an API the runtime lacks.

### 3. npm dependency criteria

Three checks per D4, following the shape
`docs/audits/README.md` and the template's worked brief describe: a
`Check` subclass with `id`, `spec` and `issue_title` as class
attributes, registered in `scripts/audit/registry.py`, a
specification page under `docs/audits/`, a line in
`docs/audits/README.md`, and tests covering pass, fail and
not-applicable.

They apply when `package.json` is present and report
`not_applicable` with a reason otherwise. Note that this repository
is inside its own audit matrix: these checks will run against
`development` too, find no `package.json`, and must report
not-applicable rather than failing.

### 4. Static runners gain node

**Do not execute without the operator's say-so; see D5.**

In `33fl/static_runner.yml`:

- Line 229, the disk specification in "Create the missing runner
  instances", moves from `@debian:12` to `@debian:13`.
- `nodejs` and `npm` join the base package list at approximately
  line 337.
- The comment at line 39, which documents the cached `debian:12`
  image as a precondition, is updated to say `debian:13`.

Three things to verify before proposing that change, none of which
this plan has checked:

- The playbook installs `yq` with `pip --break-system-packages`,
  installs docker through a shared `docker.yml`, and installs the
  claude CLI for the claude flavor. All three need confirming on
  trixie.
- Line 630 sets `--docker-image debian:12` for the GitLab docker
  executor. That is a different thing from the runner's own image
  and is deliberately left alone here, but somebody should decide
  whether it moves too.
- Whether `python3-venv` and the rest of the base list behave the
  same on trixie.

The rollout is gradual rather than a re-image: line 229 is inside
the loop over `missing_runners`, so it affects newly created
instances only, and the weekly retire and rebuild cycle replaces the
fleet over about a week. That is a feature -- a bad image shows up
on one runner rather than all of them -- but it means "landed" and
"rolled out" are a week apart, and phase 5 waits for the latter.

### 5. hunkydory CI and the fleet workflows

Once the runners have npm:

- `ci.yml` running `tools/check-node.sh` on `[self-hosted, static]`,
  with `npm_config_cache` pointed into `${{ runner.temp }}` so the
  shared `~/.npm` on a non-ephemeral runner is not written by a
  repository's build.
- `codeql-analysis.yml`, `export-repo-config.yml`, `renovate.yml`
  and `renovate.json`, `pr-re-review.yml`, `pr-retest.yml` from the
  fleet templates, closing `github-security`, `export-repo-config`,
  `renovate` and `ci-review-automation`.
- Secret scanning, push protection and delete-branch-on-merge
  enabled through the GitHub API, closing `github-security`'s
  remaining two findings and `delete-branch-on-merge`.

CodeQL supports JavaScript and TypeScript directly, so that workflow
is a language substitution rather than a new pattern.

### 6. Human review onboarding

Deploy the review tracking from `docs/code-review-tracking.md` so
the operator can work through the code: `.vscode/review-scope.toml`,
`tools/review-tracking.sh`, a `prune-reviews.yml` workflow, and a
generated `REVIEWS.md`. This turns `review-marks-pre-commit`,
`review-coverage` and `review-scope-completeness` from
not-applicable into real verdicts.

The scope config should cover `src/` and `test/`. The point of this
phase is that the operator has not read code written entirely by an
agent, and the review queue is how that gets fixed.

### 7. Marketplace release

**Blocked on the publisher account and `VSCE_PAT`; see D3.**

A `release.yml` that packages and publishes on a tag, and the
`RELEASE-SETUP.md` the fleet's release criterion expects. Note that
`release-process` as written measures a Python package and will stay
not applicable; whether it grows a TypeScript arm is future work
rather than part of this plan.

### 8. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of every phase against
`main`, per the shared block. Phases landing in `hunkydory` and
`33fl` record `<repo> <sha> (#pr)` in the `Merged` column and are
audited against those repositories' default branches as part of the
pull requests that land them; this phase cites those audits rather
than re-running them.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session, per the `subagent-execution-model` shared block
in `PLAN-TEMPLATE.md`. The management session plans, reviews the
actual files rather than the summary, and commits.

### Planning effort

Phase 3 is high effort: it changes what the fleet is measured
against, and `docs/consistency-audits.md` warns that a wrong
criterion files issues in ten repositories rather than producing a
red build. Phase 4 is high effort for the same reason turned
outward -- it changes the machine every static job runs on, in two
organisations. Phases 1, 2, 5, 6 and 7 are medium: they follow
patterns already worked out elsewhere in the fleet.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | medium | sonnet | none | Add `hunkydory` to the matrix in `.github/workflows/consistency-audit.yml` and to the in-scope list in `docs/audits/README.md`; add a `REPO_OVERRIDES` entry in `scripts/audit/repo.py` marking the six Python packaging criteria named in D4 not applicable with reasons. `audit/scope.py` parses all three statements and a test holds them to each other, so all three change together. |
| 2 | medium | sonnet | none | In hunkydory: add Biome with a `biome.json` set to 100 columns, single quotes, semicolons; write `tools/check-node.sh` mirroring ryll's `scripts/check-rust.sh`; add `.pre-commit-config.yaml` calling it as a `language: script` hook alongside shellcheck, gitleaks and skillsaw; fix four relative links; align `@types/node` and add `engines.node`. |
| 3 | high | opus | worktree | Add three `Check` subclasses for npm dependency auditing to `scripts/audit/checks/`, following the worked brief in `PLAN-TEMPLATE.md`. Register in `scripts/audit/registry.py`, write a spec page each under `docs/audits/`, add them to the index in `docs/audits/README.md`, and add tests covering pass, fail and not-applicable. They must report not-applicable with a reason where there is no `package.json`, including against this repository. |
| 4 | high | opus | worktree | **Hold.** See D5. |
| 5 | medium | sonnet | none | Copy the fleet workflow templates into hunkydory, substituting TypeScript for Python in CodeQL, and write `ci.yml` calling `tools/check-node.sh` on `[self-hosted, static]` with `npm_config_cache` under `runner.temp`. |
| 6 | medium | sonnet | none | Deploy review tracking per `docs/code-review-tracking.md`, scoped to `src/` and `test/`. |
| 7 | medium | sonnet | none | **Hold.** See D3. |
| 8 | high | opus | none | Run `PUSH-AUDIT.md` over the accumulated diff, citing the other repositories' audits. |

### Model choice

Per the `subagent-model-roster` shared block. Phases 3, 4 and 8 take
opus for the reasons in Planning effort; the rest are well-briefed
mechanical work where sonnet with the briefs above should succeed.

### Management session review checklist

Per the `plan-review-checklist` shared block, plus this
repository's own checks:

- [ ] `pre-commit run --all-files` passes.
- [ ] `python3 scripts/audit-check.py --repo-path . --repo-name
      development` still reports what it reported before the change,
      or this plan says why the verdict moved. Phase 3 is expected
      to add three not-applicable results here and nothing else.
- [ ] Issue filing was exercised with `--dry-run` only.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `scripts/audit-check.py` against `hunkydory` reports no failures.
* `scope-coverage` passes against `development` again.
* `pre-commit run --all-files` passes in both repositories.
* The three npm criteria have all four parts in step: the check and
  its registration, the specification under `docs/audits/`, the line
  in `docs/audits/README.md`, and tests.
* Every `REPO_OVERRIDES` entry added carries a stated reason.
* A push to `hunkydory` runs `npm ci` and its tests on a static
  runner without a node download.
* hunkydory appears on the VS Code Marketplace, or phase 7 records
  why it does not.
* The human review queue for hunkydory is live and the operator can
  work through `src/` and `test/`.

### Documentation index maintenance

One row added to `docs/plans/index.md`, dated 2026-09-11, linking
this plan, with a one-line intent and a status from the shared
vocabulary. The row carries the whole-plan status and reaches
`Complete` only once every phase has completed, been abandoned or
been superseded.

### Future work

* A second TypeScript repository is the point to generalise from:
  whether Biome stays the choice, whether `tools/check-node.sh`
  becomes a template under `templates/`, and whether the npm
  dependency criteria need to handle workspaces or monorepos.
* `release-process` measures a Python package. A TypeScript arm --
  or a language-neutral restatement -- is worth considering once
  there is more than one non-Python release to describe.
* The GitLab docker executor image at `33fl/static_runner.yml:630`
  is still `debian:12` after phase 4. Somebody should decide whether
  it follows.
* hunkydory's `test/corpus.ts` points by default at a sibling
  `kerbside-patches` checkout, so the corpus check's verdict depends
  on which branch that checkout happens to be on. It degrades
  gracefully and says so, but a fixture inside the repository would
  be better.
* `recount-patch.py` in `kerbside-patches` and hunkydory's
  `src/diff.ts` implement the same counting rules in two languages.
  They were verified to agree on 175 patches, but nothing keeps them
  agreeing.

### Bugs fixed during this work

Two patches in `kerbside-patches` were found to have hunk headers
that disagreed with their bodies while the counting rules were being
developed: `patch137-horizon-requires-setuptools.patch` was rejected
outright by `git apply` with "corrupt patch at line 25", and
`patch097-kolla-ansible-fixed-proxy-cert.patch` carried a trailing
context line git was silently ignoring. Neither was referenced by an
`ORDER` file. Both were corrected in
`shakenfist/kerbside-patches#1683`, which is where the recounter
itself landed.

No issue tracker entries in this repository relate to this plan;
`scope-coverage`'s failure against `development` is expected to
arrive as an audit-filed issue rather than a hand-written one, and
phase 1 closes it.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
