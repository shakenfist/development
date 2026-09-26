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
  fully reviewed in the clone of shakenfist/development this script
  lives in, recording where the signed attestation lives in
  .vscode/imports.weaudit-shas.json, then regenerate REVIEWS.md. Never
  writes a reviewer's own state file or sidecar. Run in that clone
  itself, or a worktree of it, it says so and does nothing. Always
  exits zero, except that it refuses to run beside a stray
  .vscode/imports.weaudit.
- regen: regenerate REVIEWS.md from the current state.
- next: pick a random in-scope file with no current review mark and
  open it in VSCode.
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
"""

import argparse
import datetime
import fnmatch
import glob
import json
import os
import random
import shutil
import subprocess
import sys


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

# The signing identity each reviewer's review-state commits in the
# source must carry (docs/code-review-tracking.md, "Commit signing").
# Add an entry when a new reviewer starts signing review-state commits;
# until then their reviews cannot be verified, and are not imported.
REVIEWER_IDENTITIES = {
    'mikal': 'mikal@stillhq.com',
}
GITSIGN_ISSUER = 'https://github.com/login/oauth'
GITSIGN_TIMEOUT = 120


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


def load_scope_config():
    if not os.path.exists(SCOPE_PATH):
        return {}
    import tomllib
    with open(SCOPE_PATH, 'rb') as f:
        return tomllib.load(f)


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


def load_json(path, default):
    if not os.path.exists(path):
        return default, True
    with open(path) as f:
        raw = f.read()
    return json.loads(raw), raw.endswith('\n')


def write_json(path, data, trailing_newline):
    with open(path, 'w') as f:
        f.write(json.dumps(data, indent=2) + ('\n' if trailing_newline else ''))


def marked_paths(state):
    """Return (audited, partial) where audited is a list of paths and partial
    maps path -> list of (startLine, endLine)."""
    audited = [e['path'] for e in state.get('auditedFiles', [])]
    partial = {}
    for e in state.get('partiallyAuditedFiles', []) or []:
        partial.setdefault(e['path'], []).append((e['startLine'], e['endLine']))
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
    return load_json(IMPORTS_PATH, {'version': 1, 'files': {}})


def write_imports(imports, trailing_newline):
    """Write the imports file, or remove it once it holds nothing.

    Removed rather than left as an empty shell so that a repository with
    no imports carries no imports file at all, which is the state it
    was in before import first ran.
    """
    files = imports.get('files', {})
    if not files:
        if os.path.exists(IMPORTS_PATH):
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
        audited, _partial = marked_paths(state)
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
        audited, partial = marked_paths(state)
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
    imports, _ = load_imports()
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

    old = None
    if os.path.exists(REVIEWS_PATH):
        with open(REVIEWS_PATH) as f:
            old = f.read()
    if content == old:
        return False
    with open(REVIEWS_PATH, 'w') as f:
        f.write(content)
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
        audited, partial = marked_paths(state)
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
    imports, nl = load_imports()
    stale = drop_stale_imports(imports.get('files', {}), lambda path: blob_sha('HEAD:%s' % path),
                               'review-prune')
    if stale:
        write_imports(imports, nl)
    return stale


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
    than allowed to stop every import across the fleet.
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def reviewed_blobs(source):
    """Map every blob the source has fully reviewed to where that review happened.

    Returns {blob sha: {'reviewer', 'path', 'commit', 'date'}}, where
    commit is the first commit in the source at which the reviewer's
    sidecar stamped that blob and the same commit's state file carried
    a full-file mark for the stamped path. Earliest wins across
    reviewers too: the first attestation is the one recorded.

    The whole history, not just HEAD, because the common case during a
    rollout is a target copy lagging a template the source has since
    changed and re-reviewed; only history still records the review of
    the older blob.

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

    names = git('log', '--format=', '--name-only', '--', *pathspecs, cwd=source).stdout.splitlines()
    reviewers = sorted(set(
        os.path.basename(n)[:-len(sidecar_suffix)] for n in names
        if os.path.dirname(n) == '.vscode' and n.endswith(sidecar_suffix)))
    # Should the source ever carry imports of its own, they are not
    # reviews made there and must not be re-exported as if they were.
    reviewers = [r for r in reviewers if r != IMPORTS_REVIEWER]
    if not reviewers:
        return {}

    # Oldest first, and never a commit before one of its ancestors, so
    # "first commit at which it qualifies" means the one that
    # introduced the attestation rather than a later one repeating it.
    commits = git('log', '--reverse', '--date-order', '--format=%H', '--', *pathspecs,
                  cwd=source).stdout.split()
    specs = []
    for commit in commits:
        for reviewer in reviewers:
            specs.append('%s:.vscode/%s%s' % (commit, reviewer, sidecar_suffix))
            specs.append('%s:.vscode/%s%s' % (commit, reviewer, state_suffix))
    contents = read_blobs(source, specs)

    blobs = {}
    trees = {}
    for commit in commits:
        for reviewer in reviewers:
            sidecar = parse_state(contents['%s:.vscode/%s%s' % (commit, reviewer, sidecar_suffix)])
            state = parse_state(contents['%s:.vscode/%s%s' % (commit, reviewer, state_suffix)])
            if sidecar is None or state is None:
                continue
            full = set(e.get('path') for e in state.get('auditedFiles', []) or [] if isinstance(e, dict))
            for path, stamp in sorted((sidecar.get('files') or {}).items()):
                sha = stamp.get('sha') if isinstance(stamp, dict) else None
                if not sha or sha in blobs or path not in full:
                    continue
                # Only fetched when something new would otherwise
                # qualify, which is a small fraction of the commits.
                if commit not in trees:
                    listing = git('ls-tree', '-r', '-z', '--name-only', commit, cwd=source).stdout
                    trees[commit] = set(f for f in listing.split('\0') if f)
                if is_dir_entry(path, trees[commit]):
                    continue
                blobs[sha] = {'reviewer': reviewer, 'path': path, 'commit': commit,
                              'date': stamp.get('date', '-')}
    return blobs


