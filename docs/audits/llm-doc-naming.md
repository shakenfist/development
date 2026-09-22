# Audit: Agent instruction file naming

## What we check

A project's agent instructions live in `AGENTS.md`. No file named for
a single tool -- `CLAUDE.md`, `CLAUDE.local.md`, `GEMINI.md` -- is
tracked anywhere in the repository.

`AGENTS.md` started as Codex's convention and became the shared one:
Claude Code, Codex and Gemini CLI all read it. A vendor-specific file
is therefore no longer how a project reaches a particular tool, which
is what it was for when these files were written. It is one of two
things instead, and the check's detail says which, because they want
different fixes.

* **With no `AGENTS.md` beside it**, the vendor file *is* the
  project's agent context, under a name only one tool reads. The fix
  is a rename -- `git mv CLAUDE.md AGENTS.md` -- and the
  `llm-tooling` audit is failing for the missing `AGENTS.md` at the
  same time. This audit names the file that rename starts from.
* **With an `AGENTS.md` beside it**, both are loaded, and the vendor
  file is a second set of instructions carrying equal authority. Two
  documents that both claim to say how to work in a repository will
  disagree, and the one an agent acts on is then decided by load
  order rather than by anybody. The fix is a read rather than a
  `git mv`: merge what is still true into `AGENTS.md` and delete the
  original. The fleet's copies run to hundreds of lines and predate
  the `AGENTS.md` beside them, so they hold both stale duplication
  and detail that was never carried across -- and what survives the
  merge is subject to the `llm-doc-structure` caps, so most of it
  belongs in `docs/` rather than appended to `AGENTS.md`.

Matching is on the file's basename, case-insensitively, at any depth.
That covers `.claude/CLAUDE.md` and `.gemini/GEMINI.md` without the
check having to name those directories, and it covers a nested
`subdir/CLAUDE.md` -- which an agent loads when it works in that
subdirectory, and which is the copy nobody remembers to update.

A symlink to `AGENTS.md` does not pass. It was a reasonable bridge
while tooling caught up; now that the tools read `AGENTS.md` directly
it is a second name for one file and nothing more, and the dangling
one it leaves behind after a move is worse than the tolerance is
worth.

The file list comes from `git ls-files`. An untracked `CLAUDE.md` is
somebody's scratch file in their own clone, not a property of the
repository, and the daily audit runs against a fresh clone and would
never see one. `CLAUDE.local.md` is in the matched set for the same
reason from the other side: it is meant to be gitignored, so an
untracked one is invisible here and a tracked one is a personal
override shipped to everybody.

A repository with no such file passes, including one with no agent
context at all -- "nothing here is named for a vendor" is true of it,
and whether it should have an `AGENTS.md` is `llm-tooling`'s question.

The matched set is a constant so that it can grow. GitHub Copilot's
`.github/copilot-instructions.md` is the obvious next member and is
deliberately not in it yet: nothing in the fleet has one, and a rule
is easier to defend when every repository it names is one we have
looked at.

## Template

No template -- the fix is a rename, or a read and a merge, and both
are project-specific. The merge must be a *merge*: verify the detail
survives in `AGENTS.md` or under `docs/` before deleting the original.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](compliance.md#llm-doc-naming).
