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

Left advisory, the job can still fail a pull request while sitting in
its own workflow file, where another workflow's `needs:` list cannot
reach it. A repository that writes down which jobs gate a pull request
should name this one as a deliberate exception; the workflow header
says so too.

## The worked example

The development repository runs this on itself: `tools/mermaid-lint.sh`
and `.github/workflows/mermaid-lint.yml` there are byte-identical
copies of the two files in this directory, so drift between the
template and a real deployment shows up as a diff rather than as a
surprise. `MermaidLintDeploymentTest` in
`scripts/tests/test_docs_content.py` asserts it, which matters most
for the shell script: `.pre-commit-config.yaml` scopes shellcheck to
`^(scripts|tools)/`, so the copy in this directory -- the one that
goes out to the fleet -- is linted only by proxy through its `tools/`
twin. Sync from here rather than editing either copy in place.

## Using it by hand

```bash
tools/mermaid-lint.sh            # every tracked markdown file
tools/mermaid-lint.sh docs/x.md  # just this one
```

A named file is resolved against your current directory, so the second
form works from anywhere in the tree, and a path that does not resolve
is an error rather than an empty run. That matters because the
per-file form is exactly the invocation a typo reaches, and a linter
that exits zero on a file it never read is the failure this script
exists to prevent.

For the same reason, check the exit status directly. Piping the script
into `tail` or `grep` reports the filter's status, not the script's,
and turns every failure green -- a mistake worth naming because it is
exactly how this was first mis-measured.

Two fences GitHub renders and `mmdc` does not are refused rather than
skipped: a tilde-fenced diagram, and one whose language is separated
from the backticks by a space. `mmdc` reads only ```` ```mermaid ````
hard against the backticks, so a broken diagram in either form would
otherwise ship through the exact gap this closes, with the run
reporting "nothing to lint" and exiting zero. The script names the
file, the line and what to change, and exits 1. A fence that is both
-- `~~~ mermaid` -- gets one remedy naming both faults, because being
told only to remove the space leaves a tilde fence the next run
refuses.

A third refusal is about the name rather than the content: a tracked
path containing a newline. Everything past the scan is line oriented,
including the loop inside the container, which is POSIX `sh` and has
no `read -d` to switch. Such a path was silently truncated into a name
that renders nothing, so it is named and the run made red instead.
Other awkward names are handled rather than refused -- the listing is
NUL delimited and so is the scan's own output, so a path holding a
space, a quote, a backslash or a non-ASCII byte is linted normally.

A refusal does not end the run. If the repository also holds a
diagram that does not parse, both are reported together, because the
expensive part of this lane is the virtual machine and a second round
trip to deliver the second half of the same answer is the cost worth
avoiding.

Writing about that rule is safe. The scan tracks fence state rather
than matching bare lines, so a fence shown *inside* a longer fence is
an example rather than a diagram -- which is what the page explaining
the rule inevitably contains. Wrap the counter-example in a longer
fence of the other character and the lane stays green:

`````markdown
````markdown
~~~mermaid
flowchart TB
  a --> b
~~~
````
`````

The rules are CommonMark's: a fence opens on three or more backticks
or tildes and closes on the same character, at least as long, with no
info string, and only the fences that open at the top level are
classified. Note that `mmdc` itself has no such notion -- it renders
every mermaid fence it finds, nested or not -- so this decides which
files are worth starting a container for and which are refused, not
what gets rendered once a file is selected.

Prose quoting a fence is safe too, and for a rule of its own:
CommonMark says the info string of a backtick fence may not contain a
backtick, so a line that *starts* with an inline code span -- the
usual way to begin a sentence about a fence -- is prose rather than a
fence. That rule is load-bearing rather than pedantic. Without it such
a line opens a fence that nothing closes, and every diagram below it
in the file is read as fence content and never linted, behind a
"nothing to lint" and an exit 0. This README shipped a live instance
of exactly that line before the rule was implemented.

Nesting is the only way to quote a fence in a block. Indenting one by
four spaces does not work: the scan does not model indented code blocks,
so an indented `~~~mermaid` is still refused, and an indented
```` ```mermaid ```` is still linted. That is deliberate rather than
an oversight. Four spaces before a fence is far more often a diagram
inside a list item than a diagram being quoted, and treating the
indent as a code block would silently stop linting those -- failing
open on real content to spare a rarer false positive. Wrap the
example in a longer fence instead.

A fence inside a blockquote is the one shape that is neither linted
nor refused. The scan does not look past a leading `>`, so such a
fence is skipped -- and unlike the indent case this one does fail
open, because GitHub renders it while `mmdc` reports "No mermaid
charts found" for the same file. It is left that way deliberately.
The audit's regex does not see a blockquoted fence either, so the two
halves agree and no repository is told it is covered for a diagram
nothing renders; and refusing one would mean deciding what a fence
nested inside a blockquoted fence is, which is a new rule with a new
blind spot of its own. Put a diagram at the top level rather than in
a blockquote.

The workflow's path filter names the script and the workflow itself
alongside `**.md`, so a pull request that edits the checker and no
markdown still runs it. `!REVIEWS.md` is last, because a later pattern
wins; a repository with no `REVIEWS.md` keeps the line so the
exclusion is in force from the first commit of one.

Those two paths are literals, so a repository that renames the
workflow, or installs the script somewhere other than `tools/`, must
update them to match. A path filter that matches nothing looks
exactly like a path filter that had nothing to match, so the lane
would silently stop running on changes to itself -- which is the gap
the two paths were added to close. The same applies to a repository
that folds the script into an existing gate job rather than taking
the shipped workflow: that job's own filter needs the script's path.

## The pinned image

`mermaid-lint.sh` pins `ghcr.io/mermaid-js/mermaid-cli/mermaid-cli` to
an exact tag *and* to that tag's digest. A tag is mutable and this
runs a third-party container on a runner with a docker daemon, so the
digest is what actually pins it; the tag stays for readability.
Renovate's stock managers do not read a docker reference out of a
shell script, so unlike the rest of the fleet's dependencies this one
does not move on its own -- which is also why the digest costs nothing
in maintenance. Bump it deliberately when a mermaid feature is
missing, taking the new digest from the pull, and re-run the script
over the whole repository afterwards: a mermaid major version can
reject a diagram its predecessor accepted.

Three references move together. Two are in `mermaid-lint.sh` and so
travel to every adopting repository: `IMAGE_TAG`, and the digest
composed onto it -- two lines rather than one because a digest is 71
characters and cannot be wrapped. The third is the by-hand `docker
run` in the development repository's `diagram-conversion` skill,
which pins the same image so that an agent following the skill does
not pull a mutable tag. `MermaidLintDeploymentTest` asserts the skill
and the script name the same tag and digest, so a half-done bump
fails the suite rather than going unnoticed.
