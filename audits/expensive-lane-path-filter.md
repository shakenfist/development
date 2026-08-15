# Audit: Expensive lane path filtering

## What we check

Ephemeral VM runners (the `vm` label) are the expensive pool: the
lanes that run on them build entire clouds or boot guests, and a
single run costs tens of minutes to hours of capacity. A pull
request or merge queue entry that touches only content no lane
exercises should not pay for them. The content in question is the
`docs/` directory (where one exists) and the review-tracking state
(`REVIEWS.md` and the `.vscode` weaudit files, where review
tracking is deployed).

Every workflow that runs `vm`-runner jobs on `pull_request` or
`merge_group` must therefore be path-filtered, and the filter must
exclude the repository's non-code content: a `docs/**` pattern
where the repository has a `docs/` directory, and a `REVIEWS.md`
pattern where it carries `.vscode/review-scope.toml`.

Two filtering mechanisms count:

- A workflow backing no required status check may use trigger-level
  `paths:` / `paths-ignore:`. An inclusion-style `paths:` list
  (naming what the lane exercises, as the rust workflows do)
  excludes everything else by construction and passes without
  pattern checks.
- A workflow backing a required status check must use a filter job
  instead — `dorny/paths-filter` feeding job-level `if:`
  conditions, as kerbside's `check_paths` jobs do. Trigger-level
  filtering cannot coexist with required checks: a required check
  in a `paths-ignore`'d workflow never reports on a filtered PR,
  and a required check that never reports blocks the merge
  forever, while a skipped one satisfies it.

The worked example is kerbside: `functional-tests.yml`,
`direct-qemu-functional.yml`, and `sf-e2e-functional.yml` each
carry a `check_paths` filter job, `functional-tests.yml` also runs
its filter on `merge_group` so a docs-only or review-marks-only
queue entry skips the cloud matrices, and
[kerbside's docs/testing.md](https://github.com/shakenfist/kerbside/blob/develop/docs/testing.md)
documents the design. When adopting the pattern, note
dorny/paths-filter's `predicate-quantifier: 'every'` trap: the
default ANY-match semantics make a `'**'` pattern defeat every
exclusion.

Dedicated content-scanner workflows (gitleaks, trufflehog,
detect-secrets) are exempt — detected as an unfiltered workflow
invoking a scanner. Their whole point is to read the human-written
text a filter would skip: a secret lands in docs or review marks
as easily as in code. This is the same reasoning that keeps
content scanners out of `paths-ignore` in the review-tracking
adoption procedure (see
[workflow-standards.md](workflow-standards.md)). A workflow that
mixes scanner jobs with expensive lanes and already carries a
filter is still held to the exclusion requirements; its scanner
jobs should simply not consume the filter's output.

Other deliberate exceptions — a lane that must run even for
docs-only changes — are marked with an `audit-ok: no-path-filter`
comment anywhere in the workflow file, ideally with a reason.

Repositories with neither a `docs/` directory nor review tracking
have nothing for a filter to exclude and are reported as not
applicable, as are repositories whose PR-triggered workflows never
use `vm` runners.

## Template

No template — the correct shape depends on whether the workflow
backs a required status check. Copy the `check_paths` pattern from
kerbside's smoke workflows for gating lanes, or add trigger-level
`paths-ignore` for advisory ones.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-15T06:42:58.958930+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | non-compliant | shakenfist/agent-python#123 |
| client-python | compliant | - |
| client-python-k3s | non-compliant | shakenfist/client-python-k3s#29 |
| clingwrap | non-compliant | shakenfist/clingwrap#118 |
| cloudgood | N/A | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | compliant | - |
| occystrap | non-compliant | shakenfist/occystrap#113 |
| private-ci | N/A | - |
| ryll | non-compliant | shakenfist/ryll#274 |
| sfui | non-compliant | shakenfist/sfui#14 |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **client-python-k3s** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **clingwrap** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **occystrap** (Status): 2 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering), python-unit-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **ryll** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: ci.yml (filter does not exclude docs/). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
- **sfui** (Status): 1 expensive lane(s) triggered by pull_request or merge_group without adequate path filtering: functional-tests.yml (no path filtering). Add a check_paths filter job (see kerbside functional-tests.yml) or, only for workflows backing no required status check, trigger-level paths-ignore, excluding docs/** and the review-tracking files; mark deliberate exceptions with an "audit-ok: no-path-filter" comment
<!-- consistency-audit:end -->
