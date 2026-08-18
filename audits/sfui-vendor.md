# Audit: sfui vendored copy

## What we check

sfui, the Shaken Fist web UI design system, lives canonically at
https://github.com/shakenfist/sfui and is vendored into each
consumer's static assets by that repository's `tools/vendor.sh`,
which stamps the vendored directory with a `.sfui-commit`
provenance file. This is the shared-blocks pattern applied to a
directory: one canonical copy, verbatim copies downstream, and this
audit to catch drift.

For every `.sfui-commit` found in a repository, the check verifies:

* The vendored copy is **verbatim** at the recorded canonical
  commit, by running the canonical repository's own
  `tools/vendor.sh --check` at that commit (so the distributable
  file list always matches the commit the copy claims to be). A
  difference means someone edited the vendored copy in place; the
  fix is to move the change to the canonical repository and
  re-vendor, because the next sync would silently discard it.
* The recorded commit **is canonical HEAD**. Like a stale shared
  block, a copy behind canonical means improvements have not
  propagated; the fix is to re-run `tools/vendor.sh` from an up to
  date sfui checkout.

Repositories with no `.sfui-commit` are not applicable.

private-ci, the first sfui consumer, is internal tooling and is
excluded from the conventions audits, but this check is the exception
that runs against it anyway. It is in the workflow matrix scoped by
`only_checks` in `REPO_OVERRIDES` to this check alone, so it collects
no issues about packaging, release workflows or branch naming. The
reason for the exception is that vendored drift produces no symptom:
private-ci kept working perfectly for five days with a copy two
canonical merges behind, and only a hand-run of
`tools/vendor.sh --check` -- which nobody is scheduled to do -- would
have said so.

kerbside is the second consumer and is audited normally. Its admin UI
began converting to sfui on 2026-08-11, when phase 4 of the conversion
put a second base template and the login page on the design system;
the remaining pages convert a phase at a time, so `.sfui-commit` is
already there and already checked while most of the UI is still
Bootstrap. This audit does not care how much of a consumer's UI has
converted, only that whatever it vendors is verbatim and current --
which is the useful property during a long conversion, because the
copy is re-vendored on almost every phase and each of those is a
chance to land one merge behind.

## Template

No template -- vendoring is performed by `tools/vendor.sh` in the
canonical sfui repository, and the vendored files themselves are
the template.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-18T06:47:31.254284+00:00

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| divergulent | N/A | - |
| instar | N/A | - |
| kerbside | non-compliant | shakenfist/kerbside#296 |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | non-compliant | shakenfist/private-ci#8 |
| ryll | N/A | - |
| sfui | N/A | - |
| shakenfist | N/A | - |

Details for non-compliant projects:

- **kerbside** (Status): kerbside/api/static/sfui: 35 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
- **private-ci** (Status): conductor/static/sfui: 8 commit(s) behind canonical; re-run tools/vendor.sh from an up to date sfui checkout
<!-- consistency-audit:end -->
