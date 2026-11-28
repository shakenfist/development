# Audit: Rust function length lint

## What we check

Rust projects must enable clippy's `too_many_lines` lint, with a
threshold somebody chose, so that a function growing past it is
flagged on the pull request that grows it. A function that is too long
rarely got there in one change; it grows a few lines at a time, each
reviewed on its own merits, and a reviewer scoped to a diff cannot see
the sum.

Concretely, we require:

1. The root `Cargo.toml` sets `too_many_lines` to `warn` or `deny` in
   `[workspace.lints.clippy]` (or `[lints.clippy]` for single-crate
   repositories):

   ```toml
   [workspace.lints.clippy]
   too_many_lines = "warn"
   ```

   The table form, `too_many_lines = { level = "warn", priority = -1 }`,
   is accepted too. The level is read from the value, not from a
   comment after it, so `too_many_lines = "allow"  # was "deny"` is not
   enabled.

2. A `clippy.toml` beside the root manifest sets the threshold:

   ```toml
   too-many-lines-threshold = 200
   ```

   The number is not judged, only that one was set. A line commented
   out does not count.

3. Every first-party crate manifest either inherits the workspace
   lints or defines the lint itself:

   ```toml
   [lints]
   workspace = true
   ```

   Two kinds of manifest are not asked. A fuzz harness crate (any
   `Cargo.toml` under a `fuzz` directory), as for
   [rust-unwrap-lint](rust-unwrap-lint.md). And a manifest that
   declares a `[workspace]` of its own: cargo excludes a nested
   workspace root from the workspace above it, so the root's
   `[workspace.lints]` cannot reach it, and demanding that it inherit
   would be demanding the impossible. The `[workspace]` header is what
   counts. A member carrying only `[workspace.dependencies]` or
   `[workspace.lints]` has copied a table from the root, declares no
   workspace, and is asked like any other member.

4. No tracked `.rs` file carries a bare
   `#[allow(clippy::too_many_lines)]`. An allow is accepted when it
   gives a reason: either `reason = "..."` with something in the
   string, inside the same `allow(...)` that names the lint, or a
   comment on the line immediately above the attribute's first line.
   Three forms of comment are distinguished:

   * A plain `//` comment with text after it is accepted. A bare `//`
     is not.
   * A `/* ... */` comment that opens and closes on that one line is
     accepted. Unlike `//`, the text inside it is not checked.
   * A multi-line block comment is not accepted. Only the line
     immediately above the attribute is read, and in a multi-line
     block comment that is the closing line, which does not open a
     comment. Write the reason on one line, as `//` comments or
     `reason = "..."`.

   A doc comment (`///`, `//!`, `/** */` or `/*! */`) does not count
   in any form: it describes the function to its callers, nearly every
   function long enough to want an exemption has one, and accepting it
   would accept them all.

   Only an `allow` is looked for, and only one that names the lint
   inside its own parentheses.
   `#[cfg_attr(test, allow(clippy::too_many_lines))]` is found;
   `#[cfg_attr(test, expect(clippy::too_many_lines),
   allow(dead_code))]` is not, because the lint is handled by `expect`.
   Likewise a reason belongs to the `allow` it is written in:
   `#[cfg_attr(test, allow(clippy::too_many_lines), expect(dead_code,
   reason = "..."))]` is flagged, because that reason explains the
   `expect`.

   Attributes span lines -- rustfmt wraps a long one -- so they are
   found by matching their brackets, not by searching line by line. A
   line search for `expect(clippy::too_many_lines` finds none of the
   thirteen such attributes in the repository this was piloted on, for
   exactly that reason. A `#[` whose brackets never balance is not an
   attribute: a file that does not parse is rustc's to complain about,
   and the attributes after the break are still read.

## Why

A threshold is a measurement, not a preference. Clippy's default is 100
lines, and whether that is right depends on the code. ryll, the
repository this was worked out on, has about 4,145 functions:

| Over | Functions |
|------|-----------|
| 100 lines | 40 |
| 150 lines | 21 |
| 200 lines | 13 |

At 100 the lint would have asked for forty exemptions on day one, and
a lint that opens with forty exemptions is switched off in practice. At
200 it asked for thirteen, each of which is a function somebody can
look at and say why it is long -- a flat dispatch over a protocol's
message types, say -- and the lint then fires on the fourteenth. The
repository records the date and the counts in a comment in its
`clippy.toml` beside the number, so the next person to change it can see
what it was chosen against. That is the worked example this
criterion asks of everyone: count, choose a number the existing code
mostly passes, and write down what was counted. Which number comes out
is the repository's business.

## `#[expect]`, not `#[allow]`

An exemption should be `#[expect(clippy::too_many_lines, reason =
"...")]` rather than `#[allow(...)]`. An `expect` is checked by the
compiler: once the function is shortened under the threshold the
attribute is unfulfilled and rustc warns, so the exemption is removed
when it stops being true. An `allow` stays for ever and goes stale
silently, and a stale exemption is a licence for the function to grow
back.

For that reason this criterion does not scan for `expect`: the compiler
already notices when one is stale. It does scan for `allow`, which
nothing else polices, and accepts one that says why -- a repository on
a toolchain too old for
`expect` has no other choice -- while a bare one is the unexplained
exemption the lint exists to prevent.

## What this does not check

* The threshold. See above.
* That the lint passes. `warn` in `Cargo.toml` with `-D warnings` in
  the CI clippy run is the arrangement that makes it gate a merge, as
  for `rust-unwrap-lint`; this criterion looks at configuration only.
* Whether a reason is a good one. It must be present, and a reviewer
  reads it.

## Residual

A nested workspace is unmeasured. A crate that declares its own
`[workspace]` is outside what this criterion can ask, so it may carry
no length lint at all and pass. ryll has one: a small tool crate under
`tools/` with a workspace of its own. Its functions are not counted in
the distribution above, and nothing here would notice if one grew
large.

## How to fix it

Count first: list the functions by length, as clippy would, and find
where the distribution has a knee. Set `too-many-lines-threshold` in
`clippy.toml` to a number the existing code mostly passes and write the
date and the counts in a comment beside it. Add `too_many_lines =
"warn"` under `[workspace.lints.clippy]`, make each member crate
inherit with `[lints] workspace = true`, and give each function that is
over the line an `#[expect(clippy::too_many_lines, reason = "...")]`.

The reasoning behind the family of size criteria, and the pilot that
measured ryll, are in
[PLAN-size-ratchet.md](../plans/PLAN-size-ratchet.md).

## Template

No template -- this is a code-level pattern. See the configuration
snippets above.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](compliance.md#rust-function-length-lint).
