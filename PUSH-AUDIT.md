Thanks for your work on this. I appreciate it. Some final
checks before I push.

## How to use this runbook

This repository is not a service. It is the automation that
audits sixteen other repositories, the specifications that
automation implements, and the workflow templates the fleet
copies. A defect here does not break a running system; it
breaks other repositories quietly, or files a hundred wrong
issues at 06:00 UTC. The briefs below are written for that
blast radius rather than for a product.

The audit splits into two waves:

**Wave 1 -- mechanical.** Lint and the four test suites, then
the grep-level checks on the diff. Always run wave 1 first;
wave 2 is only worth spending on if wave 1 passes.

**Wave 2 -- judgment.** Four independent sub-agents that read
code and apply judgment. They can be spawned in parallel.

The management session reviews all findings, fixes any issues,
and confirms the push.

The default branch is `main`, so every diff command below is
against `main...HEAD`.

## Wave 1: Mechanical checks

```
pre-commit run --all-files
```

That one command is the whole of lint and test here:
actionlint over `.github/workflows/` and over the shipped
templates, shellcheck over `scripts/` and `tools/`, flake8,
skillsaw, and four Python test suites. `ci.yml` runs the same
command on every pull request, so a clean local run is what
makes the CI run boring.

Then the grep-level checks on the diff:

```
# Lines over 120 characters in new Python
git diff main...HEAD -- '*.py' | grep -nE '^\+[^+].{120,}'

# New third-party imports -- the audit scripts are stdlib plus
# the git and gh CLIs only, which is why they run on a bare runner
git diff main...HEAD -- 'scripts/*.py' | grep -nE '^\+import |^\+from '

# Hand-edited compliance tables. These are regenerated and pushed
# by the daily workflow; an edit between the markers is reverted
# tomorrow morning and confuses whoever reads it today
git diff main...HEAD -- 'docs/audits/*.md' | \
    grep -nE '^\+.*consistency-audit:(begin|end)|^\+\| .* \| (compliant|non-compliant|N/A) \|'

# Changes to the issue-title interface. ISSUE_TITLES is the
# idempotency key for filing and closing: renaming an entry
# orphans every open issue for that check, fleet-wide
git diff main...HEAD -- 'scripts/audit_common.py' | \
    grep -nE '^[-+].*ISSUE_TITLES|^-\s+'\''[a-z-]+'\'':'

# A shared block edited without its version bumped. Editing the
# wording without the bump means every embedding repository keeps
# the old text and the audit never notices
git diff main...HEAD -- 'templates/shared-blocks/*.md' --name-only
git diff main...HEAD -- 'templates/shared-blocks/*.md' | \
    grep -nE '^\+<!-- shared-block: '

# TODO / FIXME / HACK / XXX, and new suppressions
git diff main...HEAD -- '*.py' | \
    grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'
git diff main...HEAD -- '*.py' | \
    grep -nE '^\+.*(# noqa|# type: ignore)'

# Documentation touched at all (warns if none)
git diff main...HEAD --name-only -- 'docs/*' '*.md'
```

Exit condition: wave 1 passes when `pre-commit` is clean and
each grep has either no hits or hits the management session has
looked at and accepted. The greps report; they do not block.

If the diff touches `templates/shared-blocks/`, confirm the
version bump is deliberate before going further. Bumping a block
marks every repository carrying the old version non-compliant on
the next daily run and files issues automatically -- that is the
mechanism working, but it should be a decision rather than a
side effect.

If the diff adds or removes a file matched by
`.vscode/review-scope.toml`, `REVIEWS.md` must have been
regenerated (`python3 scripts/review-tracking.py regen`) and the
result committed. If the diff edits a file carrying a review
mark, that mark is stale: run `prune` and say so in the PR. Never
re-stamp -- the mark attests that a person read that exact
content.

## Wave 2: Deeper review

Only run wave 2 after wave 1 passes.

### 2a. Code quality

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

The mechanical sweep has already extracted TODO/FIXME comments,
new suppressions, and third-party imports. Take that report as
input, and triage each: blocking or advisory, and why.

Then the judgment-level review of `git diff main...HEAD`:

- **The four-file rule.** A consistency criterion spans a check
  function in `scripts/audit-check.py`, metadata in
  `scripts/audit_common.py` (`AUDIT_METADATA` and `ISSUE_TITLES`),
  a spec in `docs/audits/<name>.md`, and a row in the index in
  `docs/audits/README.md` -- plus a column heading where a spec
  file carries more than one check. A new or renamed check that
  updates three of the four is the characteristic defect here.
  The tests catch most of it; say which file the diff missed.
