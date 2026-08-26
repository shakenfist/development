# Plan: split the generated compliance tables out of the audit specifications

## Context

`docs/audits/` holds 35 criterion specifications plus an index. Each
specification is hand-written prose -- what we check, why the rule
exists, what it deliberately does not cover, which template
implements it -- and 34 of the 35 also carry a machine-regenerated
per-project compliance table between `<!-- consistency-audit:begin
-->` and `<!-- consistency-audit:end -->` markers. `test-coverage.md`
is the exception: its criterion is delegated to the pre-push review
and has no check.

The generated block opens with a line that moves on every run:

```
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*
```

That timestamp is deliberate. It is how a reader tells a current
verdict from a stale one when the audit has silently stopped running,
and `.github/workflows/consistency-audit.yml` files an issue on a
failed run saying exactly that: "the tables still show the previous
run's verdicts, so the audit looks healthy from the outside".

But a whole-file review mark attests to file content by blob SHA, and
a file carrying a line that changes daily can never hold one. So
`.vscode/review-scope.toml` excludes the whole directory bar the
index:

```toml
exclude = [
    'docs/plans/*',
    'PLAN-*.md',  # unmatched-by-design: a guard against plans returning to the root
    'docs/audits/*',
    '!docs/audits/README.md',
]
```

The cost of that exclusion, measured:

| | Lines |
|---|---|
| The 35 specification files | 3,466 |
| Inside `consistency-audit` markers (generated) | 947 |
| Hand-written prose | 2,519 |

**27% of the directory is generated, and it is why the other 73% has
never been reviewable.** Those 2,519 lines are the statement of what
the fleet is held to -- the most consequential prose in the
repository after `docs/audits/README.md` and
`docs/consistency-audits.md` -- and human review cannot see any of
it. Mikal found this while trying to review it.

The exclusion was the right call when it was written; the comment
above it reasons the trade-off out at length and correctly concludes
that including the files as they stand would put permanently-stale
entries in the queue and hold a `review-coverage` issue open that no
amount of reviewing could close. The mistake is not the exclusion. It
is that two artifacts with opposite lifecycles -- prose that changes
when someone decides something, and a table that changes every
morning -- were put in one file, so the file inherits the churn of
its worst part.

## What "good" looks like

* Every criterion specification is hand-written from the first line
  to the last, changes only when a human changes it, and can hold a
  review mark.
* Per-project compliance is still published on shakenfist.com, still
  regenerated every morning, still carries the staleness timestamp.
* The daily bot commit touches one file, not 27.
* Nothing in the fleet has to learn a new format or a new location it
  cannot discover from the page it is already reading.

## Decisions

### D1. Rendered markdown, not a JSON sidecar

The obvious move -- and the one that prompted this plan -- is to put
each table in `docs/audits/<check-id>.json` beside its markdown.
Rejected, for one reason.

`docs/audits/README.md` justifies the directory's location this way:

> It sits under `docs/` so that it publishes to shakenfist.com with
> everything else. What we hold a project to is documentation, and a
> criterion nobody outside the fleet can read is a criterion nobody
> outside the fleet can meet.

A JSON file renders as nothing. Today a reader of the published
`export-repo-config` page sees which projects comply and which issue
tracks each failure; with the data in JSON, either the compliance
information leaves the published documentation entirely, or the
website repository grows a renderer for it. The website is
*excluded* from the consistency audits, so that would put a schema
contract across a repository boundary with nothing checking either
side of it -- and the format would then be load-bearing for
publishing rather than an implementation detail.

The generated output stays markdown. What changes is which file it
lives in.

There is no loss here. The machine-readable form already exists:
every run uploads `audit-result-<repo>.json` per repository as a
workflow artifact, and `audit-manage-issues.py` consumes those
directly. A committed JSON copy would be a third representation of
the same data with no reader. If one is ever wanted, see Future work.

### D2. One page, not 34 generated files

`docs/audits/compliance.md`, one page, one section per specification.
The alternative -- 34 generated markdown files under
`docs/audits/generated/` -- preserves one-page-per-criterion and
needs a single exclusion glob, but it does not fix the churn: the bot
would still rewrite 27 files most mornings, which is what makes the
git log of this repository hard to read.

One page also has a per-run timestamp problem that resolves in its
favour. All 34 blocks currently carry the *same* timestamp -- 34
copies of one string, because `render_section` takes the maximum
timestamp across the whole result set and every spec sees the same
set. One page needs one note at the top, and nothing is lost.

