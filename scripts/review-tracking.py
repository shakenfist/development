#!/usr/bin/env python3

"""Code review tracking helpers: stamp, prune, import, regen, next, status, scope-orphans.

This script implements the automation described in
docs/code-review-tracking.md. It runs in the repository under review,
invoked by hand -- deliberately not from git hooks, which proved
confusing when they fired in the middle of other git operations.
(Some subcommands also run from CI: prune then import from an
adopting repo's prune-reviews workflow, on every push to the default
branch and once a day, and status from the consistency audit's
review-coverage check; see the steady state section of the doc.)
Target repositories typically carry a thin wrapper (for example
ryll's tools/review-tracking.sh) that locates a clone of the
development repository and passes through to this script:

- stamp: record the blob SHA and date of newly reviewed files in a
  sidecar next to each weAudit state file, then regenerate REVIEWS.md.
  Run before committing new review marks; exits non-zero if it changed
  anything so the caller knows there is something to stage, or if a
  mark sits on a file the scope config excludes from review.
- prune: remove review marks (whole-file and region) for files whose
  content no longer matches the stamped blob SHA, then regenerate
  REVIEWS.md. Run after a pull, merge, or rebase; always exits zero.
- import: mark as reviewed every in-scope file whose blob at HEAD was
  fully reviewed on origin/main of the clone of shakenfist/development
  this script lives in, recording where the signed attestation lives
  in .vscode/imports.weaudit-shas.json, then regenerate REVIEWS.md.
  Never writes a reviewer's own state file or sidecar. Run in that
  clone itself, or a worktree of it, it says so and does nothing.
  Exits zero whether or not it imported anything -- a review it cannot
  verify is skipped with a warning, not an error -- and exits 1 when
  git cannot identify the target or source repository, or when it
  refuses to run beside a stray .vscode/imports.weaudit.
- regen: regenerate REVIEWS.md from the current state.
- next: pick a random in-scope file with no current review mark and
  open it in VSCode; with order = 'listed' in the scope config, the
  first such file in include-list order instead. Optional path
  arguments narrow the pick to files under those directories (or to
  those files); relative paths are repo-relative, since the wrapper
  runs from the repo root.
- status: report effective review coverage against HEAD -- which
  in-scope files carry a currently-valid review mark and which need
  review -- without modifying any state. --json emits a machine
  readable form for the consistency audit's review-coverage check.
- scope-orphans: list tracked files that are out of review scope only
  because no include pattern names them, as opposed to because an
  exclude entry says they should not be reviewed. Exits non-zero when
  there are any. Also runs from CI, in the consistency audit's
  review-scope-completeness check.

State read and written:

- .vscode/<user>.weaudit -- weAudit's own state (auditedFiles and
  partiallyAuditedFiles are read; prune rewrites them).
- .vscode/<user>.weaudit-shas.json -- the sidecar: blob SHA and date
  per reviewed path. weAudit never touches this file, so stamps cannot
  be clobbered by its save behaviour.
- .vscode/imports.weaudit-shas.json -- sidecar-shaped record of reviews
  imported from shakenfist/development, each pointing at the signed
  commit there that introduced it. There is deliberately no
  imports.weaudit beside it: weAudit reads every *.weaudit file and
  would show a tick for a file nobody read here.
- .vscode/review-scope.toml -- optional include/exclude fnmatch
  patterns defining which files are in scope for review, and an
  import-exclude list of files that must never be imported.
- REVIEWS.md -- generated summary of review state; never hand-edited.

Any of these that resolves outside the repository, is not a regular
file or exceeds the audit's read cap, or a weAudit mark on a path
carrying a control character, stops every subcommand with exit status
2 and a one-line reason; none is written through a symlink.
"""

import argparse
import datetime
import fnmatch
import glob
import io
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import unicodedata

# Every caller runs this file out of a full clone of
# shakenfist/development (the wrappers locate scripts/review-tracking.py
# in one), so scripts/ is sys.path[0] and the audit package beside it
# imports. The cap is shared rather than restated, so that this script
# and the audit cannot disagree about what is too large to read.
from audit.repo import READ_MAX_BYTES, path_is_within


DOCS_URL = ('https://github.com/shakenfist/development/blob/main/'
            'docs/code-review-tracking.md')
SCOPE_PATH = os.path.join('.vscode', 'review-scope.toml')
REVIEWS_PATH = 'REVIEWS.md'
SIDECAR_SUFFIX = '-shas.json'
SHORT_SHA = 12

# Rules the out-of-scope banner off from the per-file chatter around it.
RULE = '=' * 72

# The review tracking machinery itself is never a review target,
# whatever the repo's scope config says.
BUILTIN_EXCLUDE = ['.vscode/*', REVIEWS_PATH]

# Imported reviews live in a file shaped like a reviewer's sidecar but
# with no .weaudit state file beside it. weAudit loads every
# .vscode/*.weaudit and ticks a file from its path alone, and writes a
# change to an entry into <author>.weaudit -- so an imports.weaudit
# would show ticks for files nobody read here, and un-ticking one would
# write it into the original reviewer's own state file. weAudit never
# globs *-shas.json, so this file is invisible to it. The name also
# matches the .gitignore exception and paths-ignore entry adopted
# repositories already carry for sidecars.
IMPORTS_REVIEWER = 'imports'
IMPORTS_PATH = os.path.join('.vscode', IMPORTS_REVIEWER + '.weaudit' + SIDECAR_SUFFIX)

# Where imported reviews come from: the clone of shakenfist/development
# this script is running out of, which is the clone a target's wrapper
# already located. realpath so that a symlinked checkout still compares
# equal to itself in the self-import check.
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SOURCE_REPO = 'shakenfist/development'
SOURCE_LABEL = 'development'
# The history import walks: the source's default branch as last
# fetched, not whatever its working tree has checked out. A clone
# sitting on an unmerged review branch would otherwise export reviews
# that may never reach main, from commits a rebase may later discard.
SOURCE_BRANCH_REF = 'refs/remotes/origin/main'

# The signing identity each reviewer's review-state commits in the
# source must carry (docs/code-review-tracking.md, "Commit signing").
# Add an entry when a new reviewer starts signing review-state commits;
# until then their reviews cannot be verified, and are not imported.
REVIEWER_IDENTITIES = {
    'mikal': 'mikal@stillhq.com',
}
GITSIGN_ISSUER = 'https://github.com/login/oauth'
GITSIGN_TIMEOUT = 120
# How many verifications may time out in one import run before it stops
# trying. gitsign waits up to GITSIGN_TIMEOUT for Rekor, a network
# service, on every commit, and the prune-reviews job has thirty
# minutes: a Rekor that hangs would otherwise kill every run with more
# than about fifteen commits to verify, so that nothing ever lands.
# Three is six minutes, which rides out a blip without mistaking an
# outage for one; what is left is skipped, and a later run tries again.
VERIFY_TIMEOUT_BUDGET = 3


def git(*args, check=True, cwd=None):
    """Run git, by default in the current directory (the target repository).

    cwd is for the one caller that reads another repository -- import,
    which walks the source clone's history -- so that it never has to
    chdir away from the target and back.
    """
    p = subprocess.run(['git'] + list(args), capture_output=True, text=True, cwd=cwd)
    if check and p.returncode != 0:
        where = ' (in %s)' % cwd if cwd else ''
        raise RuntimeError('git %s failed%s: %s' % (' '.join(args), where, p.stderr.strip()))
    return p


def tracked_files():
    out = git('ls-files', '-z').stdout
    return [f for f in out.split('\0') if f]


def blob_sha(rev_path):
    """Return the blob SHA for e.g. ':path' (index) or 'HEAD:path', or None."""
    p = git('rev-parse', '--verify', rev_path, check=False)
    if p.returncode != 0:
        return None
    return p.stdout.strip()


def load_scope():
    """Return (include, exclude) fnmatch pattern lists from the scope config.

    Patterns use fnmatch semantics against the full repo-relative path, so '*'
    matches across directory separators ('src/*.rs' matches 'src/a/b.rs').
    An empty include list means every tracked file is included.

    An exclude entry beginning with '!' is a re-include: it puts back a file
    a broader exclude on the same list takes away. Without it the only way
    to exclude a directory except for one file is to name every other file
    by hand and edit that list whenever one is added.
    """
    data = load_scope_config()
    return list(data.get('include', [])), list(data.get('exclude', []))


