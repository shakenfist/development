# Audit: README absolute links

## What we check

Every link in the **top-level `README.md`** must be absolute. A link
target is acceptable if it is:

* a scheme-qualified URL (`https://`, `http://`, `mailto:`, `data:`,
  ...);
* a protocol-relative URL (`//host/...`); or
* a pure in-page anchor (`#section`), which resolves against the
  rendered page wherever it is shown.

Anything else -- `docs/x.md`, `./x.md`, `../x.md`, `/x`, `x.md` -- is
relative and is flagged.

Only the **top-level** `README.md` is audited. It is the file rendered
*off* the repository landing page: the PyPI long description,
crates.io, and README mirrors all display it in a context where a
relative link resolves against the wrong base and silently 404s.
READMEs in subdirectories are only ever viewed on the GitHub file
tree, where relative links resolve correctly, so they are
intentionally out of scope.

This audit exists because divergulent's first PyPI release rendered
with every relative link broken -- they pointed at `docs/…` relative
to the PyPI project page rather than the GitHub landing page. Absolute
`https://github.com/<org>/<repo>/blob/<branch>/…` URLs render
correctly on PyPI and still resolve on GitHub.

Links inside fenced code blocks and inline code spans are ignored: a
documented command that happens to contain `[x](y)` is sample text,
not a rendered link.

## Template

No template -- rewrite each relative link target to an absolute URL.
For links to other files in the same repository, use
`https://github.com/<org>/<repo>/blob/<default-branch>/<path>`.

## Projects

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-15T08:15:40.094463+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#107 |
| client-python | non-compliant | shakenfist/client-python#345 |
| clingwrap | non-compliant | shakenfist/clingwrap#108 |
| cloudgood | N/A | - |
| divergulent | compliant | - |
| instar | non-compliant | shakenfist/instar#436 |
| kerbside | non-compliant | shakenfist/kerbside#104 |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#40 |
| occystrap | non-compliant | shakenfist/occystrap#96 |
| ryll | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3395 |

Details for non-compliant projects:

- **agent-python** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, docs/developer-guide.md, docs/index.md, docs/protocol.md
- **client-python** (Status): 3 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md
- **clingwrap** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, RELEASE-SETUP.md, docs/, docs/index.md
- **instar** (Status): 30 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): CHANGELOG.md, docs/amend.md, docs/bench.md, docs/bitmap.md, docs/commentary/index.md, docs/commit.md, docs/create.md, docs/dd.md, docs/index.md, docs/map.md (+20 more)
- **kerbside** (Status): 15 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): AGENTS.md, ARCHITECTURE.md, docs/, docs/capabilities.md, docs/channel-protocols.md, docs/compression-protocols.md, docs/configuration.md, docs/console-sources.md, docs/index.md, docs/protocol-overview.md (+5 more)
- **library-utilities** (Status): 1 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): docs/log-record-fields.md
- **occystrap** (Status): 8 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): RELEASE-SETUP.md, docs/, docs/command-reference.md, docs/docker-tarball-formats.md, docs/installation.md, docs/performance.md, docs/pipeline.md, docs/use-cases.md
- **shakenfist** (Status): 5 relative link target(s) in README.md (use absolute URLs so the README renders off the repo landing page): docs/operator_guide/database.md, docs/operator_guide/installation.md, docs/operator_guide/logging.md, examples/, shakenfist/deploy/collection/
<!-- consistency-audit:end -->
