# Shaken Fist Project Consistency Plan

This document audits each Shaken Fist project against the criteria in
`PROJECT-CONSISTENCY-AUDITS.md` and lists the cleanups needed.

## Excluded projects

Per the "Exceptional cases" section of `PROJECT-CONSISTENCY-AUDITS.md`,
the following projects are excluded from these rules as they are
internal-only tooling or historical archive repositories:

- **actions** -- shared GitHub composite actions and Ansible playbooks
- **ansible-modules** -- historical archive
- **client-js** -- historical archive
- **client-go** -- historical archive
- **client-python-ova** -- historical archive
- **deploy** -- internal tooling
- **development** -- internal tooling
- **images** -- internal tooling
- **imago-testdata** -- test data repository
- **imago-testdata-quarantine** -- test data repository (malware samples)
- **jenkins-private** -- internal CI tooling
- **loadtest** -- internal tooling
- **occystrap-testdata** -- test data repository
- **ostrich** -- historical archive
- **performance** -- internal tooling
- **private-ci** -- internal CI tooling (legacy setup.py project)
- **reproducables** -- historical archive
- **sonobouy** -- historical archive
- **symbolicmode** -- historical archive
- **terraform-provider-shakenfist** -- historical archive
- **uefi-latency-guest** -- internal tooling
- **website** -- internal tooling

## Audit criteria

1. **LLM tooling**: `AGENTS.md`, `ARCHITECTURE.md`, and Claude skills
   for repetitive operations.
2. **Release process**: No `release.sh`, no `requirements.txt` /
   `test-requirements.txt`. Use `pyproject.toml`. If `pyproject.toml`
   exists, need `.github/workflows/release.yml` and `RELEASE-SETUP.md`.
   Use the templates in `templates/release-automation/` as the starting
   point.
3. **Claude Code automated review in CI**: Automated review job in CI
   workflow (runs after all other tests pass). The reviewer job needs
   `pull-requests: write` permission to post comments. Also need
   `.github/workflows/pr-re-review.yml` (with `pull-requests: write`
   and `issues: write`).
4. **Renovate**: `.github/workflows/renovate.yml` and `renovate.json`.
5. **Repo config export**: `.github/workflows/export-repo-config.yml`.
6. **GitHub CodeQL**: `.github/workflows/codeql-analysis.yml` for
   advanced security scanning. Reference:
   `occystrap/.github/workflows/codeql-analysis.yml`.
7. **Linting**: `actionlint`, `shellcheck`, and `.pre-commit-config.yaml`
   that runs them.
8. **Workflow permissions**: Every GitHub Actions workflow must have a
   top-level `permissions` block to restrict the default `GITHUB_TOKEN`
   scope. Workflows where every job only reads should use
   `permissions: contents: read`. Workflows with mixed needs should
   use `permissions: {}` at the top level and per-job overrides.
9. **Developer automation**: Workflow automations that respond to
   comments from authorized users. These include:
   - `pr-address-comments.yml` -- "@shakenfist-bot please address
     comments" triggers Claude Code to address review comments
   - `pr-re-review.yml` -- "@shakenfist-bot please re-review" triggers
     another automated review (already covered in criterion 3)
   - `pr-fix-tests.yml` + `test-drift-fix.yml` (optional) --
     "@shakenfist-bot please attempt to fix" triggers Claude Code
     to fix CI failures. Only suitable for projects with large test
     suites prone to drift (e.g. imago, occystrap). Use templates
     from `templates/test-drift-fix/`.
   Use templates from `templates/ci-review-automation/` for the
   core workflows.
10. **Workflow naming**: Workflow and job display names should be
    English sentences with correct capitalization (no kebab case).
    Prefer `self-hosted` runners; Claude Code jobs on `claude` runners;
    small non-mutating jobs on `self-hosted` `static` runners.

---

## DRY analysis: shared actions vs file copies

Before rolling these out, it is worth considering whether some of
these workflows should be consolidated into reusable workflows or
composite actions in the `actions/` repository.

### Candidates for reusable workflows

**`export-repo-config.yml`** (168 lines in shakenfist) -- Strong
candidate. The workflow is self-contained, uses `${{ github.repository }}`
which auto-resolves per repo, and needs no repo-specific parameters.
It could become a reusable workflow at
`actions/.github/workflows/export-repo-config.yml` that callers
invoke with:

```yaml
jobs:
  export-config:
    uses: shakenfist/actions/.github/workflows/export-repo-config.yml@main
    secrets: inherit
```