def verify_commit(git_dir, commit, identity):
    """Check a source commit's gitsign signature by identity. Returns (ok, detail).

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
    verified one.
    """
    try:
        p = subprocess.run(['gitsign', 'verify',
                            '--certificate-identity=%s' % identity,
                            '--certificate-oidc-issuer=%s' % GITSIGN_ISSUER,
                            commit],
                           capture_output=True, text=True, cwd=git_dir, timeout=GITSIGN_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    detail = (p.stderr.strip() or p.stdout.strip()).splitlines()
    return p.returncode == 0, detail[-1] if detail else 'exit status %d' % p.returncode


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
    if git('rev-parse', '--is-shallow-repository', cwd=SOURCE_ROOT).stdout.strip() == 'true':
        print('review-import: WARNING: %s is a shallow clone, so reviews older than its history '
              'cannot be found; fetch its full history to import them' % SOURCE_ROOT, file=sys.stderr)

    blobs = reviewed_blobs(SOURCE_ROOT)
    include, exclude = load_scope()
    import_exclude = load_import_exclude()
    tracked = set(tracked_files())
    head = head_blobs()
    native, valid = native_marks(tracked, head.get)
    imports, nl = load_imports()
    entries = imports.setdefault('files', {})

    # First, what this run should no longer be carrying. A stale entry
    # (its sha no longer HEAD's) is left for now: it is replaced below
    # if the new content was reviewed too, and removed after that if not.
    removed = []
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

    verified = {}
    added = []
    unverified = 0
    no_identity = 0
    blocked = 0
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
        if existing is not None and existing.get('sha') == sha:
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
                verified[(commit, identity)] = verify_commit(source_id[1], commit, identity)
            ok, detail = verified[(commit, identity)]
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
        print('review-import: imported %s from %s @ %s (%s, %s, %s)'
              % (path, origin['path'], commit[:SHORT_SHA], origin['reviewer'], origin['date'],
                 'signature verified' if verify else 'signature NOT verified'))

    # Then whatever is still stale. Its content changed and was not
    # replaced above -- the new blob was never reviewed, or its review
    # could not be imported by this run -- so the entry attests to
    # nothing at HEAD and would otherwise render as reviewed in
    # REVIEWS.md until the next prune. Removal only, as prune does it.
    removed.extend(drop_stale_imports(entries, head.get, 'review-import'))

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
    imports, _ = load_imports()
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


def cmd_next(args):
    include, exclude = load_scope()
    reviewed = set()
    for state_path in state_files():
        state, _ = load_json(state_path, {})
        audited, _partial = marked_paths(state)
        reviewed.update(audited)
    # An imported review is a review: the file is not offered.
    imports, _ = load_imports()
    reviewed.update(imports.get('files', {}))
    pool = [p for p in tracked_files()
            if in_scope(p, include, exclude) and p not in reviewed]
    if not pool:
        print('review-next: every in-scope file is reviewed. Well done!')
        return 0
    choice = random.choice(sorted(pool))
    print('review-next: %s (%d in-scope files awaiting review)' % (choice, len(pool)))
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
    p_next = sub.add_parser('next', help='pick a random unreviewed in-scope file')
    p_next.add_argument('--no-open', action='store_true', help='print the path only, do not open VSCode')
    p_status = sub.add_parser('status', help='report effective review coverage against HEAD')
    p_status.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    p_orphans = sub.add_parser(
        'scope-orphans',
        help='list tracked files out of scope only because include omits them')
    p_orphans.add_argument('--json', action='store_true', help='emit machine-readable JSON')
    args = parser.parse_args()

    top = git('rev-parse', '--show-toplevel').stdout.strip()
    os.chdir(top)

    return {'stamp': cmd_stamp, 'prune': cmd_prune, 'import': cmd_import, 'regen': cmd_regen,
            'next': cmd_next, 'status': cmd_status,
            'scope-orphans': cmd_scope_orphans}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
