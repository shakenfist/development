# Source size ratchet template

A file that is already too long rarely gets there in one pull request.
It grows a few dozen lines at a time, each landing reviewed on its own
merits, and none of them is the one that made it large: "this file was
already huge" is not a finding against the branch in front of the
reviewer. A review scoped to a diff cannot see growth that only shows
up across fifty diffs. This template closes that gap.

The evidence for it is the fleet's own. In the three weeks after the
`source-file-size` shared block started telling every push audit that
a file over 1,500 lines wants a stated reason to stay whole, every one
of the largest files kept growing -- `shakenfist/mariadb.py` by 437
lines over nine landings, ryll's `app.rs` by 295 over six, and two of
the largest test suites in shakenfist/development -- the repository
that wrote the block -- by 724 lines between them. Over the preceding months
`mariadb.py` grew on 73 of 74 landings at a median of 36 lines each,
and instar's `main.rs` grew on all 19 of its landings at a median of
235: one shape is slow and one is lumpy, and an advisory sentence
caught neither.

The ratchet makes growth a decision rather than a drift. Every file
over 1,500 lines is listed in a baseline with its current length. A
listed file may stay exactly that long for ever; it may not grow, and
no new file may cross the line, unless the pull request that does it
also edits the baseline -- a one-line diff a reviewer cannot scroll
past.

