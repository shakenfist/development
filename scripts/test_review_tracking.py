#!/usr/bin/env python3

"""Tests for review-tracking.py, run against a fixture git repository.

Run with: python3 scripts/test_review_tracking.py
"""

import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review-tracking.py')

# These tests drive fixture git repositories, and the pre-commit hook
# runs them during `git commit`, when git exports GIT_INDEX_FILE and
# friends to hooks. Inherited by the fixture git subprocesses, those
# variables point git at the outer repository's index, so the tests
# wreck the real index instead of exercising their fixtures. Scrub
# them from this process so every child starts clean.
for _variable in [name for name in os.environ if name.startswith('GIT_')]:
    del os.environ[_variable]


def make_weaudit(audited, partial=None, author='testuser'):
    return {
        'clientRemote': 'https://example.com/repo',
        'gitRemote': 'https://example.com/repo',
        'gitSha': '0' * 40,
        'treeEntries': [],
        'auditedFiles': [{'path': p, 'author': author} for p in audited],
        'partiallyAuditedFiles': [{'path': p, 'author': author, 'startLine': s, 'endLine': e}
                                  for p, s, e in (partial or [])],
        'resolvedEntries': [],
    }


class ReviewTrackingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.git('init', '-b', 'develop')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test User')
        self.git('config', 'commit.gpgsign', 'false')
        os.mkdir(os.path.join(self.repo, 'src'))
        os.mkdir(os.path.join(self.repo, '.vscode'))
        self.write('src/a.py', 'a = 1\n')
        self.write('src/b.py', 'b = 2\n')
        self.write('src/gen_pb2.py', 'generated = True\n')
        self.write('.vscode/review-scope.toml', 'exclude = ["*_pb2.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git'] + list(args), cwd=self.repo, check=True,
                              capture_output=True, text=True)

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def read(self, path):
        with open(os.path.join(self.repo, path)) as f:
            return f.read()

    def read_json(self, path):
        return json.loads(self.read(path))

    def run_tool(self, *args):
        return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=self.repo,
                              capture_output=True, text=True)

    def blob(self, rev_path):
        return self.git('rev-parse', rev_path).stdout.strip()

    def mark_reviewed(self, audited, partial=None):
        self.write('.vscode/testuser.weaudit', json.dumps(make_weaudit(audited, partial), indent=2))
        self.git('add', '.vscode/testuser.weaudit')

    def test_stamp_creates_sidecar_and_reviews_md(self):
        self.mark_reviewed(['src/a.py'])
        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)

        sidecar = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertEqual(sidecar['files']['src/a.py']['sha'], self.blob(':src/a.py'))
        self.assertIn('date', sidecar['files']['src/a.py'])

        reviews = self.read('REVIEWS.md')
        self.assertIn('src/a.py', reviews)
        self.assertIn('testuser', reviews)
        self.assertIn('1 of 2 in-scope files are currently reviewed.', reviews)

        # A second run has nothing to do and passes.
        self.git('add', '-A')
        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_stamp_refuses_to_move_a_stamp_onto_unread_content(self):
        """A changed file that is already stamped must stop the commit.

        stamp used to iterate `marked - stamps`, so this file was
        skipped in silence: the stale mark went into the commit, CI
        never looked (review-only commits are path-ignored), and the
        prune that runs on every push to the default branch then
        deleted the mark -- throwing away the review instead of the
        staleness. Re-stamping is the other wrong answer, and the one
        worth naming: it would attest to content nobody read.
        """
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        stamped = self.read_json('.vscode/testuser.weaudit-shas.json')

        self.write('src/a.py', 'a = 2\n')
        self.git('add', 'src/a.py')
        p = self.run_tool('stamp')

        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('src/a.py is stamped at', p.stderr)
        self.assertIn('prune', p.stderr)
        self.assertEqual(
            self.read_json('.vscode/testuser.weaudit-shas.json'), stamped,
            'stamp moved the attestation onto content nobody has read')

    def test_an_exclude_can_be_negated_for_one_file(self):
        # Excluding a directory except for one file otherwise means
        # naming every other file by hand, and editing that list every
        # time one is added.
        os.mkdir(os.path.join(self.repo, 'gen'))
        self.write('gen/keep.py', 'keep = 1\n')
        self.write('gen/drop.py', 'drop = 1\n')
        self.write('.vscode/review-scope.toml',
                   'exclude = ["gen/*", "!gen/keep.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'generated')

        p = self.run_tool('status')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('never reviewed: gen/keep.py', p.stdout)
        self.assertNotIn('gen/drop.py', p.stdout)

    def test_a_stamp_with_no_sha_is_stale_rather_than_skipped(self):
        # A hand-edited or truncated sidecar entry has no sha, and a
        # file that has left the index has no sha either -- so a bare
        # equality test finds None == None and passes the pair over.
        # Two unknowns are not a match, and the empty-looking answer
        # is the one worth being loud about.
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        side = os.path.join(self.repo, '.vscode/testuser.weaudit-shas.json')
        with open(side) as f:
            sidecar = json.load(f)
        del sidecar['files']['src/a.py']['sha']
        with open(side, 'w') as f:
            json.dump(sidecar, f, indent=2)
        os.remove(os.path.join(self.repo, 'src/a.py'))
        self.git('add', '-A')

        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('src/a.py', p.stderr)

    def test_a_negation_cannot_re_include_the_tracking_files(self):
        # The review state describes the reviews, so it can never
        # attest to itself: BUILTIN_EXCLUDE is not a default that a
        # config may override, and a config that tries must not win.
        self.write('.vscode/review-scope.toml',
                   'exclude = ["!REVIEWS.md", "!.vscode/testuser.weaudit"]\n')
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')

        p = self.run_tool('status')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('REVIEWS.md', p.stdout)
        self.assertNotIn('.weaudit', p.stdout)

    def test_stamp_is_idempotent_for_existing_stamps(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        before = self.read_json('.vscode/testuser.weaudit-shas.json')

        # A stamped entry is never re-stamped, even after the file changes;
        # only prune may remove it.
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.write('src/a.py', 'a = 42\n')
        self.git('add', 'src/a.py')
        self.run_tool('stamp')
        after = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertEqual(before['files']['src/a.py'], after['files']['src/a.py'])

    def test_stamp_warns_out_of_scope_and_staged_changes(self):
        self.mark_reviewed(['src/gen_pb2.py'])
        self.write('src/gen_pb2.py', 'generated = False\n')
        self.git('add', 'src/gen_pb2.py')
        p = self.run_tool('stamp')
        self.assertIn('out of review scope', p.stderr)
        self.assertIn('changes staged in this commit', p.stderr)

    def test_stamp_drops_unmarked_entries(self):
        self.mark_reviewed(['src/a.py', 'src/b.py'])
        self.run_tool('stamp')
        self.mark_reviewed(['src/a.py'])
        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 1)
        sidecar = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertNotIn('src/b.py', sidecar['files'])

    def test_prune_discards_stale_reviews(self):
        self.mark_reviewed(['src/a.py', 'src/b.py'], partial=[('src/b.py', 3, 9)])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')

        self.write('src/b.py', 'b = 3\n')
        self.git('add', 'src/b.py')
        self.git('commit', '-m', 'change b')

        p = self.run_tool('prune')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('src/b.py changed since its review', p.stdout)

        state = self.read_json('.vscode/testuser.weaudit')
        self.assertEqual([e['path'] for e in state['auditedFiles']], ['src/a.py'])
        self.assertEqual(state['partiallyAuditedFiles'], [])
        sidecar = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertEqual(sorted(sidecar['files']), ['src/a.py'])
        self.assertNotIn('src/b.py', self.read('REVIEWS.md'))

    def test_prune_keeps_fresh_reviews(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        p = self.run_tool('prune')
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('changed since its review', p.stdout)
        state = self.read_json('.vscode/testuser.weaudit')
        self.assertEqual([e['path'] for e in state['auditedFiles']], ['src/a.py'])

    def test_prune_handles_deleted_files(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.git('rm', 'src/a.py')
        self.git('commit', '-m', 'remove a')
        p = self.run_tool('prune')
        self.assertIn('src/a.py changed since its review', p.stdout)
        sidecar = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertEqual(sidecar['files'], {})

    def test_next_respects_scope_and_reviews(self):
        self.mark_reviewed(['src/a.py'])
        self.git('commit', '-m', 'reviews', '-a')
        for _ in range(5):
            p = self.run_tool('next', '--no-open')
            self.assertEqual(p.returncode, 0)
            # src/a.py is reviewed, src/gen_pb2.py is excluded by the scope
            # config, and the tracking machinery excludes itself -- so the
            # only valid candidate is src/b.py.
            self.assertIn('src/b.py', p.stdout)

    def test_next_all_reviewed(self):
        self.write('.vscode/review-scope.toml', 'include = ["src/*"]\nexclude = ["*_pb2.py"]\n')
        self.mark_reviewed(['src/a.py', 'src/b.py'])
        p = self.run_tool('next', '--no-open')
        self.assertIn('every in-scope file is reviewed', p.stdout)

    def test_stamp_ignores_directory_entries(self):
        # weAudit adds a derived directory entry to auditedFiles once every
        # file in the directory is reviewed, alongside the per-file entries.
        os.mkdir(os.path.join(self.repo, 'src/usb'))
        self.write('src/usb/mod.rs', 'mod real;\n')
        self.write('src/usb/real.rs', 'fn real() {}\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'usb')
        self.mark_reviewed(['src/usb/mod.rs', 'src/usb/real.rs', 'src/usb'])

        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertNotIn('WARNING', p.stderr)
        sidecar = self.read_json('.vscode/testuser.weaudit-shas.json')
        self.assertEqual(sorted(sidecar['files']), ['src/usb/mod.rs', 'src/usb/real.rs'])
        reviews = self.read('REVIEWS.md')
        self.assertIn('src/usb/mod.rs', reviews)
        self.assertNotIn('| src/usb |', reviews)

        # And the directory entry does not make every later run churn.
        self.git('add', '-A')
        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn('WARNING', p.stderr)

    def test_prune_removes_directory_entry_when_child_pruned(self):
        os.mkdir(os.path.join(self.repo, 'src/usb'))
        self.write('src/usb/mod.rs', 'mod real;\n')
        self.write('src/usb/real.rs', 'fn real() {}\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'usb')
        self.mark_reviewed(['src/usb/mod.rs', 'src/usb/real.rs', 'src/usb'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')

        self.write('src/usb/real.rs', 'fn real() { changed() }\n')
        self.git('add', 'src/usb/real.rs')
        self.git('commit', '-m', 'change real')

        p = self.run_tool('prune')
        self.assertIn('src/usb/real.rs changed since its review', p.stdout)
        self.assertIn('removing directory mark src/usb', p.stdout)
        state = self.read_json('.vscode/testuser.weaudit')
        self.assertEqual([e['path'] for e in state['auditedFiles']], ['src/usb/mod.rs'])

    def test_regen_deterministic(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        first = self.read('REVIEWS.md')
        p = self.run_tool('regen')
        self.assertIn('already up to date', p.stdout)
        self.assertEqual(first, self.read('REVIEWS.md'))

    def test_status_categorises_files(self):
        # src/a.py reviewed and unchanged, src/b.py reviewed then
        # changed (stale), src/c.py added later (never reviewed), and
        # src/gen_pb2.py excluded by the scope config throughout.
        self.mark_reviewed(['src/a.py', 'src/b.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.write('src/b.py', 'b = 3\n')
        self.write('src/c.py', 'c = 1\n')
        self.git('add', 'src/b.py', 'src/c.py')
        self.git('commit', '-m', 'change b, add c')

        p = self.run_tool('status', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), {
            'in_scope': 3,
            'reviewed': 1,
            'needing_review': 2,
            'stale': ['src/b.py'],
            'never_reviewed': ['src/c.py'],
        })

    def test_status_unstamped_mark_needs_review(self):
        # A mark with no stamp cannot be verified against any content,
        # so it is conservatively treated as needing review.
        self.mark_reviewed(['src/a.py'])
        self.git('commit', '-m', 'marks', '-a')
        p = self.run_tool('status', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        status = json.loads(p.stdout)
        self.assertEqual(status['reviewed'], 0)
        self.assertEqual(status['stale'], ['src/a.py'])
        self.assertEqual(status['never_reviewed'], ['src/b.py'])

    def test_status_partial_marks_do_not_count(self):
        self.mark_reviewed([], partial=[('src/a.py', 1, 1)])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'partial')
        p = self.run_tool('status', '--json')
        status = json.loads(p.stdout)
        self.assertEqual(status['reviewed'], 0)
        self.assertEqual(status['never_reviewed'], ['src/a.py', 'src/b.py'])

    def test_status_mutates_nothing(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.write('src/a.py', 'a = 2\n')
        self.git('add', 'src/a.py')
        self.git('commit', '-m', 'change a')

        state_paths = ['.vscode/testuser.weaudit',
                       '.vscode/testuser.weaudit-shas.json', 'REVIEWS.md']
        before = {path: self.read(path) for path in state_paths}
        p = self.run_tool('status')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('0 of 2 in-scope files carry a valid review at HEAD; 2 need review', p.stdout)
        self.assertIn('stale: src/a.py', p.stdout)
        self.assertIn('never reviewed: src/b.py', p.stdout)
        # status reports; it never prunes, stamps, or regenerates.
        for path in state_paths:
            self.assertEqual(self.read(path), before[path])


class ThisRepositoryTest(unittest.TestCase):
    """Checks against this repository's own review state, not a fixture.

    The fixture tests above prove the tooling behaves; these prove the
    committed state is the state the tooling would produce. They exist
    because a review-only pull request is exempted from CI by ci.yml's
    paths-ignore block, so the pre-commit run of this suite is the only
    thing that ever looks at a review commit -- which is why that hook
    carries no file filter.
    """

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('review_tracking', SCRIPT)
        cls.rt = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.rt)

    def setUp(self):
        self.previous = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.previous)

    def test_reviews_md_is_reproducible_from_the_committed_state(self):
        """REVIEWS.md must be exactly what the review state renders to.

        The header count is not enough to catch this: it counts weAudit
        marks rather than stamps, so a commit that lands the marks and
        forgets .vscode/<user>.weaudit-shas.json reports the right
        number of reviews while every Date and Blob SHA cell renders as
        '-'. That is not a cosmetic difference. prune-reviews.yml
        regenerates and commits this file on every push to main, so the
        first thing such a merge produces is a bot commit blanking the
        attestation columns -- and review-tracking.py status, which the
        review-coverage audit check reads, counts an unstamped mark as
        needing review.

        Comparing the rendering also catches a REVIEWS.md edited by
        hand, which its own header forbids.
        """
        with open(os.path.join(self.root, 'REVIEWS.md')) as f:
            committed = f.read()
        self.assertEqual(
            self.rt.render_reviews_md(), committed,
            'REVIEWS.md is not what the committed review state '
            'renders to. A difference in the header count means a file '
            'entered or left review scope, and `review-tracking.py '
            'regen` is all that is needed -- this one can be caused by '
            'another branch rather than by your change, since two '
            'branches adding an in-scope file each regen to the same '
            'header text and merge without conflict. A difference in '
            'the Date or Blob SHA columns means the sidecar '
            '(.vscode/<user>.weaudit-shas.json) is missing from the '
            'commit: run `review-tracking.py stamp` and commit the '
            'sidecar and REVIEWS.md together. A row for a file that '
            'has since changed or gone needs `review-tracking.py '
            'prune` first',
        )

    def test_every_stamp_matches_the_content_it_attests_to(self):
        """No stamp may name content that is no longer there.

        Compared against the index rather than HEAD deliberately: a
        stamp records the index, so the commit that writes the stamp
        has an index that matches and a HEAD that does not. Comparing
        against HEAD would fail on exactly the commit doing the right
        thing.

        This is a second, independent guard on the same defect
        `stamp` now refuses -- worth having twice because the two catch
        it at different moments. `stamp` catches a stale stamp only if
        somebody runs it; this catches one that is already committed,
        whoever wrote it and however. The reproducibility check above
        cannot: render_reviews_md() prints the recorded SHA without
        comparing it to anything, so a stale stamp renders faithfully
        and passes.
        """
        for state_path in self.rt.state_files():
            side_path = self.rt.sidecar_path(state_path)
            sidecar, _ = self.rt.load_json(side_path, {'files': {}})
            for path, stamp in sorted(sidecar.get('files', {}).items()):
                self.assertEqual(
                    self.rt.blob_sha(':%s' % path), stamp.get('sha'),
                    '%s stamps %s at content it no longer has. The mark '
                    'attests to a version nobody has read since: run '
                    '`review-tracking.py prune`, re-review the file, '
                    'mark it again in weAudit, then `stamp` and `regen`. '
                    'Leaving it means the prune that runs on every push '
                    'to the default branch silently discards the review'
                    % (side_path, path),
                )

    def _array_lines(self, raw, key):
        """Return the lines between `<key> = [` and its closing `]`.

        Deliberately a dumb scan rather than a TOML parse: tomllib
        gives back values, and what is needed here is the physical
        lines, because the annotation lives in a comment that a parser
        discards. A single-line array (`key = ['a', 'b']`) is returned
        as that one line.
        """
        lines = raw.splitlines()
        for index, line in enumerate(lines):
            if not line.lstrip().startswith('%s ' % key):
                continue
            if '=' not in line:
                continue
            if ']' in line.split('=', 1)[1]:
                return [line]
            body = []
            for following in lines[index + 1:]:
                if following.lstrip().startswith(']'):
                    return body
                body.append(following)
            self.fail(
                'the %s array in %s is never closed'
                % (key, self.rt.SCOPE_PATH)
            )
        self.fail(
            'no %s array found in %s; this test reads it by scanning '
            'for "%s = [" and needs updating if the file changed shape'
            % (key, self.rt.SCOPE_PATH, key)
        )

    def test_every_scope_pattern_matches_something_or_says_why_not(self):
        """A scope pattern that matches nothing must be deliberate.

        This is the failure that has actually happened: the audits tree
        moved under docs/, the exclude pattern kept saying 'audits/*',
        and because a pattern matching nothing is indistinguishable
        from a pattern doing its job, 36 machine-regenerated files
        joined the review queue with every test still passing.

        A pattern is allowed to match nothing -- 'PLAN-*.md' is a guard
        against plans reappearing at the repository root -- but it has
        to say so, so that the silent case is the one that fails.
        """
        include, exclude = self.rt.load_scope()
        tracked = self.rt.tracked_files()
        self.assertTrue(tracked, 'git ls-files returned nothing')

        with open(os.path.join(self.root, self.rt.SCOPE_PATH)) as f:
            raw = f.read()

        # Negation is an exclude-list feature. In include it would be
        # read literally, match nothing, and be reported below as a
        # stale pattern -- a true failure with a misleading cause, so
        # it is named here instead.
        for pattern in include:
            self.assertFalse(
                pattern.startswith('!'),
                'the include pattern %r in %s begins with \'!\'. '
                'Re-includes belong in exclude, where they undo a '
                'broader exclude; in include the \'!\' is matched '
                'literally and the pattern matches nothing.'
                % (pattern, self.rt.SCOPE_PATH),
            )

        for kind, patterns in [('include', include), ('exclude', exclude)]:
            body = self._array_lines(raw, kind)
            for pattern in patterns:
                # A re-include is matched with its '!' stripped, but
                # looked up in the file with it: the entry has to be
                # found as written to be annotated as written.
                bare = pattern[1:] if pattern.startswith('!') else pattern
                if any(fnmatch.fnmatch(path, bare) for path in tracked):
                    continue
                # The annotation has to be on the entry itself: the
                # pattern must appear in the code half of a line in
                # this array whose comment half carries the marker.
                # Anything looser lets a comment stand in for an
                # annotation -- the prose above the array quotes
                # patterns while explaining them, and a comment inside
                # it could name a pattern it does not annotate. Either
                # TOML quote style, so that a pattern needing a marker
                # is told so rather than told it has none because the
                # lookup missed it.
                quoted = ["'%s'" % pattern, '"%s"' % pattern]
                annotated = [
                    line for line in body
                    if 'unmatched-by-design' in line
                    and any(q in line.split('#', 1)[0] for q in quoted)
                ]
                self.assertTrue(
                    annotated,
                    'the %s pattern %r in %s matches no tracked file. '
                    'If that is deliberate, add an '
                    '`unmatched-by-design` comment on its line; '
                    'otherwise it has been left behind by a rename or '
                    'a move and is no longer doing anything.'
                    % (kind, pattern, self.rt.SCOPE_PATH),
                )


if __name__ == '__main__':
    unittest.main()