This eliminates 168 lines of duplication across 13 repos.

**`renovate.yml`** (20 lines) -- Marginal candidate. The only
difference per repo is `RENOVATE_AUTODISCOVER_FILTER`. Could become
a reusable workflow with an input parameter, but the file is so
small that copying is arguably simpler. Recommend: just copy it,
with each repo setting its own filter value.

### Candidates for composite actions

**PR review with Claude** -- The `tools/review-pr-with-claude.sh`
script (337 lines) is the biggest piece of duplicated logic. It is
currently in shakenfist's `tools/` directory, and every repo that
adds Claude review needs a copy. This could become a composite
action at `actions/review-pr-with-claude/action.yml` that:

1. Takes inputs: `pr-number`, `max-turns`, `force` (boolean)
2. Contains the review script inline or as a bundled script
3. Gets called from both the automated reviewer CI job and the
   `pr-re-review.yml` workflow

This would mean repos don't need their own copy of the 337-line
script. Their `pr-re-review.yml` would shrink to roughly:

```yaml
- name: Run automated reviewer
  uses: shakenfist/actions/review-pr-with-claude@main
  with:
    pr-number: ${{ github.event.issue.number }}
    force: true
```

### Recommend: just copy per repo

**`renovate.json`** -- Repo-specific dependency grouping rules.
Must be per-repo.

**`.pre-commit-config.yaml`** -- Could diverge per repo (e.g. Rust
repos need different hooks than Python repos). Keep per-repo.

**`pr-re-review.yml`** -- Even with a shared action for the review
logic, each repo still needs this workflow file to define the
trigger. It would be much simpler though (~20 lines vs 72).

**`pr-fix-tests.yml`** and **`pr-address-comments.yml`** -- These
developer automation workflows from imago should be moved to the
`actions/` repository as composite actions or reusable workflows,
then rolled out to all projects. This would standardize the bot
comment triggers across the organization.

**`codeql-analysis.yml`** -- Small workflow (~55 lines). CodeQL
auto-detects languages so the workflow is nearly identical across
repos. Simple copy.

### Recommendation

Build shared items in the `actions/` repository before rolling
out to individual projects:

1. **Reusable workflow**: `export-repo-config.yml` -- eliminates
   the most duplicated code (168 lines x 13 repos).
2. **Composite action**: `review-pr-with-claude` -- eliminates
   the 337-line review script from each repo and simplifies both
   the automated reviewer CI job and `pr-re-review.yml`.
3. **Developer automation actions**: Move `pr-fix-tests.yml` and
   `pr-address-comments.yml` logic from imago to shared actions,
   enabling consistent bot-triggered automations across all repos.

Then for each project, the per-repo files to add are:

- `renovate.yml` (20 lines, copy + edit filter)
- `renovate.json` (copy + edit package rules)
- `export-repo-config.yml` (3-5 lines calling reusable workflow)
- `pr-re-review.yml` (~20 lines calling shared action)
- `codeql-analysis.yml` (~55 lines, copy from occystrap)
- Automated reviewer job in CI (3-5 lines calling shared action)

### Tech debt: convert existing workflows to shared versions

The shared `export-repo-config` reusable workflow and
`review-pr-with-claude` composite action are now available in the
`actions/` repo. However, the following projects already have their
own inline copies of this logic and should be migrated to use the
shared versions:

- **shakenfist** -- has its own 168-line `export-repo-config.yml`
  and its own 337-line `tools/review-pr-with-claude.sh` plus inline
  `pr-re-review.yml`. These should be replaced with calls to the
  shared action/workflow.
- **imago** -- has its own `pr-re-review.yml` with inline review
  logic. Should be converted to use the shared composite action.
- **kerbside-patches** -- has Claude review logic in
  `daily-rebase-checks.yml`. Evaluate whether this can use the
  shared action.

This migration is not blocking but should be done to avoid drift
between the shared and inline copies.

---

## Per-project audit results

### agent-python

Python package with `pyproject.toml`. Has `functional-tests.yml` CI
workflow with Claude Code review integration and developer
automation.

**Needed cleanups:**

- [x] Add `AGENTS.md`
- [x] Add `ARCHITECTURE.md`
- [x] Remove `release.sh`
- [x] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [x] Add `RELEASE-SETUP.md`
- [x] Add Claude Code automated review to CI workflow
- [x] Add `.github/workflows/pr-re-review.yml`
- [x] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [x] Add top-level `permissions` to `functional-tests.yml`