- **`not_applicable` discipline.** A check that cannot apply to a
  repository must return `not_applicable` with a reason. Omitting
  it renders as `unknown`, which reads as a broken audit rather
  than an inapplicable one.
- **Blast radius of a changed check.** Does a modified check
  change its verdict for repositories the diff is not about? A
  stricter regex or a new required file marks the fleet
  non-compliant tomorrow. If that is intended, the plan or PR
  should say how many repositories it will newly fail; if it is
  not, it is a bug.
- **Repo overrides.** New entries in `REPO_OVERRIDES` need a
  stated reason. An override is a decision that a rule does not
  apply, and an unexplained one is indistinguishable from
  silencing a real finding.
- **Templates are shipped code.** Anything under `templates/` is
  copied into other repositories. Judge it as their code, not as
  ours: placeholders consistent, no reference to paths that only
  exist here, and the README beside it saying what to substitute.
- **Prose that is parsed.** Some documentation is read by tests
  (`AuditScopeIsStatedOnceTest` splits `docs/audits/README.md` on
  literal phrases). A new parse of a document by phrase must use
  a named constant and an assertion, not a bare `split()`.
- **Duplicated logic.** `audit-check.py` is 5,000 lines of check
  functions that resemble each other by design. Flag duplication
  only where a helper already exists and was not used.

<!-- shared-block: comment-proportion v1 -->
Comment proportion (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/comment-proportion.md`):

- A comment or docstring earns its length by saying what the code
  cannot: the contract, the units, the failure modes, the reason a
  surprising choice is correct. Restating the code in prose is not
  documentation.
- Treat as candidates any added comment or docstring that is longer
  than the code it documents, and any comment block over roughly
  fifteen lines attached to a body under ten. These are candidates,
  not verdicts -- a subtle algorithm, a public API contract, or a
  hard-won bug explanation can justify the length.
- Where the length is not justified the finding is advisory, and
  the fix is to cut the restatement rather than delete the comment:
  keep the why, drop the line-by-line narration of the what.
- Prose that documents user-visible behaviour rather than the
  implementation usually belongs in `docs/`, with the comment
  reduced to a pointer.
<!-- shared-block-end -->

<!-- shared-block: python-version-discipline v1 -->
Python version and typing (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/python-version-discipline.md`):

- No syntax or standard library API newer than the floor in
  `requires-python`. Structural pattern matching, `X | Y` unions in
  annotations evaluated at runtime, `tomllib`, and
  `datetime.UTC` each raise on an interpreter the package still
  claims to support, and none of them fail in CI when CI runs only
  the newest version. This is the finding to look for first: it is
  a real break on a real user's machine, not a style point.
- New and modified code carries type hints, and mypy is expected to
  be clean over it. A project part way through a staged rollout is
  held to the new code, not to the whole tree.
- Prefer the walrus operator and f-strings where they make the code
  read better, subject to the floor above.
- Raising the floor in `requires-python` is a supported-platforms
  decision, not a convenience: it drops users. If it is genuinely
  right, the platforms table, `requires-python` and
  `constraints.python` in `renovate.json` all move together.
<!-- shared-block-end -->

Report findings as a bullet list. For each, state the file, line,
and whether it is blocking or advisory.

### 2b. Test review

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

Review `git diff main...HEAD` for test coverage. The four suites
are `test_audit_check.py`, `test_audit_update_docs.py`,
`test_review_tracking.py` and `test_check_audit_smoke.py`, all
stdlib `unittest`, all run by `pre-commit`.

- Does every new or modified check function have cases for each
  status it can return -- `pass`, `fail`, and `not_applicable`?
  A check with no `not_applicable` case is the usual gap.
- Does a new check have a case proving it *fails* the thing it
  is meant to catch? A test that only asserts the happy path
  passes when the check is a no-op.
- Are the cross-file sync tests still meaningful after the
  change, or did the diff add a file the sync test does not know
  to look at?
- Does anything new parse prose out of a document without a test
  asserting the phrase it splits on still exists?
- Tests here build fixture repositories in `tempfile`
  directories. Does any new test reach outside its fixture --
  reading the real repository, calling `git` or `gh`, hitting the
  network? Those pass locally and fail on a bare runner.
- Are there assertions on exact rendered strings where the
  behaviour under test is the status? Those break on wording
  changes and teach people to update tests without reading them.

