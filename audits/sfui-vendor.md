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

Repositories with no `.sfui-commit` are not applicable. Note that
private-ci, the first sfui consumer, is outside consistency-audit
scope (internal tooling); its vendored copy is checked by hand with
`tools/vendor.sh --check` rather than by this audit. The expected
in-scope consumer is kerbside, once its admin UI converts to sfui.

## Template

No template -- vendoring is performed by `tools/vendor.sh` in the
canonical sfui repository, and the vendored files themselves are
the template.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-08-08T06:56:27.925065+00:00

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
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| ryll | N/A | - |
| shakenfist | N/A | - |
<!-- consistency-audit:end -->