def load_import_exclude():
    """Return the import-exclude fnmatch pattern list from the scope config.

    A separate loader rather than a third element of load_scope()'s
    tuple, so that every existing caller keeps unpacking two. The list
    has exactly the semantics of exclude, '!' re-includes and all (see
    excluded_by), and is how a target rejects an import: an imported
    review has no tick in weAudit to un-tick, and deleting the entry by
    hand would only see it re-imported on the next run.
    """
    return list(load_scope_config().get('import-exclude', []))


NEXT_ORDERS = ('random', 'listed')


def load_next_order(include):
    """Return how next picks among unread files, from the scope config.

    'random' (the default) suits an audit: it spreads attention across a
    backlog rather than always starting at its front. 'listed' suits
    reading to learn a codebase, where the order is the point: the
    include list is written as a reading path, and next offers the first
    unread file in it. Returns None, having said why, for an order that
    cannot work.
    """
    order = load_scope_config().get('order', 'random')
    if order not in NEXT_ORDERS:
        print('review-next: order = %r in %s is not one of %s'
              % (order, SCOPE_PATH, ', '.join(NEXT_ORDERS)), file=sys.stderr)
        return None
    if order == 'listed' and not include:
        print("review-next: order = 'listed' needs an include list to take the order from",
              file=sys.stderr)
        return None
    return order


def listed_position(path, include):
    """Sort key placing path at the first include pattern that names it.

    Files sharing a pattern fall back to path order, so a glob gives its
    files one position between them: name a file ahead of the glob to
    read it earlier than its neighbours.
    """
    for i, pat in enumerate(include):
        if fnmatch.fnmatch(path, pat):
            return (i, path)
    return (len(include), path)


def load_scope_config():
    data = read_state_bytes(SCOPE_PATH)
    if data is None:
        return {}
    import tomllib
    # What tomllib.load() does with a binary file: decode as UTF-8, strictly.
    return tomllib.loads(data.decode('utf-8'))


def excluded_by(path, patterns):
    """Does an exclude-style pattern list take this path away?

    A '!' entry re-includes, and is evaluated only when something else
    in the list has already matched -- so ordering within the list does
    not matter. Shared by the scope exclude list and import-exclude, so
    that the two cannot drift into meaning different things.
    """
    if not any(fnmatch.fnmatch(path, pat) for pat in patterns
               if not pat.startswith('!')):
        return False
    return not any(fnmatch.fnmatch(path, pat[1:]) for pat in patterns
                   if pat.startswith('!'))


def in_scope(path, include, exclude):
    """Is this path subject to whole-file review?

    A '!' re-include in exclude deliberately cannot override
    BUILTIN_EXCLUDE: the review state files describe the reviews and
    can never attest to themselves.
    """
    if any(fnmatch.fnmatch(path, pat) for pat in BUILTIN_EXCLUDE):
        return False
    if include and not any(fnmatch.fnmatch(path, pat) for pat in include):
        return False
    return not excluded_by(path, exclude)


def state_files():
    return sorted(f for f in glob.glob(os.path.join('.vscode', '*.weaudit')))


def sidecar_path(state_path):
    return state_path + SIDECAR_SUFFIX


def reviewer_name(state_path):
    return os.path.basename(state_path)[:-len('.weaudit')]


class RefusedState(Exception):
    """Review state this script will not read, write or act on.

    Raised rather than read as absent. The consistency audit runs status
    and scope-orphans inside every audited repository's checkout, and
    prune runs on every push to an adopting repository's default branch,
    so a state file is a path that repository chose: a symlink out of
    the checkout, a link to a device, or a file of any size. Reading
    such a file as absent would have status report a coverage figure
    the repository's own state does not support. main() reports the
    refusal as one line on stderr and exits 2, a status no subcommand
    uses for an answer, so the audit's review checks report the tooling
    failing rather than a verdict. The message names the path and the
    reason, never the content.
    """


def repository_root():
    """The target repository's toplevel, resolved: main() makes it the working directory."""
    return os.path.realpath(os.getcwd())


