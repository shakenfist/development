#!/usr/bin/env python3

"""Code review tracking hooks: stamp, prune, regen, and next.

This script implements the automation described in
docs/code-review-tracking.md. It runs in the repository under review
(normally via the pre-commit framework, see .pre-commit-hooks.yaml at
the root of the development repository):

- stamp: record the blob SHA and date of newly reviewed files in a
  sidecar next to each weAudit state file, then regenerate REVIEWS.md.
  Run at the pre-commit stage; exits non-zero if it changed anything so
  the user can stage the updates and re-run the commit.
- prune: remove review marks (whole-file and region) for files whose
  content no longer matches the stamped blob SHA, then regenerate
  REVIEWS.md. Run at the post-merge, post-checkout, and post-rewrite
  stages; always exits zero.
- regen: regenerate REVIEWS.md from the current state.
- next: pick a random in-scope file with no current review mark and
  open it in VSCode.

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
    """
    if not os.path.exists(SCOPE_PATH):
        return [], []
    import tomllib
    with open(SCOPE_PATH, 'rb') as f:
        data = tomllib.load(f)
    return list(data.get('include', [])), list(data.get('exclude', []))


def in_scope(path, include, exclude):
    if any(fnmatch.fnmatch(path, pat) for pat in BUILTIN_EXCLUDE):
        return False
    if include and not any(fnmatch.fnmatch(path, pat) for pat in include):
        return False
    return not any(fnmatch.fnmatch(path, pat) for pat in exclude)


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


def generate_reviews_md():
    """Regenerate REVIEWS.md. Returns True if the file changed."""
    include, exclude = load_scope()
    scoped = sorted(p for p in tracked_files() if in_scope(p, include, exclude))

    full_rows = []
    partial_rows = []
    reviewed_paths = set()
    for state_path in state_files():
        reviewer = reviewer_name(state_path)
        state, _ = load_json(state_path, {})
        sidecar, _ = load_json(sidecar_path(state_path), {'version': 1, 'files': {}})
        stamps = sidecar.get('files', {})
        audited, partial = marked_paths(state)
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
    out.append('This file is generated by the review tracking hooks -- do not edit')
    out.append('it by hand. See %s' % DOCS_URL)
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
    content = '\n'.join(out) + '\n'

    old = None
    if os.path.exists(REVIEWS_PATH):
        with open(REVIEWS_PATH) as f:
            old = f.read()
    if content == old:
        return False
    with open(REVIEWS_PATH, 'w') as f:
        f.write(content)
    return True


def cmd_stamp(_args):
    include, exclude = load_scope()
    staged = set(git('diff', '--cached', '--name-only').stdout.splitlines())
    changed = []
    for state_path in state_files():
        state, _ = load_json(state_path, {})
        side_path = sidecar_path(state_path)
        sidecar, side_nl = load_json(side_path, {'version': 1, 'files': {}})
        stamps = sidecar.setdefault('files', {})
        audited, partial = marked_paths(state)
        marked = set(audited) | set(partial)

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
            if not in_scope(path, include, exclude):
                print('review-stamp: WARNING: %s is marked reviewed but out of review scope (see %s)'
                      % (path, SCOPE_PATH), file=sys.stderr)
            stamps[path] = {'sha': sha, 'date': datetime.date.today().isoformat()}
            print('review-stamp: stamped %s at %s' % (path, sha[:SHORT_SHA]))
            side_changed = True
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
        print('review-stamp: updated %s; stage the changes (git add %s) and re-run the commit'
              % (', '.join(changed), ' '.join(changed)))
        return 1
    return 0


def cmd_prune(_args):
    pruned = []
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
            subprocess.run([code, choice], check=False)
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
    args = parser.parse_args()

    top = git('rev-parse', '--show-toplevel').stdout.strip()
    os.chdir(top)

    return {'stamp': cmd_stamp, 'prune': cmd_prune,
            'regen': cmd_regen, 'next': cmd_next}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
