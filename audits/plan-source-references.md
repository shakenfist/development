# Audit: Plan references in source

## What we check

Every reference to a plan file (`PLAN-*.md`) written into source code
or configuration must resolve in the repository it is written in, or
else be an absolute URL.

Comments and configuration cite plans to say where a decision is
recorded: "pinned at 50 MB rather than scaled to system memory; the
deferral is recorded in `docs/plans/PLAN-session-001-feedback.md`".
That pointer is the only trail from the code to the reasoning behind
it, and it is the trail a reader follows when they want to change the
code.

Nothing renders these pointers. A markdown link in `docs/` breaks
visibly on the documentation site, and `docs-external-links` audits
it; a path inside a `//` comment or a YAML key is inert text that no
renderer ever resolves. So when a plan is renamed, or archived into
`docs/plans/completed/`, the pointer rots silently and stays rotten.
The first person to notice is someone who went looking for the
reasoning and did not find it -- at which point the comment is worse
than no comment, because it asserts a record exists.

The automated check runs `git ls-files`, skips markdown files (they
are `docs-external-links`' scope) and files over 2 MB, and looks for
`PLAN-<name>.md` in what remains. Each match is resolved:

* **Path-qualified references** (`docs/plans/PLAN-foo.md`) are
  resolved as written, from the repository root and then from
  `docs/`. The second position exists for mkdocs navigation, which
  addresses pages relative to the documentation root.
* **Bare filenames** (`PLAN-foo.md`) are matched against every
  markdown file under `docs/plans/` at any depth, so a plan archived
  into `docs/plans/completed/` still resolves. A bare filename names
  no directory, so there is no path for it to be wrong about.

Two shapes are not flagged:

* **Absolute URLs.** Text matching a `scheme://` URL is removed
  before scanning. A plan in another repository cannot resolve
  locally and should be written as
  `https://github.com/<org>/<repo>/blob/<default-branch>/docs/plans/PLAN-foo.md`
  -- the same rule `docs-external-links` and `readme-absolute-links`
  apply for the same underlying reason: a reference that is read
  somewhere other than where it lives has to be absolute.
* **Lines carrying `audit-ok: plan-reference`.** For the rare line
  where a `PLAN-*.md` string is not a pointer at all -- a filename
  pattern in a linter config, a test fixture naming a plan that
  deliberately does not exist.

A repository with no plan references outside markdown is reported as
N/A.

This audit composes with `plan-phase-references`, which governs what
documentation prose may cite, and with `plan-index`, which governs
whether a plan is registered. This one governs only whether a pointer
written in code still lands on a file.

## Template

No template. Fix each reference at its source:

* the plan moved to `docs/plans/completed/` -- update the path, or
  drop to the bare filename, which resolves either way;
* the plan was renamed -- update the name;
* the plan lives in another repository -- rewrite the reference as an
  absolute `https://github.com/...` URL;
* the plan never existed, or the reference is not a pointer -- delete
  it, or mark the line `audit-ok: plan-reference`.

Rewording is not a fix on its own: the point of the pointer is that a
reader can reach the reasoning, so a reference that cannot be made to
resolve should be replaced by the reasoning itself, not deleted.

## Projects

<!-- consistency-audit:begin -->
*(Awaiting the first automated regeneration by the consistency
audit workflow.)*
<!-- consistency-audit:end -->