The cost is one click from a specification to its compliance table
when you want both. Accepted, in exchange for a page that answers
"what is the fleet failing right now" in one place, which nothing
answers today.

### D3. What replaces the marker block in a specification

The `## Projects` section stays, and becomes a static line:

```markdown
## Projects

Per-project compliance for this criterion is regenerated every
morning by the consistency audit:
[compliance.md#export-repo-config](compliance.md#export-repo-config).
```

Static, hand-written, reviewable, and it keeps the specification a
complete answer to "where do I find out who is failing this". The
anchor is the specification's basename, so it is derivable from the
filename and a test can assert every one of them resolves.

### D4. The new tell for "this criterion has no check"

`docs/consistency-audits.md` currently says marker-block absence is
how to find criteria with no automated check:

> A criterion with no check has no `consistency-audit` marker block in
> its spec file, which is how to find the current set -- at the time
> of writing, `test-coverage`.

With the markers gone from every specification that tell dies, and it
has to be replaced rather than dropped: it is the only thing in the
documentation that distinguishes "this rule is measured" from "this
rule is written down and judged by a human".

The replacement is better than what it replaces, because it is
visible to a reader rather than requiring a grep.
`compliance.md` grows a closing section naming the criteria that have
no check and why, and a specification with no check says so where its
compliance link would be. `AUDIT_METADATA` in
`scripts/audit_common.py` remains the machine-readable source of
truth -- it always was -- and a test asserts the three agree:
every spec in `AUDIT_METADATA` carries a compliance link and has a
section on the page, and every spec that is not in `AUDIT_METADATA`
carries neither and is named in the no-check section.

### D5. The specifications come into scope, and the coverage number moves

Measured on this branch by editing the scope file and running
`scripts/review-tracking.py status`:

| | In scope | Reviewed | Needing review |
|---|---|---|---|
| Today | 77 | 14 | 63 |
| After | 112 | 14 | 98 |

The `review-coverage` threshold is 5 and `development` is already
non-compliant against it (`shakenfist/development#45`, open). So this
files no new issue and changes no cell in any compliance table -- only
the count in the body of an issue that is already open. The backlog
this creates is the backlog that already existed and was not being
counted.

The new exclude list:

```toml
exclude = [
    'docs/plans/*',
    'PLAN-*.md',  # unmatched-by-design: a guard against plans returning to the root
    'docs/audits/compliance.md',
]
```

`!docs/audits/README.md` goes away with the glob that needed it, and
the negation support in `in_scope()` stays -- it is general
machinery, not scaffolding for this one case.

## Implementation

Work happens in a worktree off `shakenfist/development`; this plan
file lands with the change (per `CLAUDE.md`).

### Execution

Phases are the sections below rather than separate files, following
this repository's convention.

| Phase | Status | Merged |
|-------|--------|--------|
| 1. Split the generated output out | Not started | |
| 2. Bring the specifications into review scope | Not started | |
| 3. Documentation and runbooks | Not started | |
| 4. Push audit | Not started | |

Phases 1 and 2 have a hard ordering dependency:
`test_review_tracking.py` asserts that every pattern in
`review-scope.toml` matches a path that exists unless it carries an
`unmatched-by-design` comment, so `docs/audits/compliance.md` has to
be committed before it can be named in the exclude list.

Phase 1 is one commit and cannot be smaller. The daily workflow
rewrites whatever the code tells it to rewrite, so the code change,
the removal of the marker blocks from 34 specifications, and the new
page have to land together or the next 06:00 UTC run either reverts
half of it or leaves 34 files carrying a stale table nothing updates.

### 1. Split the generated output out

* **`docs/audits/compliance.md`** (new). One page. A short
  hand-written preamble explaining what it is and that it is
  generated, then a single generated block between the existing
  markers containing:

  * the `*Generated <timestamp> from scripts/audit-check.py; do not
    edit.*` note, once, at the top;
  * one `## <spec-basename>` section per spec in `AUDIT_METADATA`, in
    the order `checks_by_spec()` yields sorted, each holding a link
    back to its specification and the table `render_section` renders
    today, unchanged in format;
  * a closing section naming the criteria with no automated check.

  The preamble sits *outside* the markers so it survives
  regeneration, the same way every specification's prose does today.

  The initial content is a faithful transcription of the 34 blocks
  currently in the specification files, not a fresh audit run.
  Generating it properly needs 17 repository clones and a `gh` token;
  transcription is exact, verifiable by diffing the moved blocks
  against what was removed, and it is replaced by real output at the
  next 06:00 UTC run anyway. A one-shot migration script does the
  move; it is not committed.