**Not applicable:** `pr-fix-tests.yml` / `test-drift-fix.yml`
(small test suite, not prone to drift).

**Note:** `renovate.json` includes `constraints.python: ">=3.8"`
matching the oldest supported distro (Ubuntu 20.04). See
`ARCHITECTURE.md` for the supported platforms table.

### client-python

Python package with `pyproject.toml`. Has `functional-tests.yml` and
`code-formatting.yml` CI workflows but no Claude Code review
integration.

**Needed cleanups:**

- [ ] Add `AGENTS.md`
- [ ] Add `ARCHITECTURE.md`
- [ ] Remove `release.sh`
- [ ] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [ ] Add `RELEASE-SETUP.md`
- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [ ] Add top-level `permissions` to `code-formatting.yml`
- [ ] Add top-level `permissions` to `functional-tests.yml`

### clingwrap

Python package with `pyproject.toml`. Has `AGENTS.md` and
`ARCHITECTURE.md` already. Has `functional-tests.yml` CI workflow
but no Claude Code review integration.

**Needed cleanups:**

- [ ] Remove `release.sh`
- [ ] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [ ] Add `RELEASE-SETUP.md`
- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [ ] Add top-level `permissions` to `functional-tests.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`.

### cloudgood

Documentation and examples project. Not a Python package. Has
`AGENTS.md`, `ARCHITECTURE.md`, and `.pre-commit-config.yaml`. No
`.github/workflows/` directory.

**Needed cleanups:**

- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.pre-commit-config.yaml`.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a documentation project, not a Python package). Claude Code
automated review in CI may not apply as there are no CI test
workflows. `codeql-analysis.yml` not applicable (no source code to
scan).

### imago

Rust project (Cargo.toml in `src/`). Has `AGENTS.md`,
`ARCHITECTURE.md`, `.claude/skills/` (5 skills),
`.pre-commit-config.yaml`, `pr-re-review.yml`, `pr-fix-tests.yml`,
`pr-address-comments.yml`, and Claude Code review in CI
(`functional-tests.yml`). Well-configured overall with full developer
automation.

**Needed cleanups:**

- [x] Add `.github/workflows/renovate.yml` and `renovate.json`
- [x] Add `.github/workflows/export-repo-config.yml`
- [x] Add `.github/workflows/codeql-analysis.yml`
- [x] Add top-level `permissions` to `pr-re-review.yml`
- [x] Add top-level `permissions` to `functional-tests.yml`
- [x] Add top-level `permissions` to `test-drift-fix.yml`

**Already compliant:** All criteria. `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/`, `.pre-commit-config.yaml`, `renovate.yml`,
`renovate.json`, `export-repo-config.yml`, `codeql-analysis.yml`,
`pr-re-review.yml`, `pr-fix-tests.yml`, `pr-address-comments.yml`,
`pr-retest.yml`, Claude Code automated review in CI. All workflows
have top-level `permissions` blocks.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a Rust project, not a Python package released to pypi).

### kerbside-patches

Non-Python project containing CI patches. Has `AGENTS.md`,
`ARCHITECTURE.md`, `.claude/skills/` (4 skills),
`.pre-commit-config.yaml`. Has CI workflows including Claude Code
integration in `daily-rebase-checks.yml` for automated rebasing.
Missing developer automation workflows.

**Needed cleanups:**

- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add top-level `permissions` to `auto-retry-infra-failures.yml`
- [ ] Add top-level `permissions` to `daily-rebase-checks.yml`
- [ ] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add top-level `permissions` to `local-container-builds.yml`
- [ ] Add top-level `permissions` to `rebase-tests.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/`, `.pre-commit-config.yaml`, Claude Code
integration (for rebasing).

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is not a Python package).

### kerbside

Python package with `pyproject.toml`. Has `AGENTS.md`,
`ARCHITECTURE.md`, `release.yml`, and `RELEASE-SETUP.md`. One of
the reference projects mentioned in the audit document. Has
`functional-tests.yml` and `pin-indirect-dependencies.yml` CI
workflows but no Claude Code review integration.

**Needed cleanups:**

- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`
- [ ] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add top-level `permissions` to `pin-indirect-dependencies.yml`
- [ ] Add top-level `permissions` to `release.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`.

**Note:** The audit document references kerbside as an example for
linting setup, but kerbside itself is currently missing
`.pre-commit-config.yaml`. This should be addressed.

### library-utilities

