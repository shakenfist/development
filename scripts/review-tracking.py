#!/usr/bin/env python3

"""Code review tracking helpers: stamp, prune, regen, next, status, scope-orphans.

This script implements the automation described in
docs/code-review-tracking.md. It runs in the repository under review,
invoked by hand -- deliberately not from git hooks, which proved
confusing when they fired in the middle of other git operations.
(Two subcommands also run from CI: prune from an adopting repo's
prune-reviews workflow, and status from the consistency audit's
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
- .vscode/review-scope.toml -- optional include/exclude fnmatch
  patterns defining which files are in scope for review.
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


def git(*args, check=True):
    p = subprocess.run(['git'] + list(args), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError('git %s failed: %s' % (' '.join(args), p.stderr.strip()))
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
    if not os.path.exists(SCOPE_PATH):
        return [], []
    import tomllib
    with open(SCOPE_PATH, 'rb') as f:
        data = tomllib.load(f)
    return list(data.get('include', [])), list(data.get('exclude', []))


def in_scope(path, include, exclude):
    """Is this path subject to whole-file review?

    A '!' entry in exclude re-includes, and is evaluated only when
    something else in exclude has already matched -- so ordering within
    the list does not matter. It deliberately cannot override
    BUILTIN_EXCLUDE: the review state files describe the reviews and
    can never attest to themselves.
    """
    if any(fnmatch.fnmatch(path, pat) for pat in BUILTIN_EXCLUDE):
        return False
    if include and not any(fnmatch.fnmatch(path, pat) for pat in include):
        return False
    if not any(fnmatch.fnmatch(path, pat) for pat in exclude
               if not pat.startswith('!')):
        return True
    return any(fnmatch.fnmatch(path, pat[1:]) for pat in exclude
               if pat.startswith('!'))


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
                              stamp.get('sha', '-')[:SHORT_SHA]))
        for path, regions in sorted(partial.items()):
            stamp = stamps.get(path, {})
            lines = ', '.join('%d-%d' % (s, e) for s, e in sorted(regions))
            partial_rows.append((path, lines, reviewer, stamp.get('date', '-'),
                                 stamp.get('sha', '-')[:SHORT_SHA]))

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
        out.append('| File | Reviewer | Date | Blob SHA |')
        out.append('|------|----------|------|----------|')
        for path, reviewer, date, sha in sorted(full_rows):
            out.append('| %s | %s | %s | %s |' % (path, reviewer, date, sha))
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

    regenerated = generate_reviews_md()
    if pruned:
        print('review-prune: pruned %d stale review(s); commit the updated review state '
              '(signed) at the end of your session' % len(pruned))
        print('review-prune: if VSCode is already open, run "weAudit: Toggle Tree View Mode" or '
              'reload the window to refresh the ticks')
    elif regenerated:
        print('review-prune: regenerated %s' % REVIEWS_PATH)
    return 0


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

    valid = set()
    marked = set()
    for state_path in state_files():
        state, _ = load_json(state_path, {})
        sidecar, _ = load_json(sidecar_path(state_path), {'version': 1, 'files': {}})
        stamps = sidecar.get('files', {})
        audited, _partial = marked_paths(state)
        for path in audited:
            if is_dir_entry(path, tracked):
                continue
            marked.add(path)
            stamp = stamps.get(path)
            # A mark without a stamp cannot be verified against any
            # content, so it is conservatively treated as needing
            # review. Partial (region) marks never count as reviewed.
            if stamp is not None and blob_sha('HEAD:%s' % path) == stamp['sha']:
                valid.add(path)

    scoped_set = set(scoped)
    stale = sorted((marked - valid) & scoped_set)
    never = sorted(p for p in scoped if p not in valid and p not in marked)
    return {
        'in_scope': len(scoped),
        'reviewed': len(valid & scoped_set),
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
    hard = [pat for pat in exclude if not pat.startswith('!')]
    soft = [pat[1:] for pat in exclude if pat.startswith('!')]

    orphans = []
    for path in sorted(tracked_files()):
        if any(fnmatch.fnmatch(path, pat) for pat in BUILTIN_EXCLUDE):
            continue
        if in_scope(path, include, exclude):
            continue
        excluded = any(fnmatch.fnmatch(path, pat) for pat in hard)
        reincluded = any(fnmatch.fnmatch(path, pat) for pat in soft)
        if excluded and not reincluded:
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

    return {'stamp': cmd_stamp, 'prune': cmd_prune, 'regen': cmd_regen,
            'next': cmd_next, 'status': cmd_status,
            'scope-orphans': cmd_scope_orphans}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