* **`scripts/audit-update-docs.py`**. `render_section` keeps its
  table-rendering logic and loses its per-spec framing: the markers,
  and the timestamp note, move up to a new page-level renderer.
  `update_spec_file` becomes `update_compliance_page` and writes one
  file. `main` stops iterating over spec files, so the
  `missing_markers` error path -- which exists to catch a spec whose
  markers were deleted -- collapses to a single check on the one page.

  Keep the `column_name` fallback and its warning exactly as they
  are. The comment on it records that one missing heading once
  stopped every project's table from publishing, and consolidating to
  one page raises that stake rather than lowering it: one page is one
  write, so a failure now takes out the whole fleet's compliance
  output in a single file rather than 34.

* **`scripts/audit_common.py`**. `AUDIT_METADATA[*]['spec']` still
  names the specification file -- it is what the compliance page
  links back to, what issue bodies point at, and what
  `test_audit_update_docs.py` checks the existence of. The markers
  stay where they are defined; only their number of uses changes.

* **`scripts/commit-audit-docs.sh`**. Narrow the diff check, the
  `git add` and the commit message from `docs/audits/` to
  `docs/audits/compliance.md`. This is a safety improvement beyond
  the tidying: today the bot runs `git add docs/audits/` on a
  checkout, so any other change present in that tree would be
  committed to `main` under the bot's name and the message
  "Regenerate audit compliance tables."

* **The 34 specification files**. Marker block out, static compliance
  link in, per D3.

* **`docs/audits/test-coverage.md`**. Already explains at length why
  it has no table. Reword the "There is no per-repository table on
  this page" sentence so it reads correctly now that no specification
  has one, and point at the no-check section of the compliance page.

* **`scripts/test_audit_update_docs.py`**. The existing tests keep
  their intent and change their target:

  * `test_every_generated_block_opens_with_the_current_note` --
    currently walks every spec, asserts the note format, and requires
    it checked more than 20 files so that a pass on nothing is
    impossible. Retarget to the single page; keep the guard by
    asserting the page's section count equals
    `len(checks_by_spec())`, which is the same protection against a
    silent pass on nothing.
  * `test_every_spec_file_is_named_in_the_index` -- unchanged, and
    `compliance.md` must not trip it. It lists every `*.md` bar
    `README.md` and requires each to be linked from the index, so the
    new page is either excluded alongside `README.md` or linked from
    the index. Link it: it should be discoverable there anyway.
  * New: every spec in `AUDIT_METADATA` carries a compliance link
    whose anchor matches its basename and has a matching section on
    the page; every spec file *not* in `AUDIT_METADATA` carries no
    such link and is named in the no-check section. This is what
    keeps D4's tell honest.
  * New: no specification file contains a `consistency-audit` marker.
    Cheap, and it catches the reintroduction this plan exists to
    prevent.

### 2. Bring the specifications into review scope

* **`.vscode/review-scope.toml`**. The exclude list per D5. The long
  prose comment above it is the substance of this phase, not an
  afterthought: most of it argues for an exclusion that no longer
  applies, and it should be rewritten to record what the directory
  now looks like and why one generated page is excluded -- keeping
  the paragraph about *why* the timestamp exists, which is the part
  that stays true and the part a future reader will otherwise
  re-derive.

  Delete the paragraph about `audits/*` having silently matched
  nothing after the tree moved under `docs/`, and the one about
  `test-coverage` being left excluded rather than named as an
  exception. Both describe a bookkeeping problem that this change
  removes.

* **`REVIEWS.md`**. Regenerated, not hand-edited. Expect
  `112 in-scope files` and no change to the reviewed list.

* Confirm `scripts/review-tracking.py status` reports 14 of 112 with
  98 needing review, and that `compliance.md` is not among them.

### 3. Documentation and runbooks

* **`docs/audits/README.md`**. The "File structure" section shows a
  specification's skeleton including the markers; replace the marker
  block with the static compliance link. Add `compliance.md` to the
  audit index, or to the prose above it, so it is discoverable from
  the directory's front page. The opening paragraph says each file
  "carr[ies] a per-project compliance table regenerated every
  morning" -- reword.

* **`docs/consistency-audits.md`**. The "two layers" table describes
  the specification layer as holding "a generated per-project
  compliance table"; that becomes a link, and the generated page is
  arguably a third row. Replace the marker-absence tell per D4. Check
  the "how to add a criterion" list, which the README says touches
  five files: adding a criterion no longer means adding a marker
  block, and the count may change.

