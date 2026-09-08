# Issue Link Check Template

This template verifies that a pull request will actually close the issues
it says it fixes, and fails while the description can still be edited.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `issue-link-check.yml` | `.github/workflows/issue-link-check.yml` | Caller workflow |

## The problem it solves

GitHub only acts on issue-closing keywords found in a pull request's
**description**. Two habits defeat that silently:

- A stanza wrapped in backticks -- `` `Fixes #4087` `` -- is a markdown code
  span. GitHub renders it and parses nothing, so there is no link, no
  close, and not even a cross-reference on the issue. It looks tidier and
  it is inert.
- A stanza in a **commit message** closes an issue only when the commit
  reaches the default branch by an ordinary push. A merge queue advances
  the branch from its own staging ref, and commit messages are not parsed
  on that path. In a merge queue repository the commit stanza does nothing
  whatsoever.

An audit of shakenfist/shakenfist on 2026-09-07 found five issues left open
this way across three merged pull requests, in two distinct shapes: a
backticked stanza, twice, and a pull request with an entirely empty
description whose three stanzas lived only in commit messages. Issue
#4087 needed *both* failures at once to stay open, which is why the
incidence looks low while the arrangement is one mistake away from
failing every time.

## What it checks

Intent is read from three places: a branch named `issue-fix-NNNN` (or the
older `bug-NNNN`), a standalone stanza in the description, and a standalone
stanza in any commit message. That is compared against
`closingIssuesReferences`, which is GitHub's own parse of the description.

- **Fails** when something declared as fixed will not be closed. An issue
  which is already closed is not demanded, so a pull request following on
  from somebody else's fix is not made to claim it.
- **Reports, and never fails**, when GitHub will close an issue nobody
  asked it to. This is how a closing keyword sitting next to a reference in
  ordinary prose closes a live bug -- it happened three times in a hundred
  days in shakenfist/shakenfist. It is not enforced because real
  descriptions contain too many innocent matches (headings, table cells,
  several directives sharing a line) for a machine to judge; a check which
  cried wolf on those would be ignored within a week.

A stanza only counts as a directive when it is essentially the whole line.
That deliberately errs towards silence: a missed intent leaves the check
quiet, which is where every repository is today, while a false alarm
teaches people to ignore it.

## Opting out

A pull request which is only part of the fix for an issue declares that in
its description, on a line of its own:

```
X-No-Autoclose: #4087
```

## Customisation

The workflow is project-agnostic. It ships asking for the fleet's `static`
runners, because the reusable workflow's own default is `ubuntu-latest`
and
[the workflow standards audit](https://github.com/shakenfist/development/blob/main/docs/audits/workflow-standards.md)
prohibits a GitHub-hosted runner without an
`audit-ok: github-hosted-runner` marker and a reason. A repository with
no self-hosted runners drops the `with:` block and takes the default:

```yaml
    uses: shakenfist/actions/.github/workflows/issue-link-check.yml@main
```

That is worth doing deliberately rather than by omission. The
`ubuntu-latest` string lives in shakenfist/actions, not in the adopting
repository, so the Runners criterion scanning the adopter's workflows finds
nothing to flag and the choice never resurfaces.

## Merge queue repositories

Repositories with a merge queue should add the job to the workflow which
already computes their `Can enqueue` gate, and list it in that job's
`needs`, rather than installing this standalone file. Gating matters most
exactly where the merge queue has disabled commit-message parsing.

Two details travel with the job when it moves.

The gate workflow's `pull_request` trigger needs `edited` in its `types:`
list, for the reason the caller workflow's comment gives. Without it a
corrected description never re-runs the check, and a merge queue gate is
the worst place in the fleet to leave a stale red one.

The gate workflow is also almost certainly path-filtered, because
[the expensive lane path filter audit](https://github.com/shakenfist/development/blob/main/docs/audits/expensive-lane-path-filter.md)
requires it of anything running `vm` jobs on `pull_request`. Issue-closing
intent is orthogonal to which files a pull request touched -- a
documentation-only pull request closes documentation issues -- so the check
has to stay reachable on a filtered pull request. That means the
`dorny/paths-filter` shape, with this job deliberately left without the
`if:` condition the expensive jobs carry. Trigger-level `paths:` skips the
whole workflow, so the check silently never runs on exactly the pull
requests nothing else is watching.

## Prerequisites

- The shared workflow in `shakenfist/actions`:
  [`.github/workflows/issue-link-check.yml`](https://github.com/shakenfist/actions/blob/main/.github/workflows/issue-link-check.yml)
  and the checker it runs,
  [`tools/check-issue-links.py`](https://github.com/shakenfist/actions/blob/main/tools/check-issue-links.py).
  Everything this README describes -- the stanza matching, the `runs_on`
  input, the `X-No-Autoclose:` spelling -- is implemented there, and none of
  it is verified from here: actionlint's `could not read reusable workflow
  file` diagnostic, which is what would catch a wrong input name, is
  ignored for `templates/`. Renaming the input or the marker is a
  fleet-visible interface change.
- `permissions: contents: read, pull-requests: read, issues: read`

## Projects using this template

| Project | Status |
|---------|--------|
| [development](https://github.com/shakenfist/development) | Live (standalone; no merge queue here) |
| [shakenfist](https://github.com/shakenfist/shakenfist) | Proposed (gated, in `functional-tests.yml`) |

Only repositories which actually run it are listed, which is the rule the
other template rosters here follow. A row reading "not yet" is wrong from
the moment that repository adopts the template and nothing anywhere fails
when it is, and this repository has already deleted one roster for exactly
that -- see the end of `templates/ci-review-automation/README.md`. The four
other merge queue repositories (client-python-k3s, instar, ryll and
kerbside) are the obvious candidates, and the durable way to say so is a
consistency audit criterion and a compliance page section rather than a
table, once the shape has settled on a live adopter or two.