def read_state_bytes(path):
    """Return the bytes of the repository file at path, or None if nothing is there.

    Every read of the target's review state goes through here. The rules
    are audit.repo.read_within()'s, with each refusal raised instead of
    returned as None:

    - a path resolving outside the repository is refused before it is
      opened, so a link to /dev/zero or to a FIFO outside the checkout
      is never touched;
    - anything that is not a regular file once opened (a directory, a
      dangling link, a device) is refused. Checked with fstat on the
      open descriptor rather than beforehand, and opened non-blocking,
      so that the check is of the file actually read;
    - a file over READ_MAX_BYTES is refused rather than read in part,
      counting bytes as they are read rather than taken from a stat.

    Only a path that does not exist at all, link or otherwise, is absent.
    """
    if not os.path.lexists(path):
        return None
    if not path_is_within(repository_root(), path, root_is_real=True):
        raise RefusedState('%r resolves outside the repository; not reading it' % path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        raise RefusedState('%r cannot be opened: %s' % (path, e.strerror))
    # Before fdopen(), which raises its own error for a directory.
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RefusedState('%r is not a regular file; not reading it' % path)
    with os.fdopen(fd, 'rb') as f:
        data = f.read(READ_MAX_BYTES + 1)
    if len(data) > READ_MAX_BYTES:
        raise RefusedState('%r is larger than %d bytes; not reading it' % (path, READ_MAX_BYTES))
    return data


def read_state_text(path):
    """read_state_bytes(), decoded as text-mode open() would: UTF-8, universal newlines.

    UTF-8 strictly rather than in the locale, so that a decoding error is
    raised the same way wherever this runs; load_imports() relies on it.
    """
    data = read_state_bytes(path)
    if data is None:
        return None
    return io.TextIOWrapper(io.BytesIO(data), encoding='utf-8').read()


def check_writable(path):
    """Refuse to replace or remove path if it is a symlink or resolves outside the repository.

    A symlink at the final component is refused outright rather than
    resolved and checked: no review state file is legitimately a link,
    and prune holds a token that can push to the default branch, so a
    committed REVIEWS.md link would otherwise have it overwrite a file
    of the repository's choosing. With the final component known not to
    be a link, the containment check is of the parent directories.
    """
    if os.path.islink(path):
        raise RefusedState('%r is a symlink; not writing through it' % path)
    if not path_is_within(repository_root(), path, root_is_real=True):
        raise RefusedState('%r resolves outside the repository; not writing it' % path)


def write_state(path, content):
    """Write content (text) to the repository file at path, never through a symlink.

    O_NOFOLLOW holds the check_writable() rule at the open itself, for a
    link that appears between the two. The descriptor is checked to be a
    regular file before anything is truncated, and opened non-blocking so
    that a FIFO is an error rather than a wait.
    """
    check_writable(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o666)
    except OSError as e:
        raise RefusedState('%r cannot be written: %s' % (path, e.strerror))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RefusedState('%r is not a regular file; not writing it' % path)
    with os.fdopen(fd, 'wb') as f:
        f.truncate(0)
        f.write(content.encode('utf-8'))


def load_json(path, default):
    raw = read_state_text(path)
    if raw is None:
        return default, True
    return json.loads(raw), raw.endswith('\n')


def write_json(path, data, trailing_newline):
    write_state(path, json.dumps(data, indent=2) + ('\n' if trailing_newline else ''))


def unsafe_path(path):
    """Is path something no review state may name: not a string, or carrying a line break?

    A marked path becomes a REVIEWS.md table row verbatim, so a newline
    in one is a line of the repository's choosing in a generated file
    that prune commits. Every control character is rejected, and the
    Unicode line and paragraph separators, because str.splitlines()
    breaks a line at those too.
    """
    return not isinstance(path, str) or any(
        unicodedata.category(c) in ('Cc', 'Zl', 'Zp') for c in path)


def marked_paths(state, state_path):
    """Return (audited, partial) where audited is a list of paths and partial
    maps path -> list of (startLine, endLine).

    An unsafe path (see unsafe_path()) refuses the whole state file
    rather than being skipped. Skipping would leave a mark that stamp
    neither stamps nor reports, and it is a mark only a reviewer can
    remove: failing names the file, so whoever runs this can un-mark it.
    The path itself is not quoted, since it is what is unsafe to print.
    """
    audited = [e['path'] for e in state.get('auditedFiles', [])]
    partial = {}
    for e in state.get('partiallyAuditedFiles', []) or []:
        partial.setdefault(e['path'], []).append((e['startLine'], e['endLine']))
    if any(unsafe_path(p) for p in audited + list(partial)):
        raise RefusedState('%r marks a path that is not a string or contains a control character; '
                           'un-mark it in weAudit' % state_path)
    return audited, partial


def is_dir_entry(path, tracked_set):
    """weAudit adds a derived auditedFiles entry for a directory once every
    file in it is reviewed, alongside (never replacing) the per-file entries.
    The files carry the review state; directory entries are ignored for
    stamping and reporting, and dropped by prune when their invariant breaks.
    """
    if path in tracked_set:
        return False
    prefix = path.rstrip('/') + '/'
    return any(t.startswith(prefix) for t in tracked_set)


def load_imports():
    """Return (imports, trailing_newline) for the imports file.

    A separate read path from state_files(), which globs *.weaudit only,
    so that stamp -- which walks state_files() and nothing else -- never
    sees an imported entry. Nothing in this file was marked in this
    clone, so there is nothing for stamp to stamp or drop.
    """
    try:
        imports, nl = load_json(IMPORTS_PATH, {'version': 1, 'files': {}})
    except (ValueError, UnicodeDecodeError) as e:
        print('review-tracking: WARNING: %s is not valid JSON (%s); ignoring it' % (IMPORTS_PATH, e),
              file=sys.stderr)
        imports, nl = {'version': 1, 'files': {}}, True
    if not isinstance(imports, dict):
        print('review-tracking: WARNING: %s is not a JSON object; ignoring it' % IMPORTS_PATH, file=sys.stderr)
        imports = {'version': 1, 'files': {}}
    malformed = drop_malformed_imports(imports)
    return imports, nl, malformed


def drop_malformed_imports(imports):
    """Remove, and report, imports entries not shaped as import writes them. Returns their paths.

    The one place the imports file's shape is checked, so that nothing
    reading it needs a guard of its own. Unlike the source's history,
    which import walks defensively entry by entry, this file is the
    target's own and can be hand-edited: an entry import did not write
    is treated as absent, and import or prune drops it from the file
    the next time either writes it. (A whole file that is not a JSON
    object, load_imports() treats as empty, with the same result.)
    """
    entries = imports.get('files')
    if not isinstance(entries, dict):
        if entries is not None:
            print('review-tracking: WARNING: %s "files" is not an object; ignoring it' % IMPORTS_PATH,
                  file=sys.stderr)
        imports['files'] = {}
        return []
    malformed = []
    for path in sorted(entries):
        entry = entries[path]
        imported = entry.get('imported') if isinstance(entry, dict) else None
        # An unsafe path would put a line of its own in REVIEWS.md, as a
        # marked one would (see marked_paths()). Dropped here rather than
        # refused, because this file is machine-written and dropping is
        # how this file treats every other entry import did not write.
        if (not unsafe_path(path) and isinstance(entry, dict) and isinstance(entry.get('sha'), str)
                and isinstance(imported, dict)
                and all(isinstance(imported.get(k), str) for k in ('repo', 'reviewer', 'path', 'commit'))):
            continue
        # json.dumps for the path too, so that an unsafe one is escaped on stderr.
        print('review-tracking: WARNING: %s entry for %s is malformed; ignoring it (%s)'
              % (IMPORTS_PATH, path if not unsafe_path(path) else json.dumps(path),
                 json.dumps(entry, sort_keys=True)), file=sys.stderr)
        del entries[path]
        malformed.append(path)
    return malformed


def write_imports(imports, trailing_newline):
    """Write the imports file, or remove it once it holds nothing.

    Removed rather than left as an empty shell so that a repository with
    no imports carries no imports file at all, which is the state it
    was in before import first ran.
    """
    files = imports.get('files', {})
    if not files:
        if os.path.lexists(IMPORTS_PATH):
            check_writable(IMPORTS_PATH)
            os.remove(IMPORTS_PATH)
        return
    imports['files'] = dict(sorted(files.items()))
    # The only write in this script whose directory may not exist yet:
    # every other one rewrites a file beside a state file already read
    # from .vscode, or REVIEWS.md at the top level. A repository with
    # no scope config and no reviewer of its own has no .vscode at all.
    os.makedirs(os.path.dirname(IMPORTS_PATH), exist_ok=True)
    write_json(IMPORTS_PATH, imports, trailing_newline)


def head_blobs():
    """Map every path at HEAD to its blob SHA, in one git call.

    import asks about every in-scope file, and a rev-parse per file is
    a process per file; in the larger adopted repositories that is
    thousands of them for an answer ls-tree gives at once.
    """
    p = git('ls-tree', '-r', '-z', '--full-tree', 'HEAD', check=False)
    if p.returncode != 0:
        return {}
    blobs = {}
    for record in p.stdout.split('\0'):
        if not record:
            continue
        meta, path = record.split('\t', 1)
        _mode, kind, sha = meta.split()
        if kind == 'blob':
            blobs[path] = sha
    return blobs


def native_marks(tracked, current_sha):
    """Return (marked, valid) for the reviewers' own state files.

    marked maps each path carrying a full-file mark (directory entries
    aside) to the reviewer who made it; valid is the subset whose
    stamped blob SHA is the file's current content, as reported by
    current_sha(path). A mark without a stamp cannot be verified
    against any content, so it is never valid. Partial (region) marks
    are neither.
    """
    marked = {}
    valid = set()
    for state_path in state_files():
        reviewer = reviewer_name(state_path)
        state, _ = load_json(state_path, {})
        sidecar, _ = load_json(sidecar_path(state_path), {'version': 1, 'files': {}})
        stamps = sidecar.get('files', {})
        audited, _partial = marked_paths(state, state_path)
        for path in audited:
            if is_dir_entry(path, tracked):
                continue
            marked.setdefault(path, reviewer)
            stamp = stamps.get(path)
            if stamp is not None and stamp.get('sha') is not None and current_sha(path) == stamp['sha']:
                valid.add(path)
    return marked, valid


def import_source_label(entry):
    """The REVIEWS.md Source cell for an imported entry.

    An entry imported under --no-verify says so here, where a reader of
    REVIEWS.md will see it, rather than only in the imports file.
    """
    imported = entry.get('imported', {})
    label = '%s@%s' % (SOURCE_LABEL, imported.get('commit', '-')[:SHORT_SHA])
    if imported.get('verified') is False:
        label += ' (unverified)'
    return label


def render_reviews_md():
    """Return the REVIEWS.md content implied by the committed review state.

    Split out from generate_reviews_md() so that a test can compare the
    rendering against the checked-in file without writing to it. That
    comparison is the only thing standing between a review commit and an
    unreproducible REVIEWS.md: the header count trusts marks rather than
    stamps (see review_status), so a commit that forgets the sidecar
    still reports the right count while every Date and Blob SHA cell
    silently renders as '-'.
    """
    include, exclude = load_scope()
    tracked = set(tracked_files())
    scoped = sorted(p for p in tracked if in_scope(p, include, exclude))

    full_rows = []
    partial_rows = []
    reviewed_paths = set()
    for state_path in state_files():
        reviewer = reviewer_name(state_path)
        state, _ = load_json(state_path, {})
        sidecar, _ = load_json(sidecar_path(state_path), {'version': 1, 'files': {}})
        stamps = sidecar.get('files', {})
        audited, partial = marked_paths(state, state_path)
        audited = [p for p in audited if not is_dir_entry(p, tracked)]
        for path in audited:
            stamp = stamps.get(path, {})
            reviewed_paths.add(path)
            full_rows.append((path, reviewer, stamp.get('date', '-'),
                              stamp.get('sha', '-')[:SHORT_SHA], '-'))
        for path, regions in sorted(partial.items()):
            stamp = stamps.get(path, {})
            lines = ', '.join('%d-%d' % (s, e) for s, e in sorted(regions))
            partial_rows.append((path, lines, reviewer, stamp.get('date', '-'),
                                 stamp.get('sha', '-')[:SHORT_SHA]))

    # Imported reviews count toward the header and get a row, like a
    # native mark, and like a native mark they are trusted here rather
    # than checked against HEAD (see review_status). import removes an
    # entry a native review supersedes; the path check below keeps a
    # file to one row, the human's, even if that has not happened yet.
    imports, _, _ = load_imports()
    native_paths = set(reviewed_paths)
    for path, entry in sorted(imports.get('files', {}).items()):
        if path in native_paths:
            continue
        reviewed_paths.add(path)
        full_rows.append((path, entry.get('imported', {}).get('reviewer', '-'), entry.get('date', '-'),
                          entry.get('sha', '-')[:SHORT_SHA], import_source_label(entry)))

    reviewed_in_scope = len([p for p in reviewed_paths if p in set(scoped)])
    out = []
    out.append('# Code review status')
    out.append('')
    out.append('The code in this repository receives periodic whole-file human')
    out.append('review, looking for the inconsistencies that creep into a codebase')
    out.append('over time. This is in addition to the more usual review of changes')
    out.append('at pull request time. Each review is recorded by a signed commit')
    out.append('binding the reviewer, date, and exact content reviewed; reviews are')
    out.append('automatically discarded when the file later changes.')
    out.append('')
    out.append('This file is generated by the review tracking tooling -- do not')
    out.append('edit it by hand. See %s' % DOCS_URL)
    out.append('for how this works, including how to verify the attestations.')
    out.append('')
    out.append('%d of %d in-scope files are currently reviewed.' % (reviewed_in_scope, len(scoped)))
    out.append('')
    out.append('## Reviewed files')
    out.append('')
    if full_rows:
        out.append('| File | Reviewer | Date | Blob SHA | Source |')
        out.append('|------|----------|------|----------|--------|')
        for path, reviewer, date, sha, source in sorted(full_rows):
            out.append('| %s | %s | %s | %s | %s |' % (path, reviewer, date, sha, source))
    else:
        out.append('No files are currently reviewed.')
    if partial_rows:
        out.append('')
        out.append('## Partially reviewed files')
        out.append('')
        out.append('| File | Lines | Reviewer | Date | Blob SHA |')
        out.append('|------|-------|----------|------|----------|')
        for path, lines, reviewer, date, sha in sorted(partial_rows):
            out.append('| %s | %s | %s | %s | %s |' % (path, lines, reviewer, date, sha))
    return '\n'.join(out) + '\n'


def generate_reviews_md():
    """Regenerate REVIEWS.md. Returns True if the file changed."""
    content = render_reviews_md()
    if content == read_state_text(REVIEWS_PATH):
        return False
    write_state(REVIEWS_PATH, content)
    return True


def report_out_of_scope(paths):
    """Announce marks on files the scope config excludes from review.

    Loudly, and at the end of the run rather than in the middle of it.
    Reviewing an out-of-scope file is easy to do by accident and hard
    to notice afterwards, because the failure is silent in both
    directions: the file does appear in the REVIEWS.md table, so the
    review looks recorded, but the coverage count above that table
    only counts in-scope files and does not move. `status` cannot see
    it either, so the review-coverage audit still reports the file as
    outstanding, and `next` will never offer it because it was never
    in the queue. The reviewer reads a file carefully and the number
    they are trying to move stays where it was.

    Reported on every run, not only the run that first stamps the
    file: a mark noticed once and left alone is exactly the case that
    needs saying again.
    """
    if not paths:
        return
    # The per-file lines above go to stdout, which is block-buffered
    # whenever stamp is piped or redirected -- so without this flush the
    # banner is emitted first and lands at the top of the output, which
    # is the one place it was never meant to be.
    sys.stdout.flush()
    lines = ['', RULE,
             'review-stamp: %d file(s) MARKED REVIEWED BUT OUT OF REVIEW SCOPE' % len(paths),
             RULE]
    lines.extend('    %s' % path for path in paths)
    lines.extend([
        '',
        'These are excluded by %s, so reviewing them' % SCOPE_PATH,
        'did not count. They do get a row in the %s table, which is what' % REVIEWS_PATH,
        'makes this easy to miss, but the coverage number above that table counts',
        'in-scope files only and has not moved. `status` cannot see them either, so',
        'the review-coverage audit still considers them outstanding, and `next`',
        'never offered them in the first place.',
        '',
        'If reviewing them was a mistake, un-mark them in weAudit. If they should',
        'be reviewed, widen the scope config -- and say why in the commit message,',
        'because the exclusions there are argued rather than incidental.',
        RULE])
    print('\n'.join(lines), file=sys.stderr)


def cmd_stamp(_args):
    """Record the reviewed content of every marked file in the sidecar.

    Stamps are taken against the index rather than HEAD, because the
    commit being prepared is the one the stamp belongs to.

    A file that is already stamped and has since changed is reported
    and never re-stamped. Re-stamping it would move the attestation
    onto content nobody has read, which is the single thing this
    tooling exists to prevent; and until this was checked, stamp
    skipped such a file in silence, so the stale mark survived the
    commit, survived CI (review-only commits are path-ignored) and was
    then deleted by the prune the first push to the default branch
    runs -- discarding the review rather than the staleness.
    """
    include, exclude = load_scope()
    tracked = set(tracked_files())
    staged = set(git('diff', '--cached', '--name-only').stdout.splitlines())
    changed = []
    stale = []
    out_of_scope = []
    for state_path in state_files():
        state, _ = load_json(state_path, {})
        side_path = sidecar_path(state_path)
        sidecar, side_nl = load_json(side_path, {'version': 1, 'files': {}})
        stamps = sidecar.setdefault('files', {})
        audited, partial = marked_paths(state, state_path)
        marked = set(p for p in set(audited) | set(partial) if not is_dir_entry(p, tracked))
        out_of_scope.extend(p for p in marked if not in_scope(p, include, exclude))

        side_changed = False
        for path in sorted(marked - set(stamps)):
            sha = blob_sha(':%s' % path)
            if sha is None:
                print('review-stamp: WARNING: %s is marked reviewed but not in the git index; '
                      'not stamping it' % path, file=sys.stderr)
                continue
            if path in staged:
                print('review-stamp: WARNING: %s is marked reviewed but has changes staged in this '
                      'commit; the stamp attests to the staged content' % path, file=sys.stderr)
            stamps[path] = {'sha': sha, 'date': datetime.date.today().isoformat()}
            print('review-stamp: stamped %s at %s' % (path, sha[:SHORT_SHA]))
            side_changed = True
        for path in sorted(marked & set(stamps)):
            sha = blob_sha(':%s' % path)
            # `is not None and ==` rather than a bare ==: a sidecar
            # entry with no sha at all compares equal to a file that
            # has left the index, and two unknowns are not a match.
            recorded = stamps[path].get('sha')
            if recorded is not None and sha == recorded:
                continue
            stale.append(path)
            if sha is None:
                print('review-stamp: ERROR: %s is marked reviewed and stamped but is no longer '
                      'in the git index' % path, file=sys.stderr)
            else:
                print('review-stamp: ERROR: %s is stamped at %s but its content is now %s'
                      % (path, (recorded or 'nothing')[:SHORT_SHA], sha[:SHORT_SHA]),
                      file=sys.stderr)
        for path in sorted(set(stamps) - marked):
            del stamps[path]
            print('review-stamp: dropped stamp for unmarked file %s' % path)
            side_changed = True

        if side_changed:
            sidecar['files'] = dict(sorted(stamps.items()))
            write_json(side_path, sidecar, side_nl or not os.path.exists(side_path))
            changed.append(side_path)

    if generate_reviews_md():
        changed.append(REVIEWS_PATH)
    if changed:
        print('review-stamp: updated %s; stage the changes (git add %s) and include them in the '
              'review-state commit' % (', '.join(changed), ' '.join(changed)))
    if stale:
        print('review-stamp: run `review-tracking.py prune` to drop the stale mark(s), then '
              're-review those files and mark them again in weAudit. They are deliberately not '
              're-stamped: a stamp nobody read the content for is a false attestation.',
              file=sys.stderr)
    report_out_of_scope(sorted(set(out_of_scope)))
    return 1 if changed or stale or out_of_scope else 0


def cmd_prune(_args):
    pruned = []
    tracked = set(tracked_files())
    for state_path in state_files():
        state, state_nl = load_json(state_path, {})
        # prune reads no marked path itself, but the REVIEWS.md render at
        # the end does; refusing here, before any state file is
        # rewritten, keeps a refused run from writing half its result.
        marked_paths(state, state_path)
        side_path = sidecar_path(state_path)
        sidecar, side_nl = load_json(side_path, {'version': 1, 'files': {}})
        stamps = sidecar.get('files', {})

        stale = []
        for path in sorted(stamps):
            current = blob_sha('HEAD:%s' % path)
            if current != stamps[path]['sha']:
                stale.append((path, stamps[path], current))
        if not stale:
            continue

        for path, stamp, current in stale:
            del stamps[path]
            now = current[:SHORT_SHA] if current else 'gone'
            print('review-prune: %s changed since its review (%s, %s -> %s); treating as unreviewed'
                  % (path, stamp.get('date', 'undated'), stamp['sha'][:SHORT_SHA], now))
        stale_paths = set(path for path, _, _ in stale)
        state['auditedFiles'] = [e for e in state.get('auditedFiles', [])
                                 if e['path'] not in stale_paths]
        if state.get('partiallyAuditedFiles'):
            state['partiallyAuditedFiles'] = [e for e in state['partiallyAuditedFiles']
                                              if e['path'] not in stale_paths]

        # weAudit adds a derived directory entry once every file in that
        # directory is reviewed, and removes it itself when a file is
        # unmarked in its UI; replicate the removal for pruned files.
        audited_set = set(e['path'] for e in state['auditedFiles'])
        kept = []
        for e in state['auditedFiles']:
            path = e['path']
            if is_dir_entry(path, tracked):
                prefix = path.rstrip('/') + '/'
                if any(t.startswith(prefix) and t not in audited_set for t in tracked):
                    print('review-prune: removing directory mark %s (no longer fully reviewed)' % path)
                    continue
            kept.append(e)
        state['auditedFiles'] = kept
        write_json(state_path, state, state_nl)
        write_json(side_path, sidecar, side_nl)
        pruned.extend(sorted(stale_paths))

    pruned.extend(prune_imports())

    regenerated = generate_reviews_md()
    if pruned:
        print('review-prune: pruned %d stale review(s); commit the updated review state '
              '(signed) at the end of your session' % len(pruned))
        print('review-prune: if VSCode is already open, run "weAudit: Toggle Tree View Mode" or '
              'reload the window to refresh the ticks')
    elif regenerated:
        print('review-prune: regenerated %s' % REVIEWS_PATH)
    return 0


def prune_imports():
    """Drop imported reviews whose blob is no longer the file's content.

    Exactly the rule prune applies to a native stamp: an import attests
    to a blob SHA, not a path, so once the file changes the import says
    nothing about it. Returns the paths dropped.
    """
    imports, nl, malformed = load_imports()
    stale = drop_stale_imports(imports['files'], lambda path: blob_sha('HEAD:%s' % path), 'review-prune')
    if stale or malformed:
        write_imports(imports, nl)
    return malformed + stale


def drop_stale_imports(entries, current_sha, prefix):
    """Remove, and report, entries whose blob is not current_sha(path). Returns their paths.

    Shared by prune and import so that the two apply one rule and say
    the same thing when they apply it.
    """
    stale = []
    for path in sorted(entries):
        entry = entries[path]
        current = current_sha(path)
        recorded = entry.get('sha')
        if recorded is not None and current == recorded:
            continue
        stale.append(path)
        now = current[:SHORT_SHA] if current else 'gone'
        print('%s: %s changed since its review (%s, %s -> %s, imported from %s); '
              'treating as unreviewed'
              % (prefix, path, entry.get('date', 'undated'), (recorded or 'nothing')[:SHORT_SHA], now,
                 import_source_label(entry)))
        del entries[path]
    return stale


def read_blobs(cwd, specs):
    """Return {spec: bytes or None} for '<rev>:<path>' specs, in one git process.

    The import history walk reads two files at every commit that
    touched review state, which is a few hundred git show calls in the
    source today and grows with every review session; cat-file --batch
    answers all of them at once. A spec naming nothing (a file that did
    not exist yet at that commit) maps to None.
    """
    if not specs:
        return {}
    p = subprocess.run(['git', 'cat-file', '--batch'], input=('\n'.join(specs) + '\n').encode(),
                       capture_output=True, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError('git cat-file --batch failed (in %s): %s'
                           % (cwd, p.stderr.decode(errors='replace').strip()))
    out = p.stdout
    pos = 0
    result = {}
    for spec in specs:
        end = out.index(b'\n', pos)
        header = out[pos:end].decode(errors='replace').split()
        pos = end + 1
        if len(header) != 3 or not header[2].isdigit():
            # '<spec> missing' (or ambiguous): there is no object body.
            result[spec] = None
            continue
        size = int(header[2])
        result[spec] = out[pos:pos + size] if header[1] == 'blob' else None
        pos += size + 1
    return result


def parse_state(raw):
    """Parse a historical state file or sidecar, or None if it cannot be.

    A commit in the source's history holding a file that is not valid
    JSON (a botched merge, say) attests to nothing; it is skipped rather
    than allowed to stop every import across the fleet. Valid JSON of
    the wrong shape inside the object is the walk's to skip, entry by
    entry (full_marks(), and the stamp checks in reviewed_blobs()):
    the history is permanent, so one malformed entry that raised would
    break every adopted repository's import until the code changed.
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def full_marks(state):
    """Return the set of paths a historical state file marks as fully reviewed.

    Entries that are not an object with a string path are skipped, as
    is an auditedFiles that is not a list (see parse_state()).
    """
    audited = state.get('auditedFiles')
    if not isinstance(audited, list):
        return set()
    return set(e['path'] for e in audited if isinstance(e, dict) and isinstance(e.get('path'), str))


def source_revision(source):
    """Return the revision of the source whose history import walks.

    SOURCE_BRANCH_REF when the source has it, as every clone of
    shakenfist/development does, CI's fresh one included. A source
    without it (a clone with no origin, say) falls back to HEAD and
    says so, since that is a branch nobody has vouched for.
    """
    if git('rev-parse', '--verify', '--quiet', SOURCE_BRANCH_REF + '^{commit}', check=False,
           cwd=source).returncode == 0:
        return SOURCE_BRANCH_REF
    print('review-import: WARNING: %s has no %s, so reviews are imported from its HEAD instead'
          % (source, SOURCE_BRANCH_REF), file=sys.stderr)
    return 'HEAD'


def reviewed_blobs(source, revision):
    """Map every blob the source has fully reviewed to where that review happened.

    Returns (blobs, attested). blobs is {blob sha: {'reviewer', 'path',
    'commit', 'date'}}, where commit is the first commit in the source
    at which the reviewer's sidecar stamped that blob and the same
    commit's state file carried a full-file mark for the stamped path.
    Earliest wins across reviewers too: the first attestation is the one
    recorded, and the one a new import points at.

    attested is the set of (sha, reviewer, path, commit) for every
    attestation the history holds, not only the earliest per blob:
    every commit that introduced a reviewer's full review of a path at
    a blob, meaning the review is in that commit's state and in none of
    its parents'. It is what an existing import is checked against. One
    review can be introduced more than once -- on a branch merged after
    another made the same review, or by re-stamping a blob whose mark
    was pruned in between -- and an entry pointing at any of those
    commits points at a real introducing commit, even if one earlier
    sorts first. An entry pointing at a commit that merely carries the
    review forward (an unsigned prune, or the merge of a review branch)
    points at none.

    Only the history reachable from revision (source_revision()) is
    walked. The whole history, not just its tip, because the common
    case during a rollout is a target copy lagging a template the source
    has since changed and re-reviewed; only history still records the
    review of the older blob.

    The sidecar alone is not enough. stamp also stamps files that carry
    only partial (region) marks, so a stamped blob proves nothing about
    how much of it was read; only a full-file auditedFiles entry in the
    same commit's state file does. A derived directory entry is not a
    review of anything, which is why is_dir_entry needs the source
    commit's own file list.

    Commits touching either file are walked, not just those touching a
    sidecar. Upgrading a partial mark to a full one changes only the
    state file -- stamp never restamps an already-stamped blob -- so
    the commit that first attests to the full review may leave the
    sidecar alone.
    """
    state_suffix = '.weaudit'
    sidecar_suffix = state_suffix + SIDECAR_SUFFIX
    pathspecs = ['.vscode/*' + sidecar_suffix, '.vscode/*' + state_suffix]

    names = git('log', '--full-history', '--format=', '--name-only', revision, '--', *pathspecs,
                cwd=source).stdout.splitlines()
    reviewers = sorted(set(
        os.path.basename(n)[:-len(sidecar_suffix)] for n in names
        if os.path.dirname(n) == '.vscode' and n.endswith(sidecar_suffix)))
    # Should the source ever carry imports of its own, they are not
    # reviews made there and must not be re-exported as if they were.
    reviewers = [r for r in reviewers if r != IMPORTS_REVIEWER]
    if not reviewers:
        return {}, set()

    # Oldest first, and never a commit before one of its ancestors, so
    # "first commit at which it qualifies" means the one that
    # introduced the attestation rather than a later one repeating it.
    # Each commit's parents are read too: an attestation is introduced
    # at a commit where it qualifies and no parent's state did.
    # --full-history, because git's default simplification follows only
    # the matching parent of a merge whose review state matches one of
    # them, and so never walks a review branch merged after main made
    # the same review -- whose commits introduced it too.
    walk = git('log', '--full-history', '--reverse', '--date-order', '--format=%H %P', revision, '--',
               *pathspecs, cwd=source).stdout.splitlines()
    parents = {}
    for line in walk:
        commit, *rest = line.split()
        parents[commit] = rest
    revs = sorted(set(parents) | set(p for ps in parents.values() for p in ps))
    specs = []
    for rev in revs:
        for reviewer in reviewers:
            specs.append('%s:.vscode/%s%s' % (rev, reviewer, sidecar_suffix))
            specs.append('%s:.vscode/%s%s' % (rev, reviewer, state_suffix))
    contents = read_blobs(source, specs)

    stamped = {}

    def fully_stamped(rev, reviewer):
        """{(sha, path): stamp} the reviewer's sidecar stamps at rev with a full mark beside it."""
        if (rev, reviewer) not in stamped:
            found = {}
            sidecar = parse_state(contents['%s:.vscode/%s%s' % (rev, reviewer, sidecar_suffix)])
            state = parse_state(contents['%s:.vscode/%s%s' % (rev, reviewer, state_suffix)])
            stamps = sidecar.get('files') if sidecar is not None and state is not None else None
            if isinstance(stamps, dict):
                full = full_marks(state)
                for path, stamp in stamps.items():
                    sha = stamp.get('sha') if isinstance(stamp, dict) else None
                    if isinstance(sha, str) and sha and path in full:
                        found[(sha, path)] = stamp
            stamped[(rev, reviewer)] = found
        return stamped[(rev, reviewer)]

    trees = {}

    def attests(rev, reviewer, sha, path):
        """Whether rev's state holds the reviewer's full review of path at sha."""
        if (sha, path) not in fully_stamped(rev, reviewer):
            return False
        # Only fetched for a candidate, which is a small fraction of
        # the commits.
        if rev not in trees:
            listing = git('ls-tree', '-r', '-z', '--name-only', rev, cwd=source).stdout
            trees[rev] = set(f for f in listing.split('\0') if f)
        return not is_dir_entry(path, trees[rev])

    blobs = {}
    attested = set()
    for commit in parents:
        for reviewer in reviewers:
            for (sha, path), stamp in sorted(fully_stamped(commit, reviewer).items()):
                # Every parent is checked, not just the first, so that
                # a merge carrying a review branch's attestation onto
                # main is not mistaken for its introduction; the branch
                # commit that made it is.
                if any(attests(p, reviewer, sha, path) for p in parents[commit]):
                    continue
                if not attests(commit, reviewer, sha, path):
                    continue
                attested.add((sha, reviewer, path, commit))
                if sha in blobs:
                    continue
                date = stamp.get('date')
                blobs[sha] = {'reviewer': reviewer, 'path': path, 'commit': commit,
                              'date': date if isinstance(date, str) else '-'}
    return blobs, attested


def attestation_of(entry):
    """Return an imports entry's (sha, reviewer, path, commit), or None if from another source.

    The key reviewed_blobs() records in attested, so that an entry can
    be checked against the history it claims to come from. An entry
    naming another repository has no such history -- import reads
    SOURCE_REPO alone and never writes one -- so it is unsupported like
    any other entry the history does not vouch for.
    """
    imported = entry['imported']
    if imported['repo'] != SOURCE_REPO:
        return None
    return (entry['sha'], imported['reviewer'], imported['path'], imported['commit'])


def unsupported_reason(entry):
    """Say why an imports entry is not an attestation the source's history holds."""
    if entry['imported']['repo'] != SOURCE_REPO:
        return 'it names %s, and imports come from %s alone' % (entry['imported']['repo'], SOURCE_REPO)
    return '%s history has no such attestation' % SOURCE_REPO


def verify_commit(git_dir, commit, identity):
    """Check a source commit's gitsign signature by identity. Returns (ok, detail, timed_out).

    identity is the certificate identity the reviewer who made the
    review signs as (REVIEWER_IDENTITIES): a commit signed by anyone
    else is not that reviewer's attestation.

    git_dir is the source's common git directory, not its working tree.
    gitsign reads the repository through go-git, which cannot follow a
    linked worktree's .git file to the shared object store and reports
    every commit as "reference not found" from one; the common git
    directory opens as a repository whether the source is a plain clone
    (it is then just .git) or one of several worktrees.

    The one place import decides whether to believe an attestation, so
    that a test can replace it. Checked here, at import time, rather
    than merely recorded: an unsigned commit forging a stamp on the
    source's default branch would otherwise mark the file reviewed in
    every adopted repository, not just the one it was pushed to. A
    gitsign that cannot run, or runs past the timeout (Rekor is a
    network service), is a failure: an unverifiable signature is not a
    verified one. timed_out says which of those it was, so that import
    can stop asking a Rekor that is not answering (VERIFY_TIMEOUT_BUDGET).
    """
    try:
        p = subprocess.run(['gitsign', 'verify',
                            '--certificate-identity=%s' % identity,
                            '--certificate-oidc-issuer=%s' % GITSIGN_ISSUER,
                            commit],
                           capture_output=True, text=True, cwd=git_dir, timeout=GITSIGN_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        return False, str(e), True
    except OSError as e:
        return False, str(e), False
    detail = (p.stderr.strip() or p.stdout.strip()).splitlines()
    return p.returncode == 0, detail[-1] if detail else 'exit status %d' % p.returncode, False


def repository_identity(path):
    """Return (top level, common git dir) for the repository at path, both real paths.

    The common git dir is what makes a worktree of the source compare
    equal to the source: each worktree has its own top level, but they
    share one repository.
    """
    top = git('rev-parse', '--show-toplevel', cwd=path).stdout.strip()
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=path).stdout.strip()
    return os.path.realpath(top), os.path.realpath(common)


def cmd_import(args):
    """Record, in the imports file, reviews the source made of identical blobs.

    A separate subcommand from prune so that prune stays remove-only,
    which is the property its unsigned bot commits rest on, and so that
    the one path that adds marks without a human can be read, tested
    and switched off on its own. The bot's import commit is unsigned
    too, which is why every entry points at the signed source commit
    that introduced the review: the entry is a pointer to an
    attestation, not an attestation.

    The prune job runs this, and prune, in a depth 1 clone of the
    target, so neither may read the target's history; only the source's
    is walked. test_prune_and_import_work_in_a_shallow_clone_of_the_target
    holds them to that.
    """
    try:
        target_id = repository_identity(os.getcwd())
        source_id = repository_identity(SOURCE_ROOT)
    except RuntimeError as e:
        print('review-import: ERROR: %s' % e, file=sys.stderr)
        return 1
    if target_id[0] == source_id[0] or target_id[1] == source_id[1]:
        # A no-op rather than an error, so that the shared prune-reviews
        # template can run import unchanged in every repository,
        # including the one the reviews come from. Nothing is written,
        # REVIEWS.md included.
        print('review-import: nothing to do; %s is the source of imported reviews' % SOURCE_REPO)
        return 0
    stray = os.path.join('.vscode', IMPORTS_REVIEWER + '.weaudit')
    if os.path.exists(stray):
        # Its sidecar would be the imports file, so a reviewer of this
        # name would have their stamps rewritten by import and imports
        # shown as ticks by weAudit -- both things the separate file
        # exists to prevent.
        print('review-import: ERROR: %s exists, so %s would be read as its sidecar; a reviewer '
              'named "%s" cannot coexist with imported reviews' % (stray, IMPORTS_PATH, IMPORTS_REVIEWER),
              file=sys.stderr)
        return 1

    verify = not args.no_verify
    # Without gitsign nothing can be verified, and the only way to
    # import unverified is to ask for it. Falling back to "verified":
    # false instead would let a runner that merely lacks gitsign mark
    # files reviewed on the strength of commits nobody checked. The run
    # still exits zero, so such a CI job does not fail, and it still
    # removes what should go, which needs no verification.
    gitsign_missing = verify and shutil.which('gitsign') is None
    if not verify:
        print('review-import: WARNING: signature verification disabled by --no-verify; reviews '
              'imported by this run are recorded with "verified": false', file=sys.stderr)
    shallow = git('rev-parse', '--is-shallow-repository', cwd=SOURCE_ROOT).stdout.strip() == 'true'
    if shallow:
        print('review-import: WARNING: %s is a shallow clone, so reviews older than its history '
              'cannot be found; fetch its full history to import them' % SOURCE_ROOT, file=sys.stderr)

    revision = source_revision(SOURCE_ROOT)
    blobs, attested = reviewed_blobs(SOURCE_ROOT, revision)
    include, exclude = load_scope()
    import_exclude = load_import_exclude()
    tracked = set(tracked_files())
    head = head_blobs()
    native, valid = native_marks(tracked, head.get)
    imports, nl, removed = load_imports()
    entries = imports['files']

    # First, what this run should no longer be carrying (a malformed
    # entry, load_imports() has already dropped). A stale entry (its sha
    # no longer HEAD's) is left for now: it is replaced below if the new
    # content was reviewed too, and removed after that if not.
    for path in sorted(entries):
        if path in valid:
            reason = 'superseded by a native review by %s' % native[path]
        elif excluded_by(path, import_exclude):
            reason = 'matches import-exclude in %s' % SCOPE_PATH
        elif not in_scope(path, include, exclude):
            reason = 'no longer in review scope'
        else:
            continue
        del entries[path]
        removed.append(path)
        print('review-import: removed import of %s (%s)' % (path, reason))

    # Then entries the source's history does not support: their sha,
    # reviewer, source path and commit are not an attestation the walk
    # found. That is a hand-forged entry, or one imported from a commit
    # that has since left the history (a branch rebased away, or one
    # never merged, from before import walked origin/main only). Each
    # is treated as absent below, so a real review of the same content
    # replaces it, and whatever is not replaced is removed at the end --
    # if this run can judge it at all.
    unsupported = set(path for path in entries if attestation_of(entries[path]) not in attested)

    verified = {}
    added = []
    unverified = 0
    no_identity = 0
    blocked = 0
    timeouts = 0
    timed_out = 0
    deferred = 0
    for path in sorted(tracked):
        if not in_scope(path, include, exclude) or excluded_by(path, import_exclude):
            continue
        # Any full-file native mark keeps the file out, a stale one
        # included: until prune has removed it, the file's own review
        # history says it needs a human, and an import would hide that.
        # A partial mark alone does not, and its row stays beside the
        # import.
        if path in native:
            continue
        sha = head.get(path)
        if sha is None or sha not in blobs:
            continue
        existing = entries.get(path)
        if existing is not None and existing['sha'] == sha and path not in unsupported:
            continue
        origin = blobs[sha]
        commit = origin['commit']
        if gitsign_missing:
            blocked += 1
            continue
        if verify:
            identity = REVIEWER_IDENTITIES.get(origin['reviewer'])
            if identity is None:
                no_identity += 1
                print('review-import: WARNING: not importing %s: the review of %s was made by %s, who '
                      'has no signing identity in REVIEWER_IDENTITIES, so %s cannot be verified; add '
                      'them to REVIEWER_IDENTITIES in %s'
                      % (path, origin['path'], origin['reviewer'], commit, os.path.basename(__file__)),
                      file=sys.stderr)
                continue
            # Per identity as well as per commit: a commit carrying two
            # reviewers' state is two attestations, each checked
            # against its own signer.
            if (commit, identity) not in verified:
                if timeouts >= VERIFY_TIMEOUT_BUDGET:
                    deferred += 1
                    continue
                verified[(commit, identity)] = verify_commit(source_id[1], commit, identity)
                if verified[(commit, identity)][2]:
                    timeouts += 1
            ok, detail, timeout = verified[(commit, identity)]
            if not ok and timeout:
                timed_out += 1
                print('review-import: WARNING: not importing %s: the review of %s was introduced by '
                      '%s, whose signature could not be checked in time (%s)'
                      % (path, origin['path'], commit, detail), file=sys.stderr)
                continue
            if not ok:
                unverified += 1
                print('review-import: WARNING: not importing %s: the review of %s was introduced by '
                      '%s, whose signature did not verify (%s)' % (path, origin['path'], commit, detail),
                      file=sys.stderr)
                continue
        entries[path] = {
            'sha': sha,
            # The source stamp's date, not today's: it records when the
            # content was read.
            'date': origin['date'],
            'imported': {
                'repo': SOURCE_REPO,
                'reviewer': origin['reviewer'],
                'path': origin['path'],
                'commit': commit,
                'verified': verify,
            },
        }
        added.append(path)
        if path in unsupported:
            unsupported.discard(path)
            print('review-import: WARNING: replaced import of %s: %s (%s)'
                  % (path, unsupported_reason(existing), json.dumps(existing['imported'], sort_keys=True)),
                  file=sys.stderr)
        print('review-import: imported %s from %s @ %s (%s, %s, %s)'
              % (path, origin['path'], commit[:SHORT_SHA], origin['reviewer'], origin['date'],
                 'signature verified' if verify else 'signature NOT verified'))

    # A run that could not verify everything it wanted to (no gitsign,
    # or the timeout budget spent) leaves unsupported entries alone,
    # since it could not have replaced them with what history does
    # support. So does one that walked something other than the full
    # history of origin/main, whose attestations it may simply not have.
    # (One that is also stale goes anyway, just below.)
    if gitsign_missing or timeouts >= VERIFY_TIMEOUT_BUDGET or shallow or revision != SOURCE_BRANCH_REF:
        kept = sorted(path for path in unsupported if entries[path]['sha'] == head.get(path))
        if kept:
            print('review-import: WARNING: kept %d import(s) that %s history does not support (%s), since '
                  'this run cannot judge them' % (len(kept), SOURCE_REPO, ', '.join(kept)), file=sys.stderr)
    else:
        for path in sorted(unsupported):
            print('review-import: WARNING: removed import of %s: %s (%s)'
                  % (path, unsupported_reason(entries[path]), json.dumps(entries[path]['imported'], sort_keys=True)),
                  file=sys.stderr)
            del entries[path]
            removed.append(path)

    # Then whatever is still stale. Its content changed and was not
    # replaced above -- the new blob was never reviewed, or its review
    # could not be imported by this run -- so the entry attests to
    # nothing at HEAD and would otherwise render as reviewed in
    # REVIEWS.md until the next prune. Removal only, as prune does it.
    removed.extend(drop_stale_imports(entries, head.get, 'review-import'))

    if deferred:
        print('review-import: WARNING: gitsign timed out %d time(s), so verification stopped; %d more '
              'file(s) needing it were skipped, and a later run will try them again'
              % (timeouts, deferred), file=sys.stderr)

    if added or removed:
        write_imports(imports, nl)
    regenerated = generate_reviews_md()
    summary = 'review-import: imported %d file(s), removed %d import(s)' % (len(added), len(removed))
    if unverified:
        summary += ', skipped %d whose source commit did not verify' % unverified
    if no_identity:
        summary += ', skipped %d whose reviewer has no signing identity' % no_identity
    if blocked:
        summary += ', skipped %d because gitsign is not installed' % blocked
    if timed_out:
        summary += ', skipped %d whose verification timed out' % timed_out
    if deferred:
        summary += ', skipped %d once verification stopped after %d timeout(s)' % (deferred, timeouts)
    print(summary)
    changed = ([IMPORTS_PATH] if added or removed else []) + ([REVIEWS_PATH] if regenerated else [])
    if changed:
        print('review-import: updated %s' % ', '.join(changed))
    if gitsign_missing:
        report_gitsign_missing(blocked)
    return 0


def report_gitsign_missing(blocked):
    """Announce, loudly and last, that import imported nothing for want of gitsign.

    A run that exits zero and imports nothing looks exactly like a run
    with nothing to import, so this has to be impossible to scroll past.
    """
    sys.stdout.flush()
    lines = ['', RULE,
             'review-import: GITSIGN NOT FOUND ON PATH -- NOTHING WAS IMPORTED',
             RULE,
             'Every imported review must point at a source commit whose signature was',
             'checked, and without gitsign none can be. %d file(s) whose content was' % blocked,
             'reviewed in %s were therefore left unreviewed here; existing' % SOURCE_REPO,
             'imports were kept, except any this run would have removed anyway.',
             '',
             'Install gitsign and run import again. To import without verification',
             'instead, say so explicitly with --no-verify: those entries are then',
             'recorded, and shown in %s, as unverified.' % REVIEWS_PATH,
             RULE]
    print('\n'.join(lines), file=sys.stderr)


def cmd_regen(_args):
    if generate_reviews_md():
        print('review-regen: regenerated %s' % REVIEWS_PATH)
    else:
        print('review-regen: %s already up to date' % REVIEWS_PATH)
    return 0


def review_status():
    """Compute effective review coverage against HEAD.

    A file counts as reviewed only if it carries a full-file mark whose
    stamped blob SHA still matches HEAD. This deliberately differs from
    the REVIEWS.md header count, which trusts marks without checking
    them against HEAD and is therefore only accurate immediately after
    a prune. Recomputing here means a missed prune cannot inflate the
    coverage the consistency audit sees.
    """
    include, exclude = load_scope()
    tracked = set(tracked_files())
    scoped = sorted(p for p in tracked if in_scope(p, include, exclude))

    def head_sha(path):
        return blob_sha('HEAD:%s' % path)

    native, valid = native_marks(tracked, head_sha)
    marked = set(native)

    # An imported review counts on the same terms as a native one: its
    # blob SHA must still be HEAD's. Its provenance is not checked here.
    # Native marks are not signature-checked by status either, and the
    # audit runs this against a depth-1 checkout of the source with no
    # history to check against; import checked it when it was added.
    imports, _, _ = load_imports()
    imported = set()
    for path, entry in imports.get('files', {}).items():
        marked.add(path)
        if entry.get('sha') is not None and head_sha(path) == entry['sha']:
            if path not in valid:
                imported.add(path)
            valid.add(path)

    scoped_set = set(scoped)
    stale = sorted((marked - valid) & scoped_set)
    never = sorted(p for p in scoped if p not in valid and p not in marked)
    return {
        'in_scope': len(scoped),
        'reviewed': len(valid & scoped_set),
        'imported': len(imported & scoped_set),
        'needing_review': len(stale) + len(never),
        'stale': stale,
        'never_reviewed': never,
    }


def cmd_status(args):
    status = review_status()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    print('review-status: %d of %d in-scope files carry a valid review at HEAD; %d need review'
          % (status['reviewed'], status['in_scope'], status['needing_review']))
    print('review-status: %d of those reviews were imported from %s'
          % (status['imported'], SOURCE_REPO))
    for path in status['stale']:
        print('review-status: stale: %s' % path)
    for path in status['never_reviewed']:
        print('review-status: never reviewed: %s' % path)
    return 0


def scope_orphans():
    """Tracked files that are out of scope without anyone having said so.

    A file leaves the review queue by one of two routes. It can match an
    `exclude` entry, which is a decision somebody made and can defend in a
    comment beside it. Or it can simply fail to match anything in
    `include`, which is not a decision at all -- it is what happens when a
    file type nobody thought about arrives in the repository. The second
    route is silent and has no expiry: templates/renovate/renovate.json sat
    outside review here for as long as the scope config had no JSON
    pattern, and nothing anywhere said so.

    So this reports the files taking the second route, and only those. A
    `!` re-include counts as having said so in reverse: a file put back by
    one and then dropped by an `include` that does not name it is an
    orphan, because the config asks for it to be reviewed and the file is
    not being reviewed.

    BUILTIN_EXCLUDE is never an orphan. The review state files cannot
    attest to themselves whatever the scope config says, so there is
    nothing for anyone to decide.
    """
    include, exclude = load_scope()

    orphans = []
    for path in sorted(tracked_files()):
        if any(fnmatch.fnmatch(path, pat) for pat in BUILTIN_EXCLUDE):
            continue
        if in_scope(path, include, exclude):
            continue
        if excluded_by(path, exclude):
            continue
        orphans.append(path)
    return {'orphans': orphans, 'orphan_count': len(orphans)}


def cmd_scope_orphans(args):
    result = scope_orphans()
    # Non-zero whichever form is asked for: the audit reads the JSON
    # but a developer or a hook reads the exit status, and a --json
    # run that always succeeded would be a silent way to ask.
    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result['orphans'] else 0
    if not result['orphans']:
        print('review-scope-orphans: every tracked file is either in scope '
              'or explicitly excluded')
        return 0
    print('review-scope-orphans: %d tracked file(s) are out of review scope '
          'only because %s does not name them:'
          % (result['orphan_count'], SCOPE_PATH))
    for path in result['orphans']:
        print('review-scope-orphans: unnamed: %s' % path)
    print('review-scope-orphans: add a pattern that covers each, or an '
          'exclude entry saying why it should not be reviewed.')
    return 1


def subtree_prefixes(paths):
    """Normalise next's path arguments to repo-relative prefixes.

    Relative paths are taken as repo-relative rather than relative to
    the caller's directory: the wrapper cds to the repo root before
    running this script, so the caller's directory is already gone by
    the time it would matter. An absolute path is converted, which is
    what lets "next $PWD" mean the subtree the caller is standing in.
    Returns None for a path outside the repository.
    """
    top = os.path.realpath(os.getcwd())
    prefixes = []
    for path in paths:
        if os.path.isabs(path):
            path = os.path.relpath(os.path.realpath(path), top)
        path = os.path.normpath(path)
        if path == '..' or path.startswith('../'):
            return None
        prefixes.append(path)
    return prefixes


def under_any(path, prefixes):
    """True if path is one of prefixes or inside a directory named by one."""
    for prefix in prefixes:
        if prefix == '.' or path == prefix or path.startswith(prefix + '/'):
            return True
    return False


def cmd_next(args):
    include, exclude = load_scope()
    order = load_next_order(include)
    if order is None:
        return 2
    prefixes = subtree_prefixes(args.paths)
    if prefixes is None:
        print('review-next: %s is outside this repository' % ' '.join(args.paths), file=sys.stderr)
        return 2
    candidates = [p for p in tracked_files() if in_scope(p, include, exclude)]
    where = ''
    if prefixes:
        candidates = [p for p in candidates if under_any(p, prefixes)]
        where = ' under %s' % ' '.join(prefixes)
        # Nothing in scope at all is a different answer from everything
        # reviewed: usually a typo, or a subtree the scope config leaves
        # out, and either way "well done" would be a lie.
        if not candidates:
            print('review-next: no in-scope files%s' % where, file=sys.stderr)
            return 1
    reviewed = set()
    for state_path in state_files():
        state, _ = load_json(state_path, {})
        audited, _partial = marked_paths(state, state_path)
        reviewed.update(audited)
    # An imported review is a review: the file is not offered.
    imports, _, _ = load_imports()
    reviewed.update(imports.get('files', {}))
    pool = [p for p in candidates if p not in reviewed]
    if not pool:
        print('review-next: every in-scope file%s is reviewed. Well done!' % where)
        return 0
    if order == 'listed':
        choice = min(pool, key=lambda p: listed_position(p, include))
    else:
        choice = random.choice(sorted(pool))
    print('review-next: %s (%d in-scope files%s awaiting review)' % (choice, len(pool), where))
    if not args.no_open:
        code = shutil.which('code')
        if code:
            # Pass the repo root as well as the file: the root opens (or
            # focuses) as the workspace, which weAudit needs -- a bare file
            # window has no workspace for it to record reviews against.
            subprocess.run([code, os.getcwd(), os.path.abspath(choice)], check=False)
        else:
            print('review-next: "code" not found on PATH, not opening an editor', file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('stamp', help='record blob SHAs for newly reviewed files')
    sub.add_parser('prune', help='discard reviews of files changed since review')
    p_import = sub.add_parser(
        'import', help='mark files reviewed whose content was reviewed in %s' % SOURCE_REPO)
    p_import.add_argument('--no-verify', action='store_true',
                          help='do not verify source commit signatures with gitsign')
    sub.add_parser('regen', help='regenerate REVIEWS.md')
    p_next = sub.add_parser('next', help='pick an unreviewed in-scope file (random, or in scope order)')
    p_next.add_argument('--no-open', action='store_true', help='print the path only, do not open VSCode')
    p_next.add_argument('paths', nargs='*', metavar='PATH',
                        help='only pick files under these paths (repo-relative, or absolute)')
    p_status = sub.add_parser('status', help='report effective review coverage against HEAD')
    p_status.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    p_orphans = sub.add_parser(
        'scope-orphans',
        help='list tracked files out of scope only because include omits them')
    p_orphans.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    args = parser.parse_args()

    top = git('rev-parse', '--show-toplevel').stdout.strip()
    os.chdir(top)

    command = {'stamp': cmd_stamp, 'prune': cmd_prune, 'import': cmd_import, 'regen': cmd_regen,
               'next': cmd_next, 'status': cmd_status,
               'scope-orphans': cmd_scope_orphans}[args.command]
    try:
        return command(args)
    except RefusedState as e:
        print('review-tracking: ERROR: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