<!-- shared-block: functional-test-coverage v1 -->
Functional test coverage (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/functional-test-coverage.md`):

- The standard is "do we run the code to do the real thing, and
  does it work as intended". Every subcommand exposed on the command
  line, and every endpoint exposed by an API, should have a test
  that exercises it for real rather than against a mock of itself.
- For a change that adds or alters user-visible behaviour, the
  question to answer is which functional test would have failed
  before it and passes after. If there is none, that is the finding,
  and it is a finding about this change rather than a note for
  later.
- Unit tests are held to no coverage percentage, but a branch that
  is reachable from outside the process and has no test is worth
  naming. Error paths and argument validation are where this bites:
  they are the code most often written once and never run again.
- Mocking the system under test proves nothing. Mock the boundary --
  the network, the clock, the hypervisor -- and let the code being
  tested actually run.
- Where a gap is real but out of scope for the change in hand, say
  so plainly and record it, rather than silently widening the
  change or silently leaving it unsaid.
<!-- shared-block-end -->

Report findings as a bullet list grouped by file.

### 2c. Documentation review

| Setting | Value |
|---------|-------|
| Model | sonnet |
| Effort | medium |

**Brief for sub-agent:**

Check that documentation matches the current code state. Read
`git diff main...HEAD` and verify:

<!-- shared-block: readme-discipline v1 -->
README discipline (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/readme-discipline.md`):

- New user-visible features are documented in `docs/` (and
  `ARCHITECTURE.md` / `AGENTS.md` where appropriate), not by
  adding bullets to `README.md`.
- `README.md` is a pitch: what the project is, who it is for,
  minimal installation instructions, a small number of usage
  examples, and curated absolute links into `docs/`. It only
  changes when the pitch, the install story, or the
  documentation links change.
- README growth is itself a finding: if the diff adds README
  content that belongs in `docs/`, flag it as blocking and
  move it.
<!-- shared-block-end -->

<!-- shared-block: llm-doc-discipline v1 -->
AGENTS.md and ARCHITECTURE.md discipline (shared block; do not
edit -- the canonical copy lives in shakenfist/development at
`templates/shared-blocks/llm-doc-discipline.md`):

- `AGENTS.md` is a working guide: the conventions, invariants and
  gotchas an agent cannot infer by reading the code, plus curated
  links into `docs/`. It is loaded into every session, so every
  line costs context on every task.
- `ARCHITECTURE.md` is a map: the component inventory, how data
  moves between components, and why the shape is the way it is.
  A deep dive on one subsystem belongs in `docs/`, where humans
  benefit from it too.
- One canonical home per fact. If `docs/` covers it, link to it
  instead of restating it -- and the same rule applies between
  `AGENTS.md` and `ARCHITECTURE.md`.
- Neither file is a reference manual, a runbook, or a changelog.
  CLI flags, configuration keys, wire protocols, step-by-step
  procedures and plan history go to `docs/`.
- Growth in either file is itself a finding: if the diff adds
  content that belongs in `docs/`, flag it as blocking and move
  it.
<!-- shared-block-end -->

- In this project, the structure that reaches `ARCHITECTURE.md`
  is the shape of the audit pipeline and the review-tracking
  system -- new scripts, new workflows, new data files and how
  they flow. The conventions that reach `AGENTS.md` are the
  invariants an agent cannot infer: the four-file rule, generated
  files that must not be hand-edited, `ISSUE_TITLES` as an
  interface, `--dry-run`. Neither is the place for a criterion's
  details; those go in `docs/audits/<name>.md`.
- A new or changed criterion is documented in its spec file, and
  the spec says both what is checked and *why*, including what
  was rejected. The specs are read by people fixing a
  non-compliant repository, so "how to fix it" is not optional.
- `docs/consistency-audits.md` is the reference for how the audit
  runs. A change to the run itself -- scheduling, scope,
  issue handling, how to test a change -- belongs there.
- Generated content is not edited by hand: the compliance tables
  between the `consistency-audit` markers in `docs/audits/*.md`,
  and `REVIEWS.md`.