Python package with `pyproject.toml`. No `.github/workflows/`
directory at all. Has `release.sh` that should be removed.

**Needed cleanups:**

- [ ] Add `AGENTS.md`
- [ ] Add `ARCHITECTURE.md`
- [ ] Remove `release.sh`
- [ ] Add `.github/workflows/release.yml` (has `pyproject.toml`)
- [ ] Add `RELEASE-SETUP.md`
- [ ] Add CI workflow with Claude Code automated review
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`
- [ ] Add `.pre-commit-config.yaml` with `actionlint` and `shellcheck`

### occystrap

Python package with `pyproject.toml`. Has `AGENTS.md`,
`ARCHITECTURE.md`, `release.yml`, `RELEASE-SETUP.md`,
`.pre-commit-config.yaml`, `renovate.yml`, `renovate.json`,
`export-repo-config.yml`, `codeql-analysis.yml`, `pr-re-review.yml`,
and Claude Code automated review in `functional-tests.yml`.
All workflows have proper top-level `permissions` blocks.

**Needed cleanups:**

- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)

**Already compliant:** All criteria except developer automation
(`pr-fix-tests.yml` and `pr-address-comments.yml`).

### ryll

Rust project with `Cargo.toml` at root. Has `AGENTS.md`,
`ARCHITECTURE.md`, and `.pre-commit-config.yaml`. No
`.github/workflows/` directory at all.

**Needed cleanups:**

- [ ] Add `.github/workflows/` directory with CI workflow
- [ ] Add Claude Code automated review to CI workflow
- [ ] Add `.github/workflows/pr-re-review.yml`
- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add `.github/workflows/renovate.yml` and `renovate.json`
- [ ] Add `.github/workflows/export-repo-config.yml`
- [ ] Add `.github/workflows/codeql-analysis.yml`

**Already compliant:** `AGENTS.md`, `ARCHITECTURE.md`,
`.pre-commit-config.yaml`.

**Not applicable:** `pyproject.toml`, `release.yml`, `RELEASE-SETUP.md`
(this is a Rust project, not a Python package released to pypi).

### shakenfist

The reference project. Has `AGENTS.md`, `ARCHITECTURE.md`,
`.claude/skills/` (3 skills), `pyproject.toml`, `release.yml`,
`RELEASE-SETUP.md`, `pr-re-review.yml`, `renovate.yml`,
`renovate.json`, `export-repo-config.yml`,
`.pre-commit-config.yaml`, `codeql-analysis.yml`, and Claude Code
automated review in `functional-tests.yml`.

**Needed cleanups:**

- [ ] Add `.github/workflows/pr-fix-tests.yml` (developer automation)
- [ ] Add `.github/workflows/pr-address-comments.yml` (developer automation)
- [ ] Add top-level `permissions` to `ci-dependencies.yml`
- [ ] Add top-level `permissions` to `ci-images.yml`
- [ ] Add top-level `permissions` to `ci-images-test.yml`
- [ ] Add top-level `permissions` to `code-formatting.yml`
- [ ] Add top-level `permissions` to `codeql-analysis.yml`
- [ ] Add top-level `permissions` to `docs-tests.yml`
- [ ] Add top-level `permissions` to `export-repo-config.yml`
- [ ] Add top-level `permissions` to `functional-tests.yml`
- [ ] Add top-level `permissions` to `functional-tests-skip.yml`
- [ ] Add top-level `permissions` to `pin-indirect-dependencies.yml`
- [ ] Add top-level `permissions` to `pr-re-review.yml`
- [ ] Add top-level `permissions` to `publish-website.yml`
- [ ] Add top-level `permissions` to `refresh-website.yml`
- [ ] Add top-level `permissions` to `release.yml`
- [ ] Add top-level `permissions` to `renovate.yml`
- [ ] Add top-level `permissions` to `scheduled-tests.yml`
- [ ] Add top-level `permissions` to `sync-external-docs.yml`

**Already compliant:** All other criteria except developer automation
and workflow permissions.

---

## Summary

### Fully compliant projects

- **imago** -- fully compliant with all criteria including full
  developer automation (`pr-fix-tests.yml`, `pr-address-comments.yml`,
  `pr-retest.yml`).
- **occystrap** -- missing only developer automation workflows
  (`pr-fix-tests.yml`, `pr-address-comments.yml`).

### Nearly compliant projects (1-3 items)

None.

### Partially compliant projects (4-6 items)

- **cloudgood** -- needs export-repo-config, renovate,
  pr-re-review, and developer automation (5 items). No workflows
  to add permissions to.

### Major work needed (7+ items)

- **ryll** -- needs entire `.github/workflows/` directory, renovate,
  export-repo-config, pr-re-review, developer automation, CodeQL,
  and CI workflows for Claude review (8 items).
- **kerbside-patches** -- needs pr-re-review, developer
  automation, renovate, export-repo-config, CodeQL, plus top-level
  `permissions` on all 5 workflows (11 items).
- **kerbside** -- needs Claude review, pr-re-review, developer
  automation, renovate, export-repo-config, CodeQL, pre-commit,
  plus top-level `permissions` on 3 workflows (11 items).
- **clingwrap** -- 12 items (has AGENTS.md/ARCHITECTURE.md but
  missing most CI/release infrastructure including developer
  automation, plus permissions).
- **agent-python** -- 3 items remaining (needs
  export-repo-config, CodeQL, and pre-commit).
- **client-python** -- 15 items (missing nearly everything
  including developer automation, plus permissions on 2 workflows).
- **library-utilities** -- 13 items (missing nearly everything
  including developer automation, no workflows directory).
- **shakenfist** -- needs developer automation plus top-level
  `permissions` on 17 workflows (19 items). Otherwise compliant.

### Most common missing items across non-excluded projects

| Item                                | Missing in  |
|-------------------------------------|-------------|
| Developer automation (pr-fix-tests, pr-address-comments) | 10 projects |
| Workflow top-level `permissions`    | 7 projects  |
| `renovate.yml` + `renovate.json`   | 8 projects  |
| `export-repo-config.yml`           | 8 projects  |
| `codeql-analysis.yml`              | 8 projects  |
| `pr-re-review.yml`                 | 8 projects  |
| Claude Code automated review in CI | 7 projects  |
| `.pre-commit-config.yaml`          | 4 projects  |
| `AGENTS.md`                        | 3 projects  |
| `ARCHITECTURE.md`                  | 3 projects  |
| `release.sh` removal               | 4 projects  |
| `release.yml`                      | 5 projects  |
| `RELEASE-SETUP.md`                 | 5 projects  |

Note: The 7 projects missing workflow permissions account for 35
individual workflow files that need top-level `permissions` blocks
added. Shakenfist alone accounts for 17 of these.

Note: Developer automation includes `pr-fix-tests.yml` and
`pr-address-comments.yml`. Only imago currently has these
workflows.

### Suggested approach

1. **Build shared infrastructure first** (in `actions/` repo):
   a. ~~Create reusable workflow for `export-repo-config`~~ DONE
   b. ~~Create composite action for `review-pr-with-claude`~~ DONE
   c. Move developer automation workflows (`pr-fix-tests.yml`,
      `pr-address-comments.yml`) from imago to shared actions

2. **Quick wins** (simple file copies/additions per repo):
   a. Add `renovate.yml` + `renovate.json` to remaining 9 repos
   b. Add `export-repo-config.yml` caller to remaining 9 repos
   c. Add `pr-re-review.yml` to remaining 8 repos
   d. Add `codeql-analysis.yml` to remaining 9 repos (copy from
      occystrap; CodeQL auto-detects languages so the workflow is
      nearly identical everywhere, except non-code repos like
      cloudgood where it's N/A)

3. **Developer automation**: Roll out `pr-fix-tests.yml` and
   `pr-address-comments.yml` to remaining 10 repos (using shared
   actions once available).

4. **Pre-commit configs**: Add `.pre-commit-config.yaml` to the 4
   projects missing it.

5. **AGENTS.md and ARCHITECTURE.md**: Create these for the 3
   non-excluded projects missing them (requires understanding each
   project).

6. **Release infrastructure**: Add `release.yml` and `RELEASE-SETUP.md`
   to the 5 Python projects missing them (use the templates in
   `templates/release-automation/`), and remove `release.sh` from
   the 4 remaining projects that still have it.

7. **Claude Code automated review**: Add to the 7 remaining CI
   workflows missing it (using the shared composite action).

8. **Workflow permissions**: Add top-level `permissions` blocks to
   all 38 workflow files across 8 projects. Most read-only workflows
   need `permissions: contents: read`. Workflows with mixed needs
   (e.g. `release.yml`, `codeql-analysis.yml`) should use
   `permissions: {}` at the top level with per-job overrides.
   This is a high-priority security item flagged by GitHub Advanced
   Security.
