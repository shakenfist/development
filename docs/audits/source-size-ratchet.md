# Audit: Source size ratchet

## What we check

Every repository with tracked source files carries the source size
ratchet from
[templates/source-size-ratchet/](https://github.com/shakenfist/development/tree/main/templates/source-size-ratchet),
wired so that it gates a merge:

| What | Where |
|------|-------|
| The script | `tools/source-size-ratchet.py`, byte-identical to `source-size-ratchet.py` in the template |
| The hook | A `.pre-commit-config.yaml` hook whose entry runs `tools/source-size-ratchet.py` |
| The CI lane | At least one workflow that runs every hook, as `pre-commit run --all-files` does, or that invokes `tools/source-size-ratchet.py` itself |

The copy is compared as bytes, not as text, so a copy that differs
only in its line endings fails. A copy that is missing fails, and so
does one the audit will not read: a link, dangling or out of the
checkout, a directory, or a file over the read cap.

The hook is matched by its entry rather than by its id, so a
repository that renamed the hook still passes and a comment naming the
tool does not. A repository whose index holds no file with a source
extension the script counts is reported as not applicable.

Which source extensions those are is read out of the template itself,
so this criterion cannot disagree with the tool about what counts.

## Why the CI lane is part of it

A hook in `.pre-commit-config.yaml` gates a commit made by somebody
who has pre-commit installed. It gates a merge only if something in CI
runs it, and until it does, a branch pushed with `--no-verify` carries
whatever it likes. Both shapes the fleet uses are accepted: a single
lane running `pre-commit run --all-files`, which runs every declared
hook, and a job per hook, which reaches this one by invoking the script
directly.

An invocation that names a single hook -- `pre-commit run <some other
hook>` -- is not a lane for this criterion. It runs that hook and no
others, so a repository whose ratchet job had been deleted would
otherwise pass on the strength of a job that lints something else.

## Why a ratchet at all

A file that is already too long rarely got there in one pull request.
It grows a few dozen lines at a time, each landing reviewed on its own
merits, and none of them is the one that made it large. A review
scoped to a diff cannot see growth that only shows up across fifty
diffs.

So every file longer than 1,500 lines -- the length at which
`templates/shared-blocks/source-file-size.md` says a file wants a
stated reason to stay whole -- is listed in `tools/source-size-baseline`
with its current length, and the baseline is exactly the list of files
that owe such a reason. A listed file may stay that long for ever. It
may not grow, and no new file may cross the line, unless the pull
request that does it also edits the baseline: a one-line diff a
reviewer cannot scroll past.

## What this does not check

The ratchet is not re-run here. Whether the baseline is honoured is
the hook's job on every pull request, and an audit that re-judged it
would only disagree with CI.

Neither `tools/source-size-baseline` nor `tools/source-size-exclude`
is required to exist. A missing baseline is an empty one, so a
repository with nothing over the threshold is compliant without the
file, and the exclude file is for a repository that has a generated or
vendored tree to leave out.

A fork-guarded CI job satisfies the lane requirement while reporting
no verdict on a pull request from a fork. That is the trade the
template's job snippet makes deliberately -- the alternative is
running a pull request's own code on this fleet's self-hosted runners
-- and this criterion cannot see the guard without parsing conditions
it has no business parsing. A repository that takes contributions from
forks should know that the ratchet is enforced on the merge rather
than on the fork's pull request.

## How to fix it

Follow "Adopting it" in the template's README: copy the script
unmodified and check it with `git hash-object` against the template,
merge the hook stanza into `.pre-commit-config.yaml`, write the
baseline with `tools/source-size-ratchet.py --init`, and -- if CI
runs hooks individually rather than with `pre-commit run --all-files`
-- paste the template's CI job snippet into the workflow that gates
merges.

Make changes in the template, not in a repository's copy, and re-sync
the copies: the script's threshold is a constant rather than a flag so
that one review of the template is a review of every copy, and a copy
differing by a byte is a blob that has to be reviewed again everywhere
it is carried.

## Template

[templates/source-size-ratchet/](https://github.com/shakenfist/development/tree/main/templates/source-size-ratchet).
Its README describes what the script counts, what it does not, and
what the adopting repository writes for itself.

The reasoning behind the ratchet, the measurements that motivated it
and the pilot that settled how it is enforced are in
[PLAN-size-ratchet.md](../plans/PLAN-size-ratchet.md).

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](compliance.md#source-size-ratchet).
