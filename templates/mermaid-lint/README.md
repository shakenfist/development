# Mermaid lint template

Mermaid diagrams fail at render time, not at commit time. A syntax
error commits cleanly, passes every linter in the fleet, and then
shows an error box on GitHub and nothing at all on the mkdocs sites.
Nothing else in CI reads a diagram. This template closes that gap.

It is the enforcement half of the `diagram-discipline` shared block
(`templates/shared-blocks/diagram-discipline.md`), which is the policy
half: diagrams of structure and flow are written as mermaid rather
than drawn in ASCII. Converting them is covered by the
`diagram-conversion` skill in `.claude/skills/`.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `mermaid-lint.sh` | `tools/mermaid-lint.sh` | Renders every tracked markdown file that contains a mermaid fence |
| `mermaid-lint.yml` | `.github/workflows/mermaid-lint.yml` | Runs the script on markdown-touching pull requests |

Both copy directly, with no per-project substitution. A project that
already has a CI gate job may prefer to add the script as a step there
rather than take the workflow -- see "Required status checks" below.

## Why a container

`mmdc` renders through puppeteer, so it needs a browser. Running it
from the upstream container keeps chromium and a node toolchain off
the runners.

There is no lighter path worth taking. mermaid's own `parse()` under
plain node throws `DOMPurify.addHook is not a function` for
`flowchart` and `stateDiagram-v2` -- the two most common types in this
fleet -- so a DOM-free checker reports false failures on exactly the
diagrams that matter. Supplying a DOM with jsdom pulls in an undici
that needs a newer node than the runners carry.

The cost is smaller than it looks: the image is cached after its first
pull, and rendering is about 1.4 seconds per file amortised inside a
single container. ryll's seven diagram-bearing files lint in ten
seconds; ryll and kerbside together, sixteen files, in twenty-two.
Nearly all of the real cost is the virtual machine, which is why the
workflow carries a path filter.

## The runner label

`[self-hosted, vm, debian-12-docker, s]`, not `static`. Static runners
have no docker daemon; `debian-12-docker` is the fleet image that
ships `docker.io`. The label must also be listed in
`.github/actionlint.yaml` under `self-hosted-runner: labels:`, or
actionlint fails on the workflow.

## Required status checks

The workflow is path-filtered, so it does not run on a pull request
that touches no markdown. A path-filtered workflow that a branch
ruleset requires never reports on those pull requests, and blocks
them forever. Two ways out, both fine:

- **Leave it advisory.** It runs when markdown changes, which is when
  it can find anything, and a red X on the pull request is enough.
- **Fold it into the repository's existing gate.** Add the script as a
  step in a job the gate already covers, guarded by the repository's
  `check_paths` output, and let the gate's "success or skipped" jq do
  the rest. ryll's `ci.yml` is the worked example of that pattern.

Do not simply add `merge_group:` to the trigger list: `paths` is not
supported on that event, so every merge would spin a virtual machine
to lint diagrams that the pull request already linted.

## The worked example

The development repository runs this on itself: `tools/mermaid-lint.sh`
and `.github/workflows/mermaid-lint.yml` there are byte-identical
copies of the two files in this directory, so drift between the
template and a real deployment shows up as a diff rather than as a
surprise. Sync from here rather than editing either copy in place.

## Using it by hand

```bash
tools/mermaid-lint.sh            # every tracked markdown file
tools/mermaid-lint.sh docs/x.md  # just this one
```

Check the exit status directly. Piping the script into `tail` or
`grep` reports the filter's status, not the script's, and turns every
failure green -- a mistake worth naming because it is exactly how this
was first mis-measured.

## The pinned image

`mermaid-lint.sh` pins `ghcr.io/mermaid-js/mermaid-cli/mermaid-cli` to
an exact tag. Renovate's stock managers do not read a docker reference
out of a shell script, so unlike the rest of the fleet's dependencies
this one does not move on its own. Bump it deliberately when a mermaid
feature is missing, and re-run the script over the whole repository
afterwards: a mermaid major version can reject a diagram its
predecessor accepted.