It is the enforcement half of the `source-file-size` shared block
(shakenfist/development's `templates/shared-blocks/source-file-size.md`),
which is the policy half: what a reviewer should ask of a long file,
and why splitting at a line number rather than a seam is worse than
not splitting at all.

## Files

| File | Destination | Description |
|------|-------------|-------------|
| `source-size-ratchet.py` | `tools/source-size-ratchet.py` | The check; stdlib Python 3, copied verbatim |
| `pre-commit-hook.yaml` | merged into `.pre-commit-config.yaml` | A `repo: local` hook stanza, `pass_filenames: false` |
| -- | `tools/source-size-baseline` | Written by the adopting repository on adoption: one `<lines> <path>` per oversize file, sorted by path |
| -- | `tools/source-size-exclude` | Optional, written by the adopting repository: generated or vendored trees, one glob per line, each with a `#` comment saying why |
| `README.md` | -- | This file |

The script copies with no per-project substitution, and should stay
byte-identical to this one: its threshold is a constant rather than a
flag for that reason, so that one review of the template is a review
of every copy. Change it here and re-sync, rather than editing a copy.

## What it counts

- **Tracked files only.** The list comes from `git ls-files`, so
  `target/`, `.cargo-cache/`, virtualenvs and anything else untracked
  are never seen, with no list to maintain. A tracked path that is not
  a regular file in the working tree -- deleted, a symlink, a
  submodule -- is skipped.
- **Source only:** `.py .rs .ts .tsx .mts .cts .js .jsx .mjs .cjs .go
  .sh .c .h .cc .cpp .cxx .hh .hpp .css`.
  Markdown, data, configuration and lock files are not counted, and
  neither is a file with no extension, such as a script known only by
  its shebang. A repository adopting another language widens the list
  in the template, for the whole fleet.
- **Not generated output.** A short built-in list that is never
  anybody's review unit: protoc's `*_pb2.py`, `*_pb2_grpc.py` and
  `*.pb.go`, and minified `*.min.js` and `*.min.css`. Then the
  repository's own exclusions, below.
- **Lines as `wc -l` counts them**, plus one for a non-empty file
  whose last line has no newline. Comments count. A comment-blind
  count would need a lexer per language, and the baseline already
  removes the pressure to delete rationale to get under the line.

A file is oversize when it is longer than 1,500 lines -- the length
at which the shared block says a file wants a stated reason to stay
whole, so the baseline is exactly the list of files that owe one.

## Adopting it

1. Copy `source-size-ratchet.py` to `tools/source-size-ratchet.py`
   and keep it executable. Its shebang runs Python with `-I`
   (isolated mode), as the hook stanza does and as every command
   here does: without it, Python puts `tools/` first on its import
   path, and a module a pull request plants there -- a
   `tools/fnmatch.py`, say -- would run in place of the standard
   library's.
2. Merge `pre-commit-hook.yaml` into `.pre-commit-config.yaml`. It is
   indented to sit under `repos:` as it stands; if the file already
   has a `repo: local` entry, add the hook to that entry's `hooks:`
   instead. The comments in the stanza say why each setting is what
   it is. There is no workflow to add: the fleet's CI already runs
   `pre-commit run --all-files`, so the hook gates merges too.
3. If the repository tracks generated or vendored source, write
   `tools/source-size-exclude` first (see below), so those files never
   enter the baseline.
4. Run `python3 -I tools/source-size-ratchet.py --init`. It writes
   `tools/source-size-baseline` from the current tree, with a header
   explaining what the file is.
5. Commit all of it in one pull request. The baseline in that pull
   request is the list of files that already owe a reason; it is a
   starting point, not an endorsement, and nothing about adopting the
   ratchet asks anyone to split them.

`--init` is for adoption. Run it later and it silently accepts
whatever has grown since, which is the drift the tool exists to stop.

## Repository-local exclusions

`tools/source-size-exclude` is optional: one glob per line, whole-line
`#` comments and blank lines ignored. Each pattern is matched with
Python's `fnmatch` against the repository-relative path, so `*`
crosses `/`. A pattern that matches a leading directory of a path
excludes the whole tree beneath it, with or without a trailing `/`;
with a trailing `/` it only ever matches a directory.

Patterns are anchored at the repository root, unlike `.gitignore`:
`vendor/` matches `vendor/x.py`, not `sub/vendor/x.py`; write
`*/vendor/` or the full path for that. That is deliberate: every line
has to carry a reason, and a pattern that silently matched at any depth
would exclude trees nobody gave a reason for. Give every line a comment
saying why that tree is not reviewed here:

```
# Vendored from shakenfist/sfui, and reviewed there rather than
# here.
sfui/

# Written by build.rs on every build.
*_generated.rs
```

A pattern that matches no tracked source file is reported as a note,
because a line that excludes nothing reads exactly like one that
excluded a tree. It is not a failure.

Exclude what nobody reviews here -- generated and vendored code -- and
nothing else. A hand-written file that is merely long belongs in the
baseline, where its length is visible, not in the exclude file, where
it is not.

## Growing a baselined file

When a pull request has a good reason to make an oversize file longer,
it raises that file's entry in `tools/source-size-baseline` by hand,
to the new length, and says in the pull request description why the
file should grow rather than split. A new file crossing the threshold
is the same: add its line, sorted by path, and say why. That is the
whole mechanism. The edit is the question asked in a place a reviewer
cannot miss; the reason lives in the description, not in the file,
because the tool rewrites the file's comments away.

The failure message names each file, its length and its entry, and
gives the two honest fixes: shrink it, or raise the entry and explain.
Shrinking a file by deleting its comments is not one of them.

## Shrinking

Shrinking needs nothing. An entry higher than its file, or for a file
that is gone, excluded or back under the threshold, is not a failure
-- the check notes it and passes, so that a shrink is never a two-step
dance. When you want the baseline tightened, run:

```bash
python3 -I tools/source-size-ratchet.py --update
```

It lowers entries that have shrunk and drops files that are gone,
excluded, or at or under the threshold. It never raises an entry and
never adds a file, because that hand edit is the reviewable decision.
If something has grown it still writes the tightened baseline, then
reports the growth and exits 1. When there is nothing to tighten it
leaves the file alone, comments included.

Nothing here forces a split. A ratchet that pushed files under a
deadline would push them into splits at line numbers rather than
seams, which the shared block counts as worse than the long file.
Splitting is each repository's own planned work; the ratchet only
stops the numbers getting worse while it waits.

## Exit status

| Status | Meaning |
|--------|---------|
| 0 | Every oversize file is at or under its baseline entry |
| 1 | A file grew past its entry, or crossed the threshold with none |
| 2 | The tool could not do its job: not a git repository, git failed, a malformed baseline line, which it names, or a baseline, exclude file or `tools/` that is a symlink or special file rather than a regular file or directory |

A tool failure is never reported as a size violation.

The tree it measures is pull request content, so the script treats it
as hostile. It refuses, rather than follows, a symlink or special file
where the baseline, the exclude file or `tools/` should be -- so a
committed `tools/source-size-baseline` pointing out of the tree can
neither be read nor written through -- and writes the baseline through
a temporary file renamed into place. It prints a path or pattern
escaped, with Python's `ascii()`, when it holds a control character or
an undecodable byte or begins with `::`, after any leading whitespace
(the runner trims it before looking for a command), so a file name cannot write
terminal escapes or workflow commands into a CI log. A tracked path
with a line break in it is counted, but if it is over the threshold it
is a tool failure, because the baseline cannot hold its name.
