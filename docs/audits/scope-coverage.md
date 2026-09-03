# Audit: audit scope against the organisation

## What we check

Every repository in the `shakenfist` organisation is either in the
audit matrix in `.github/workflows/consistency-audit.yml` or on the
excluded list in [README.md](README.md), and every name in either of
those still resolves to a repository in the organisation.

This is the only criterion that measures the fleet rather than the
repository it was handed. It runs against the `development`
repository, because that is where the lists are written, and reports
`not applicable` everywhere else.

## Why this exists

Scope is written down three times: the `repo:` matrix, the in-scope
list and the excluded list. `AuditScopeIsStatedOnceTest` in
`scripts/tests/test_registry.py` holds those three in agreement with
each other, which catches a repository added to the matrix but not to
the documentation, or dropped from the matrix while the documentation
still claims it.

Nothing compared any of them to the organisation. A repository in none
of the three was not audited, was not documented as excluded, and
produced no finding anywhere -- silent by construction, because the
only signal was a repository missing from a list nobody diffs. When
this check was written, five repositories were in that state and three
names on the excluded list had not existed for over a year.

Both directions are the same missing reconciliation:

- **A repository nobody decided about.** Auditing it and excluding it
  are both fine. Having made neither decision is not, and it is
  indistinguishable from the outside from a decision to exclude.
- **A name that no longer resolves.** Harmless in itself -- an
  exclusion for a repository that does not exist excludes nothing --
  but it means the list has never been reconciled against reality, and
  a list nobody reconciles is one nobody can trust in the other
  direction either.

## Archived repositories are not exempt

`isArchived` is the obvious filter and is deliberately not used. Every
archived repository in the organisation is already on the excluded
list, so requiring a decision for all of them costs nothing to adopt.

The case that settles it is a repository dormant for years that nobody
archived: an `isArchived` filter passes it silently, which is the exact
failure this criterion exists to remove. Where a repository really is
finished, archiving it and listing it as excluded are both cheap, and
between them they say so in two places a reader can see.

## How to fix it

For a repository the check names as undecided, one of:

- Add it to the matrix in `.github/workflows/consistency-audit.yml`
  and to the in-scope list in [README.md](README.md). See "Bringing a
  repository into scope" in
  [docs/consistency-audits.md](../consistency-audits.md), and expect
  every failing criterion to file an issue on the next run.
- Add it to the excluded list in [README.md](README.md), which is a
  decision with a reason attached rather than an omission.

For a name that is not in the listing, the check asks GitHub about it
directly rather than assuming, and says which of three things
happened:

- **It no longer exists.** The entry goes with it.
- **It was renamed.** The finding names the new name; write that in
  the matrix or the list. The API follows a rename redirect while
  issue listing and search do not, which is why a stale name is worth
  fixing rather than tolerating: `audit-manage-issues.py` has its own
  warning for the same trap.
- **It exists but the listing did not return it.** Then the lists are
  right and the listing is short -- almost always a token that cannot
  see private repositories. Nothing about the scope is wrong, and the
  finding says to check the token, because a listing that cannot see
  part of the organisation cannot answer the first question either.

## What this does not check

Whether a repository *should* be audited. That is a judgement about
the project, and the check makes no attempt at it: what it removes is
the third state, where nobody made the judgement either way.

It also knows nothing about repositories outside the GitHub
organisation. Projects on the private GitLab are invisible to it.

## Template

No template -- compliance is restored by editing the matrix and the
lists, which is per-decision by nature.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](compliance.md#scope-coverage).
