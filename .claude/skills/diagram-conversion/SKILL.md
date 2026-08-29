---
name: diagram-conversion
description: Convert ASCII-art diagrams in markdown to mermaid, and decide which ones should stay as ASCII. Use when asked to convert a diagram to mermaid, when adding a diagram to documentation, or when sweeping a repository's docs for diagrams that should be rendered.
---

# Diagram conversion

The policy is the `diagram-discipline` shared block, canonically at
`templates/shared-blocks/diagram-discipline.md` in this repository and
embedded in each project's `PUSH-AUDIT.md`. Read it first; this skill
is the procedure, not the rule.

The hard part of this job is **not converting the wrong things**. Most
character art in these repositories is not a diagram, and mermaid
destroys it. Budget your attention accordingly: finding candidates
takes a grep, deciding which are real takes judgment.

## Finding candidates

Box-drawing characters alone are a bad signal -- they match file
trees, memory maps and tables far more often than diagrams. Require
box or `+--+` characters **and** an arrow, and exclude trees and
hex-offset rows:

```bash
grep -rn --include='*.md' -E '[─│┌└├┐┘┬┴┼]|\+--+\+' docs/ *.md \
    | grep -vE '├──|└──'
```

Then read each hit. Excluding vendored trees (`.cargo-cache/`,
`node_modules/`, `vendor/`) is not optional -- a Rust repository's
registry cache holds thousands of markdown files nobody here wrote.

In the shakenfist repository, `docs/components/` is synced from the
component repositories by `sync-external-docs.yml`. Never convert a
diagram there: fix it in the source repository, or the next sync
reverts you.

## Deciding

Convert it if the picture is nodes and edges. Leave it if the layout
is the content. Worked examples from this fleet, all of which a naive
sweep would have wrecked:

| Leave alone | Why |
|-------------|-----|
| `ryll/ARCHITECTURE.md` `src/` listing | A file tree with aligned inline comments |
| `instar/docs/guest-architecture.md` | Guest address-space map; the hex column is the point |
| `instar/docs/qcow2/qcow2-l1l2-tables.md` | Bit-field layouts |
| `instar/docs/prototypes/virtio-block.md` | Ring-buffer memory layout, not a flow |

A block that mixes both -- a layout diagram with a couple of arrows
bolted on -- usually wants splitting: keep the layout as a fence, and
add a `mermaid` block beside it for the flow.

## Converting

Pick the type from what the diagram claims, not from what it looks
like. An ASCII diagram drawn left-to-right with arrows between
labelled parties is almost always a `sequenceDiagram` that was drawn
as a flowchart because ASCII made ordering hard.

Keep the node text honest: ASCII boxes force short labels, and the
conversion is the moment to restore the real name. Use `<br/>` for a
second line rather than cramming.

Preserve the surrounding prose. A sentence that says "the diagram
below shows the boxes left to right" describes the ASCII layout, not
the content, and needs rewriting or deleting -- mermaid chooses its
own layout.

## Verifying

Never hand back a diagram you have not rendered. mermaid fails at
render time, not at commit time, so an unverified conversion is a
broken docs page.

`mmdc` reads a markdown file, renders every mermaid fence in it, and
exits non-zero on a parse error:

```bash
docker run --rm -u "$(id -u):$(id -g)" \
    -v "$PWD":/src:ro -v /tmp/mermaid-out:/out \
    ghcr.io/mermaid-js/mermaid-cli/mermaid-cli:11.4.2 \
    -i /src/docs/whatever.md -o /out/x.md
```

About two seconds per file. Check the exit status directly -- piping
the run into `tail` or `grep` reports the pipeline's status, not
`mmdc`'s, which silently turns every failure green.

Projects that have adopted `templates/mermaid-lint/` have this wrapped
as `tools/mermaid-lint.sh`; use that where it exists.

There is no useful DOM-free shortcut. `mermaid.parse()` under plain
node throws `DOMPurify.addHook is not a function` for `flowchart` and
`stateDiagram-v2` -- the two most common types -- so a pure-node
checker reports false failures on exactly the diagrams that matter.

## Scope

Converting a diagram the change already touches is in scope for that
change. A whole-repository sweep is its own commit, and preferably its
own pull request, because the reviewer's job is to check that every
diagram still says what it said before.