<!-- shared-block: plan-phase-references v1 -->
Plan phase references (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-phase-references.md`):

- Documentation outside plans directories describes the current
  state of the software, not the history of how it was built. Do
  not write "implemented in phase 5" or "since phase 3 of the
  two-tier CI plan": a reader wants to know whether a feature
  exists, not which phase of which plan delivered it.
- If a documented behaviour is implemented, describe it plainly.
  If it is planned but not yet implemented, link to the master
  plan in `docs/plans/` instead of citing a phase number.
- Reserve the word "phase" for plan documents. A procedural
  document describing a live multi-stage process (a release
  runbook, say) should call its stages "steps" or "stages", so
  that a phase reference in `docs/` is always a plan smell.
- The consistency audit greps `README.md` and `docs/` (excluding
  plans directories) for "phase <number>". Append
  `<!-- audit-ok: phase-reference -->` to a line only when the
  reference is genuinely not about an implementation plan.
<!-- shared-block-end -->

- Plan files in `docs/plans/` are up to date -- the Execution
  table reflects reality, deferred items are recorded rather than
  dropped, and the row in `docs/plans/index.md` matches. In this
  repository plans keep their phases as sections inside the
  master plan rather than as separate phase files.

Report findings as a bullet list. "No documentation gaps found"
is a valid answer.

### 2d. Security review

| Setting | Value |
|---------|-------|
| Model | opus |
| Effort | high |

**Brief for sub-agent:**

Security review of `git diff main...HEAD`. Read the actual code,
not just the diff summary.

The threat model here is unusual and worth stating: this
repository has no users and holds no data. What it has is write
access to sixteen repositories, a `GITHUB_TOKEN` in workflows
triggered by comments from outside, and templates that other
projects run verbatim. The interesting vulnerabilities are ones
that reach out of this repository.

- **Privileged workflow triggers.** `pr-re-review.yml` and
  `pr-retest.yml` run on `issue_comment`, which executes with the
  base repository's permissions. Verify authorisation is enforced
  before anything else runs, that scripts are checked out from
  the base branch and never from the PR, that
  `persist-credentials: false` holds, that `core.hooksPath` is
  neutered, and that pre-commit is not run in the privileged
  context. Any new step that runs PR-controlled content is a
  critical finding.
- **Injection into shell and into prompts.** PR titles, branch
  names, comment bodies and issue titles are attacker-controlled.
  Are any interpolated into a shell command, a `gh` invocation,
  or an LLM prompt without being passed as data? The existing
  reviewer substitutes via Python for exactly this reason.
- **Token scope and leakage.** Does any new workflow widen
  `permissions:`? Is a token echoed, written to a file, passed to
  a script as an argument (visible in `ps`), or exposed to a
  step that runs untrusted content? Is `gh auth setup-git` scoped
  to the push rather than the whole job?
- **Templates ship to the fleet.** A template with a weak trigger
  or a broad `permissions:` block propagates that weakness into
  every repository that adopts it. Review anything under
  `templates/` as though it were being merged into all of them,
  because it is.
- **The issue-filing path.** `audit-manage-issues.py` creates,
  updates and closes issues fleet-wide, keyed on title. Could a
  change cause it to close issues it did not open, file
  duplicates at scale, or write attacker-controlled text into an
  issue body? A bug here is a fleet-wide mess, not a local one.
- **Self-hosted runners.** Jobs run on self-hosted runners
  declared in `.github/actionlint.yaml`. Does any new job run
  untrusted code there, or leave state behind between runs?
- **Secret scanning.** Does the diff add anything that looks like
  a credential, or weaken `secret-scan.yml`?

Report findings with severity (critical / high / medium / low /
informational). For each, state the file, line, the vulnerability
class, and a recommended fix.

<!-- shared-block: path-traversal-review v1 -->
Path construction from outside data (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/path-traversal-review.md`):

- Treat as a candidate any filesystem path built from a value the
  process did not choose: a request parameter, an image name, tag or
  digest, a layer path, an archive member name, a filename out of a
  configuration file or a database row.
- The question is not whether the value looks dangerous but whether
  the resulting path is *proved* to stay inside its intended base
  directory. Resolve the joined path with `os.path.realpath()` and
  verify it still starts with the base; a check on the untrusted
  component alone is defeated by symlinks and by encodings the
  check did not anticipate.
- Prefer a helper that cannot be forgotten at a call site --
  `safe_path_join()` in occystrap, or the framework's own
  (`send_from_directory` in Flask) -- over an inline guard repeated
  at each join.
- Archive extraction is the case most often missed: a member name
  inside a tarball or zip is attacker-controlled in exactly the same
  way as a request parameter.
- Where a bare join is correct because every component is
  process-chosen, say so in a comment rather than leaving the
  reader to re-derive it.
<!-- shared-block-end -->

## Management session checklist

After all agents complete:

- [ ] Wave 1 passed (`pre-commit run --all-files` clean, greps
      reviewed).
- [ ] Wave 2 findings reviewed.
- [ ] Any blocking findings from 2a/2b/2c fixed and re-verified.
- [ ] Security findings assessed -- critical and high must be
      fixed before push.
- [ ] Any shared-block version bump was deliberate, and the
      fleet-wide consequence is understood.
- [ ] Generated files are generated, not hand-edited:
      compliance tables and `REVIEWS.md`.
- [ ] Stale review marks pruned, and said so.
- [ ] Commit history is clean -- no fixups that should be
      squashed, no accidental files, no WIP messages.
- [ ] Branch is up to date with `main`.
- [ ] Ready to push.