* **`PUSH-AUDIT.md`**. The wave 1 grep for hand-edited compliance
  tables is keyed on `docs/audits/*.md` and matches marker lines and
  status rows. Retarget it: a status row appearing in a *specification*
  is now the defect worth grepping for, and an edit to
  `compliance.md` is the other. Both are worth a line.

* **`ARCHITECTURE.md`** and **`AGENTS.md`**. Check only. The
  component inventory does not change and no convention changes, so
  the expected outcome is no edit -- but `docs/audits/` is
  prominent enough in both that a stale sentence about tables living
  in the specifications is likely, and a grep for `consistency-audit`
  and for `docs/audits` across the repository is the cheap way to
  find every remaining one.

* Run `pre-commit run --all-files` -- actionlint, shellcheck, flake8,
  skillsaw and the four Python suites -- which is the whole of lint
  and test here.

### 4. Push audit

Run `PUSH-AUDIT.md` over the accumulated diff of the first three
phases against `main`. Findings land as their own pull request; the
plan is not complete until each is resolved or declined in writing,
with the reason recorded here. If the audit finds nothing, say so in
one sentence.

The interesting brief for wave 2 here is the documentation one: this
change edits 34 specification files mechanically and rewrites prose
in five more, and a mechanical edit repeated 34 times is exactly
where a wrong anchor or a dropped section heading hides.

## Risks and mitigations

* **The next daily run publishes nothing, or publishes to the wrong
  place.** The real risk of the phase. The `update-docs` job runs at
  06:00 UTC unattended and pushes to `main`; a mistake is discovered
  by a missing table, not by a red run. Mitigation: run
  `audit-update-docs.py --no-issues` against a saved results
  directory before the phase lands and diff the output, and watch the
  first real run rather than assuming it.

* **The website does not render the anchors.** The compliance links
  are the whole of the connection between a specification and its
  table, and they are markdown heading anchors. Mitigation: check one
  published page after the first deploy. If the site generator does
  not emit heading anchors, the links still resolve to the page and
  degrade to a scroll, which is the acceptable failure.

* **The 35 files arrive in the review queue and nothing reviews
  them.** The exclusion at least was honest about not covering them;
  a queue entry that sits for a year is worse than an exclusion,
  because it makes the coverage number a number nobody acts on.
  Mitigation: none in tooling -- `review-coverage` already files the
  nudge and `development#45` is already open. This is the phase
  making a real backlog visible, which is what was asked for.

* **A specification regrows a table.** An agent picking up an audit
  issue, or a future version of this repository's own tooling, adds
  one back. Mitigation: the phase 1 test asserting no specification
  contains a marker, plus the retargeted `PUSH-AUDIT.md` grep.

## Definition of done

* No file in `docs/audits/` other than `compliance.md` contains a
  `consistency-audit` marker, and every specification links to its
  section on the compliance page.
* `compliance.md` carries one timestamp note, a section for each of
  the 34 specifications with a check, and a section naming the ones
  without.
* `.vscode/review-scope.toml` excludes `docs/audits/compliance.md`
  and nothing else in that directory; `review-tracking.py status`
  reports 112 in scope.
* `pre-commit run --all-files` passes.
* One 06:00 UTC run has completed after the change, its commit
  touched only `docs/audits/compliance.md`, and the published page on
  shakenfist.com shows the tables with working anchors from at least
  one specification.
* Phase 4's findings are resolved or declined in writing.

## Future work

* **`compliance.json`.** If something ever wants the fleet-wide
  verdicts machine-readably from a checkout rather than from workflow
  artifacts, `audit-update-docs.py` can emit it alongside the page
  from the same in-memory results, excluded from review by the same
  pattern extended to `docs/audits/compliance.*`. Not now: it would
  have no reader, and an unread generated file drifts.

* **Reviewing the 35 specifications.** The point of the change, and
  not part of it. The first session on them is where we find out
  whether the prose in a directory nobody could review has the
  problems that suggests.

* **The same shape elsewhere in the fleet.** If any other repository
  has generated content interleaved with reviewable prose, it has
  this problem too. Worth a look during the next review session
  rather than a check of its own -- one instance is not a pattern.

## Back brief

Before phase 1 begins, the management session confirms with Mikal:
the single-page layout and its anchor scheme (D2, D3), and the
wording that replaces the marker-absence tell (D4). Both are cheap to
propose now and expensive to redo across 39 files.
