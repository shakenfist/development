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
this way across three merged pull requests, in three distinct shapes: a
backticked stanza, a backticked stanza in a second pull request, and a pull
request with an entirely empty description whose three stanzas lived only
in commit messages. Issue #4087 needed *both* failures at once to stay
open, which is why the incidence looks low while the arrangement is one
mistake away from failing every time.

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
its description:

```
X-No-Autoclose: #4087
```

## Customisation

The workflow is project-agnostic. Repositories with self-hosted runners
pass their labels:

```yaml
    uses: shakenfist/actions/.github/workflows/issue-link-check.yml@main
    with:
      runs_on: '["self-hosted","static"]'
```

Repositories with a merge queue should add the job to the workflow which
already computes their `Can enqueue` gate, and list it in that job's
`needs`, rather than installing this standalone file. Gating matters most
exactly where the merge queue has disabled commit-message parsing.

## Prerequisites

- The shared workflow in `shakenfist/actions`
- `permissions: contents: read, pull-requests: read, issues: read`

## Projects using this template

| Project | Status |
|---------|--------|
| [shakenfist](https://github.com/shakenfist/shakenfist) | Proposed (gated, in `functional-tests.yml`) |
| [client-python-k3s](https://github.com/shakenfist/client-python-k3s) | Not yet |
| [instar](https://github.com/shakenfist/instar) | Not yet |
| [ryll](https://github.com/shakenfist/ryll) | Not yet |
| [kerbside](https://github.com/shakenfist/kerbside) | Not yet |
