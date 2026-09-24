#!/usr/bin/env python3

"""Tests for review-tracking.py, run against a fixture git repository.

Run with: python3 scripts/tests/test_review_tracking.py
"""

import fnmatch
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPT_DIR, 'review-tracking.py')

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


# The header count line of REVIEWS.md, which
# test_reviews_md_is_reproducible_from_the_committed_state excludes from
# its comparison. The count is a property of the whole tree: any file
# entering or leaving review scope moves it, on whatever branch happens
# to do so. Asserting it would make REVIEWS.md a file that every such
# branch has to regenerate and commit, which is how a generated file
# becomes a merge-conflict hot spot and a CI round trip for a change
# that is entirely prose. Nothing is lost by leaving it out: cmd_prune
# regenerates REVIEWS.md whether or not it pruned anything, so
# prune-reviews corrects the count on the next push to main, and the
# review-coverage audit does not read this number at all --
# review_status() recomputes coverage against HEAD precisely so that a
# missed regen cannot inflate it.
#
# ReviewTrackingTest.test_stamp_creates_sidecar_and_reviews_md asserts
# that this still matches what render_reviews_md emits, so rewording the
# sentence fails there rather than silently putting the count back into
# the comparison.
COUNT_LINE = re.compile(
    r'^\d+ of \d+ in-scope files are currently reviewed\.$', re.MULTILINE)


def without_count(text):
    """Return REVIEWS.md text with the header count neutralised."""
    return COUNT_LINE.sub('<count>', text)


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

    def run_tool_interleaved(self, *args):
        """Run with stdout and stderr on one pipe, as a terminal sees them."""
        return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=self.repo,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

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
        # Asserted through COUNT_LINE rather than as a literal, so that
        # rewording the sentence fails here, with the reason attached,
        # rather than silently dropping the count out of the
        # reproducibility comparison in ThisRepositoryTest.
        count = COUNT_LINE.search(reviews)
        self.assertIsNotNone(
            count,
            'the count line was reworded; without_count no longer neutralises '
            'it, so REVIEWS.md is a merge-conflict hot spot again')
        self.assertEqual(
            '1 of 2 in-scope files are currently reviewed.', count.group(0))

        # A second run has nothing to do and passes.
        self.git('add', '-A')
        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_the_count_may_drift_from_the_committed_file_but_a_row_may_not(self):
        """Both halves of the reproducibility test's tolerance.

        ThisRepositoryTest compares the committed REVIEWS.md against a
        fresh rendering with the header count neutralised, so a branch
        that moves the count does not have to regenerate and commit the
        file. That comparison cannot demonstrate its own tolerance: on
        the real tree the two sides agree, so it passes either way.
        Perturb a rendering here instead -- a moved count still compares
        equal, a moved row does not.
        """
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        rendered = self.read('REVIEWS.md')

        moved_count = rendered.replace(
            '1 of 2 in-scope files are currently reviewed.',
            '1 of 3 in-scope files are currently reviewed.')
        self.assertNotEqual(moved_count, rendered)
        self.assertEqual(without_count(moved_count), without_count(rendered))

        moved_row = rendered.replace('src/a.py', 'src/b.py')
        self.assertNotEqual(moved_row, rendered)
        self.assertNotEqual(without_count(moved_row), without_count(rendered))

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
        self.assertIn('OUT OF REVIEW SCOPE', p.stderr)
        self.assertIn('src/gen_pb2.py', p.stderr)
        self.assertIn('changes staged in this commit', p.stderr)

    def test_stamp_announces_out_of_scope_every_run(self):
        """The announcement must survive the run that first stamps the file.

        The mistake this catches is noticed late or not at all, so a
        warning that fires once and then goes quiet is no use: the
        second run is the one where the reviewer is looking for
        confirmation that the count moved.
        """
        self.mark_reviewed(['src/gen_pb2.py'])
        first = self.run_tool('stamp')
        self.assertIn('OUT OF REVIEW SCOPE', first.stderr)
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')

        second = self.run_tool('stamp')
        self.assertNotIn('stamped src/gen_pb2.py', second.stdout)
        self.assertIn('OUT OF REVIEW SCOPE', second.stderr)
        self.assertIn('src/gen_pb2.py', second.stderr)
        self.assertEqual(second.returncode, 1)

    def test_stamp_out_of_scope_announcement_is_the_last_thing_on_stderr(self):
        """It has to outlive the chatter it would otherwise scroll past.

        The old warning printed in the middle of the per-file loop,
        which is where it got lost. Coming after the staged-changes
        warning is not enough to prove that -- that warning is early
        enough that almost any placement beats it. The property worth
        holding is that nothing follows the announcement at all, so it
        is what remains on screen when stamp returns.

        Set up against the noisiest run there is: a stale stamp (which
        reports after the per-file loop) and a staged out-of-scope
        file (which reports inside it).
        """
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.write('src/a.py', 'a = 99\n')
        self.git('add', 'src/a.py')
        self.mark_reviewed(['src/a.py', 'src/gen_pb2.py'])
        self.write('src/gen_pb2.py', 'generated = False\n')
        self.git('add', 'src/gen_pb2.py')

        p = self.run_tool('stamp')
        self.assertIn('is stamped at', p.stderr)
        self.assertIn('changes staged in this commit', p.stderr)
        self.assertIn('.vscode/review-scope.toml', p.stderr)
        self.assertIn('un-mark them in weAudit', p.stderr)

        tail = p.stderr.rstrip().rsplit('=' * 72, 1)
        self.assertEqual(len(tail), 2, 'announcement is not delimited by a rule')
        self.assertEqual(tail[1], '', 'something is printed after the announcement')
        self.assertIn('OUT OF REVIEW SCOPE', tail[0])

    def test_stamp_out_of_scope_announcement_survives_stdout_buffering(self):
        """Last on stderr is not last on screen.

        The per-file lines go to stdout, which Python block-buffers as
        soon as the output is a pipe rather than a terminal -- so an
        unflushed banner is written first and scrolls off the top,
        which is the exact failure it exists to prevent. Checked on a
        combined stream, because separate captures cannot see it.
        """
        self.mark_reviewed(['src/a.py', 'src/b.py', 'src/gen_pb2.py'])
        p = self.run_tool_interleaved('stamp')
        self.assertIn('stamped src/a.py', p.stdout)
        self.assertGreater(p.stdout.index('OUT OF REVIEW SCOPE'),
                           p.stdout.index('stamped src/a.py'))

    def test_stamp_silent_when_every_mark_is_in_scope(self):
        self.mark_reviewed(['src/a.py'])
        p = self.run_tool('stamp')
        self.assertNotIn('OUT OF REVIEW SCOPE', p.stderr)

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

    def test_prune_regenerates_the_count_even_when_nothing_was_stale(self):
        """cmd_prune must regenerate REVIEWS.md on a run that pruned nothing.

        This is the healing path the whole no-REVIEWS.md-in-a-pull-request
        policy rests on: a branch that moves the header count leaves it
        wrong, and prune-reviews corrects it on the next push to the
        default branch. That only works because generate_reviews_md() is
        called unconditionally rather than under `if pruned`. Making it
        conditional reads like a harmless optimisation and would leave
        the count drifting forever with nothing failing, which is
        precisely what the comment on COUNT_LINE above relies on not
        happening -- so the property is asserted here rather than left to
        that comment.
        """
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        self.assertIn('1 of 2 in-scope files are currently reviewed.',
                      self.read('REVIEWS.md'))

        # A new in-scope file moves the count and stales nothing.
        self.write('src/c.py', 'c = 3\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add c')

        p = self.run_tool('prune')
        self.assertEqual(p.returncode, 0)
        self.assertNotIn('changed since its review', p.stdout)

        self.assertIn(
            '1 of 3 in-scope files are currently reviewed.',
            self.read('REVIEWS.md'),
            'prune regenerated no REVIEWS.md on a run that pruned nothing, '
            'so a moved header count would never heal on the default branch')
        state = self.read_json('.vscode/testuser.weaudit')
        self.assertEqual([e['path'] for e in state['auditedFiles']],
                         ['src/a.py'])

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
            'imported': 0,
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

    def test_scope_orphans_reports_files_no_include_pattern_names(self):
        # The fixture scope config has no include list, so nothing is
        # an orphan: an empty include means every tracked file.
        p = self.run_tool('scope-orphans', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['orphans'], [])

        # Enumerating Python leaves the JSON file nobody thought about
        # outside review, with nothing anywhere recording that choice.
        self.write('config.json', '{}\n')
        self.write('.vscode/review-scope.toml',
                   'include = ["*.py"]\nexclude = ["*_pb2.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add config')

        p = self.run_tool('scope-orphans', '--json')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), {
            'orphans': ['config.json'],
            'orphan_count': 1,
        })

    def test_scope_orphans_accepts_a_file_an_exclude_names(self):
        # An excluded file is a decision somebody made, whether or not
        # the include list would otherwise have covered it.
        self.write('config.json', '{}\n')
        self.write('.vscode/review-scope.toml',
                   'include = ["*.py"]\nexclude = ["config.json"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add config')
        p = self.run_tool('scope-orphans', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['orphans'], [])

    def test_scope_orphans_ignores_the_tracking_files(self):
        # .vscode/* and REVIEWS.md can never hold a review mark, so
        # there is no decision for anyone to record about them and
        # they must not be reported as needing one.
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'reviews')
        p = self.run_tool('scope-orphans', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['orphans'], [])

    def test_scope_orphans_reports_a_re_include_the_include_list_defeats(self):
        # 'docs/notes.md' is excluded by the directory pattern and then
        # put back by the negation, so the config asks for it to be
        # reviewed -- but the include list names only Python, so it is
        # not. Treating that as a deliberate exclusion would hide a
        # config contradicting itself.
        os.mkdir(os.path.join(self.repo, 'docs'))
        self.write('docs/notes.md', 'notes\n')
        self.write('.vscode/review-scope.toml',
                   'include = ["*.py"]\n'
                   'exclude = ["docs/*", "!docs/notes.md"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add docs')
        p = self.run_tool('scope-orphans', '--json')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)['orphans'], ['docs/notes.md'])

    def test_scope_orphans_names_every_file_in_its_text_output(self):
        # The audit issue body is the work queue, so the human-readable
        # form has to list the files rather than count them.
        self.write('config.json', '{}\n')
        self.write('data.yaml', 'k: v\n')
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add config')
        p = self.run_tool('scope-orphans')
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('config.json', p.stdout)
        self.assertIn('data.yaml', p.stdout)

    def test_scope_orphans_mutates_nothing(self):
        self.write('config.json', '{}\n')
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'add config')
        before = self.git('status', '--porcelain').stdout
        self.run_tool('scope-orphans')
        self.assertEqual(self.git('status', '--porcelain').stdout, before)

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


# A stand-in for gitsign, which needs Sigstore's network services. It
# logs every invocation beside itself and fails exactly the commits
# listed in gitsign.bad, so a test can say which signatures are good.
GITSIGN_STUB = """#!%s
import json
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, 'gitsign.log'), 'a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
bad_path = os.path.join(here, 'gitsign.bad')
bad = open(bad_path).read().split() if os.path.exists(bad_path) else []
if sys.argv[-1] in bad:
    print('error: no valid signature for this commit', file=sys.stderr)
    sys.exit(1)
print('Good signature', file=sys.stderr)
"""

IMPORTS = '.vscode/imports.weaudit-shas.json'


class ImportTest(unittest.TestCase):
    """import, run in a target fixture against a source fixture.

    The script takes its source from the clone it lives in, so each test
    builds a throwaway "development" repository with a copy of the
    script under scripts/ and runs that copy in the target, exactly as
    an adopted repository's wrapper does. The tool runs with a PATH
    holding only git and, unless a test removes it, a gitsign stub --
    the real gitsign needs the network, and a host that has it
    installed must not stop the "gitsign absent" path being tested.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.source = os.path.join(root, 'source')
        self.target = os.path.join(root, 'target')
        self.bin = os.path.join(root, 'bin')
        for path in (self.source, self.target, self.bin):
            os.mkdir(path)
        os.symlink(shutil.which('git'), os.path.join(self.bin, 'git'))
        self.gitsign = os.path.join(self.bin, 'gitsign')
        with open(self.gitsign, 'w') as f:
            f.write(GITSIGN_STUB % sys.executable)
        os.chmod(self.gitsign, 0o755)

        for repo in (self.source, self.target):
            self.git(repo, 'init', '-b', 'main')
            self.git(repo, 'config', 'user.email', 'test@example.com')
            self.git(repo, 'config', 'user.name', 'Test User')
            self.git(repo, 'config', 'commit.gpgsign', 'false')
            os.mkdir(os.path.join(repo, '.vscode'))

        os.mkdir(os.path.join(self.source, 'scripts'))
        self.script = os.path.join(self.source, 'scripts', 'review-tracking.py')
        shutil.copy(SCRIPT, self.script)
        self.git(self.source, 'add', '-A')
        self.git(self.source, 'commit', '-m', 'tooling')

        os.mkdir(os.path.join(self.target, 'src'))
        self.twrite('src/a.py', 'a = 1\n')
        self.twrite('src/b.py', 'b = 2\n')
        self.twrite('src/gen_pb2.py', 'generated = True\n')
        self.twrite('.vscode/review-scope.toml', 'exclude = ["*_pb2.py"]\n')
        self.tcommit('initial')

    def tearDown(self):
        self.tmp.cleanup()

    # Fixture plumbing.

    def git(self, repo, *args):
        return subprocess.run(['git'] + list(args), cwd=repo, check=True, capture_output=True, text=True)

    def write(self, repo, path, content):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)

    def swrite(self, path, content):
        self.write(self.source, path, content)

    def twrite(self, path, content):
        self.write(self.target, path, content)

    def tread(self, path):
        with open(os.path.join(self.target, path)) as f:
            return f.read()

    def tcommit(self, message):
        self.git(self.target, 'add', '-A')
        self.git(self.target, 'commit', '-m', message)

    def tblob(self, path):
        return self.git(self.target, 'rev-parse', 'HEAD:%s' % path).stdout.strip()

    def imports(self):
        path = os.path.join(self.target, IMPORTS)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)

    def imported(self):
        """The imports file's entries, or {} when there is no file."""
        return (self.imports() or {}).get('files', {})

    def source_review(self, stamps, full=None, partial=(), reviewer='mikal', message='review'):
        """Commit reviewer state in the source and return the commit.

        stamps maps each stamped path to its stamp date, and the sha is
        the path's content in the source working tree -- so write the
        file first. full lists the paths carrying a full-file mark and
        defaults to every stamped path; partial lists paths carrying a
        region mark. The state file and sidecar are written from
        scratch each time, so an unchanged argument leaves that file
        unchanged in the commit.
        """
        if full is None:
            full = list(stamps)
        files = {}
        for path, date in sorted(stamps.items()):
            sha = self.git(self.source, 'hash-object', path).stdout.strip()
            files[path] = {'sha': sha, 'date': date}
        self.swrite('.vscode/%s.weaudit' % reviewer,
                    json.dumps(make_weaudit(full, [(p, 1, 2) for p in partial], author=reviewer), indent=2))
        self.swrite('.vscode/%s.weaudit-shas.json' % reviewer,
                    json.dumps({'version': 1, 'files': files}, indent=2) + '\n')
        self.git(self.source, 'add', '-A')
        self.git(self.source, 'commit', '--allow-empty', '-m', message)
        return self.git(self.source, 'rev-parse', 'HEAD').stdout.strip()

    def review_a(self, path='templates/a.py', date='2026-01-02'):
        """Have the source fully review a copy of the target's src/a.py."""
        self.swrite(path, 'a = 1\n')
        return self.source_review({path: date})

    def target_review(self, audited, partial=None):
        """Mark and stamp files natively in the target, and commit."""
        self.twrite('.vscode/testuser.weaudit', json.dumps(make_weaudit(audited, partial), indent=2))
        self.git(self.target, 'add', '-A')
        self.run_tool('stamp')
        self.tcommit('reviews')

    def run_tool(self, *args, cwd=None):
        env = dict(os.environ, PATH=self.bin)
        return subprocess.run([sys.executable, self.script] + list(args), cwd=cwd or self.target,
                              capture_output=True, text=True, env=env)

    def run_import(self, *args, cwd=None):
        p = self.run_tool('import', *args, cwd=cwd)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        return p

    def gitsign_calls(self):
        log = os.path.join(self.bin, 'gitsign.log')
        if not os.path.exists(log):
            return []
        with open(log) as f:
            return [json.loads(line) for line in f]

    def vscode_snapshot(self):
        """Bytes of every file in the target's .vscode except the imports file."""
        snapshot = {}
        vscode = os.path.join(self.target, '.vscode')
        for name in sorted(os.listdir(vscode)):
            if name == os.path.basename(IMPORTS):
                continue
            with open(os.path.join(vscode, name), 'rb') as f:
                snapshot[name] = f.read()
        return snapshot

    # What is imported.

    def test_import_records_a_full_mark_with_its_provenance(self):
        commit = self.review_a()
        p = self.run_import()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.imports(), {
            'version': 1,
            'files': {
                'src/a.py': {
                    'sha': self.tblob('src/a.py'),
                    'date': '2026-01-02',
                    'imported': {
                        'repo': 'shakenfist/development',
                        'reviewer': 'mikal',
                        'path': 'templates/a.py',
                        'commit': commit,
                        'verified': True,
                    },
                },
            },
        })
        self.assertIn('imported src/a.py from templates/a.py @ %s' % commit[:12], p.stdout)
        self.assertIn('imported 1 file(s), removed 0 import(s)', p.stdout)
        self.assertTrue(self.tread(IMPORTS).endswith('\n'))

    def test_import_verifies_the_introducing_commit_against_the_signing_identity(self):
        commit = self.review_a()
        self.run_import()
        calls = self.gitsign_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['argv'][0], 'verify')
        self.assertEqual(calls[0]['argv'][-1], commit)
        self.assertIn('--certificate-identity=mikal@stillhq.com', calls[0]['argv'])
        self.assertIn('--certificate-oidc-issuer=https://github.com/login/oauth', calls[0]['argv'])
        # The common git directory, which gitsign can open from a
        # worktree as well as a plain clone.
        self.assertEqual(os.path.realpath(calls[0]['cwd']), os.path.join(self.source, '.git'))

    def test_a_partial_mark_alone_is_not_imported(self):
        """stamp stamps partially reviewed files too; a stamp is not a full review."""
        self.swrite('templates/a.py', 'a = 1\n')
        self.source_review({'templates/a.py': '2026-01-02'}, full=[], partial=['templates/a.py'])
        p = self.run_import()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIsNone(self.imports())
        self.assertIn('imported 0 file(s)', p.stdout)

    def test_upgrading_a_partial_mark_imports_from_the_upgrading_commit(self):
        """The commit that upgrades a partial mark touches only the state file.

        stamp never restamps an already-stamped blob, so a history walk
        that looked only at sidecar commits would either miss the full
        review or credit the partial one.
        """
        self.swrite('templates/a.py', 'a = 1\n')
        partial = self.source_review({'templates/a.py': '2026-01-02'}, full=[], partial=['templates/a.py'])
        full = self.source_review({'templates/a.py': '2026-01-02'}, message='finish review')
        self.assertEqual(self.git(self.source, 'diff', '--name-only', partial, full).stdout.split(),
                         ['.vscode/mikal.weaudit'])

        self.run_import()
        entry = self.imported()['src/a.py']
        self.assertEqual(entry['imported']['commit'], full)
        self.assertEqual(entry['date'], '2026-01-02')

    def test_the_earliest_of_two_paths_reviewing_a_blob_wins(self):
        self.swrite('first/a.py', 'a = 1\n')
        first = self.source_review({'first/a.py': '2026-01-01'})
        self.swrite('second/a.py', 'a = 1\n')
        self.source_review({'second/a.py': '2026-02-02'}, reviewer='zed')
        self.run_import()
        self.assertEqual(self.imported()['src/a.py']['imported'],
                         {'repo': 'shakenfist/development', 'reviewer': 'mikal', 'path': 'first/a.py',
                          'commit': first, 'verified': True})
        self.assertEqual(self.imported()['src/a.py']['date'], '2026-01-01')

    def test_the_earliest_of_two_reviews_of_one_path_wins(self):
        first = self.review_a(date='2026-01-01')
        self.source_review({}, message='unmark')
        self.review_a(date='2026-03-03')
        self.run_import()
        entry = self.imported()['src/a.py']
        self.assertEqual(entry['imported']['commit'], first)
        self.assertEqual(entry['date'], '2026-01-01')

    def test_a_lagging_copy_is_imported_from_history(self):
        """The target has the template as it was before the source changed and re-reviewed it."""
        old = self.review_a(date='2026-01-01')
        self.swrite('templates/a.py', 'a = 99\n')
        self.source_review({'templates/a.py': '2026-05-05'}, message='change and re-review')
        self.run_import()
        entry = self.imported()['src/a.py']
        self.assertEqual(entry['sha'], self.tblob('src/a.py'))
        self.assertEqual(entry['imported']['commit'], old)
        self.assertEqual(entry['date'], '2026-01-01')

    def test_a_stale_import_is_replaced_when_the_new_content_was_reviewed_too(self):
        self.review_a(date='2026-01-01')
        self.run_import()
        self.tcommit('import')
        self.swrite('templates/a.py', 'a = 99\n')
        newer = self.source_review({'templates/a.py': '2026-05-05'}, message='change and re-review')
        self.twrite('src/a.py', 'a = 99\n')
        self.tcommit('update a')

        p = self.run_import()
        entry = self.imported()['src/a.py']
        self.assertEqual(entry['sha'], self.tblob('src/a.py'))
        self.assertEqual(entry['imported']['commit'], newer)
        self.assertIn('imported 1 file(s)', p.stdout)

    # What is not imported.

    def test_a_file_with_a_valid_native_mark_is_not_imported(self):
        self.review_a()
        self.target_review(['src/a.py'])
        p = self.run_import()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIsNone(self.imports())

    def test_a_stale_native_mark_blocks_import_until_pruned(self):
        """Until prune removes it, the file's own review history says it needs a human."""
        self.twrite('src/a.py', 'a = 0\n')
        self.tcommit('older a')
        self.target_review(['src/a.py'])
        self.twrite('src/a.py', 'a = 1\n')
        self.tcommit('a now matches the reviewed source blob')
        self.review_a()

        self.run_import()
        self.assertIsNone(self.imports())

        self.run_tool('prune')
        self.tcommit('prune')
        self.run_import()
        self.assertIn('src/a.py', self.imported())

    def test_a_native_mark_supersedes_an_existing_import(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        self.target_review(['src/a.py'])

        p = self.run_import()
        self.assertIn('removed import of src/a.py (superseded by a native review by testuser)', p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.target, IMPORTS)),
                         'an emptied imports file should be removed, not left as an empty shell')
        rows = [line for line in self.tread('REVIEWS.md').splitlines() if line.startswith('| src/a.py ')]
        self.assertEqual(rows, ['| src/a.py | testuser | %s | %s | - |'
                                % (json.loads(self.tread('.vscode/testuser.weaudit-shas.json'))
                                   ['files']['src/a.py']['date'], self.tblob('src/a.py')[:12])])

    def test_a_partial_native_mark_does_not_block_import(self):
        self.review_a()
        self.target_review([], partial=[('src/a.py', 1, 1)])
        self.run_import()
        self.assertIn('src/a.py', self.imported())
        reviews = self.tread('REVIEWS.md')
        self.assertIn('## Partially reviewed files', reviews)
        self.assertRegex(reviews, r'\| src/a\.py \| 1-1 \| testuser \|')
        self.assertRegex(reviews, r'\| src/a\.py \| mikal \| 2026-01-02 \|')

    def test_an_out_of_scope_file_is_not_imported(self):
        self.swrite('templates/gen_pb2.py', 'generated = True\n')
        self.source_review({'templates/gen_pb2.py': '2026-01-02'})
        self.run_import()
        self.assertIsNone(self.imports())

    def test_an_import_that_leaves_scope_is_removed(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        self.twrite('.vscode/review-scope.toml', 'exclude = ["*_pb2.py", "src/a.py"]\n')
        self.tcommit('narrow scope')

        p = self.run_import()
        self.assertIn('removed import of src/a.py (no longer in review scope)', p.stdout)
        self.assertIsNone(self.imports())

    def test_import_exclude_prevents_an_import_and_a_re_include_restores_it(self):
        self.swrite('templates/a.py', 'a = 1\n')
        self.swrite('templates/b.py', 'b = 2\n')
        self.source_review({'templates/a.py': '2026-01-02', 'templates/b.py': '2026-01-02'})
        self.twrite('.vscode/review-scope.toml',
                    'exclude = ["*_pb2.py"]\nimport-exclude = ["src/*", "!src/b.py"]\n')
        self.tcommit('reject imports')
        self.run_import()
        self.assertEqual(sorted(self.imported()), ['src/b.py'])

    def test_import_exclude_removes_an_existing_import(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        self.twrite('.vscode/review-scope.toml', 'exclude = ["*_pb2.py"]\nimport-exclude = ["src/a.py"]\n')
        self.tcommit('reject the import')

        p = self.run_import()
        self.assertIn('removed import of src/a.py (matches import-exclude in .vscode/review-scope.toml)',
                      p.stdout)
        self.assertIsNone(self.imports())
        # And it stays out: the next run does not bring it back.
        self.run_import()
        self.assertIsNone(self.imports())

    def test_the_sources_own_imports_are_never_re_exported(self):
        """An import is not a review made in the source, so it is not the source's to pass on."""
        self.swrite('templates/a.py', 'a = 1\n')
        self.source_review({'templates/a.py': '2026-01-02'}, reviewer='imports')
        self.run_import()
        self.assertIsNone(self.imports())

    # Verification.

    def test_a_commit_that_fails_verification_is_skipped_with_a_warning(self):
        commit = self.review_a()
        with open(os.path.join(self.bin, 'gitsign.bad'), 'w') as f:
            f.write(commit + '\n')
        p = self.run_import()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIsNone(self.imports())
        self.assertIn('not importing src/a.py', p.stderr)
        self.assertIn(commit, p.stderr)
        self.assertIn('no valid signature for this commit', p.stderr)
        self.assertIn('skipped 1 whose source commit did not verify', p.stdout)

    def test_without_gitsign_imports_are_recorded_unverified_with_a_warning(self):
        os.remove(self.gitsign)
        self.review_a()
        p = self.run_import()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIs(self.imported()['src/a.py']['imported']['verified'], False)
        self.assertIn('gitsign not found on PATH', p.stderr)
        self.assertIn('signature NOT verified', p.stdout)

    def test_no_verify_records_unverified_with_a_warning_and_never_runs_gitsign(self):
        self.review_a()
        p = self.run_import('--no-verify')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIs(self.imported()['src/a.py']['imported']['verified'], False)
        self.assertIn('signature verification disabled by --no-verify', p.stderr)
        self.assertEqual(self.gitsign_calls(), [])

    def test_the_verification_warning_is_repeated_on_a_run_that_imports_nothing(self):
        self.review_a()
        self.run_import('--no-verify')
        self.tcommit('import')
        p = self.run_import('--no-verify')
        self.assertIn('imported 0 file(s)', p.stdout)
        self.assertIn('signature verification disabled by --no-verify', p.stderr)

    # Refusals.

    def test_import_refuses_to_run_in_the_source_itself(self):
        self.review_a()
        p = self.run_import(cwd=self.source)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('refusing to import shakenfist/development into itself', p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.source, IMPORTS)))

    def test_import_refuses_to_run_in_a_worktree_of_the_source(self):
        self.review_a()
        worktree = os.path.join(os.path.dirname(self.source), 'source-wt')
        self.git(self.source, 'worktree', 'add', '-b', 'wt', worktree)
        p = self.run_import(cwd=worktree)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('refusing to import shakenfist/development into itself', p.stderr)
        self.assertFalse(os.path.exists(os.path.join(worktree, IMPORTS)))

    def test_import_refuses_when_an_imports_state_file_exists(self):
        """Its sidecar would be the imports file, so weAudit would tick every import."""
        self.review_a()
        self.twrite('.vscode/imports.weaudit', json.dumps(make_weaudit([], author='imports')))
        p = self.run_import()
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn('.vscode/imports.weaudit exists', p.stderr)
        self.assertIsNone(self.imports())

    # Writes.

    def test_import_never_writes_a_state_file_or_a_reviewers_sidecar(self):
        self.review_a()
        self.target_review(['src/b.py'], partial=[('src/a.py', 1, 1)])
        before = self.vscode_snapshot()
        source_before = self.git(self.source, 'status', '--porcelain').stdout

        self.run_import()
        self.assertIn('src/a.py', self.imported())
        self.assertEqual(self.vscode_snapshot(), before)
        self.assertEqual(self.git(self.source, 'status', '--porcelain').stdout, source_before)

        # Nor when it removes an import.
        self.tcommit('import')
        self.target_review(['src/a.py', 'src/b.py'])
        before = self.vscode_snapshot()
        self.run_import()
        self.assertIsNone(self.imports())
        self.assertEqual(self.vscode_snapshot(), before)

    def test_a_second_import_changes_nothing(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        p = self.run_import()
        self.assertIn('imported 0 file(s), removed 0 import(s)', p.stdout)
        self.assertNotIn('updated', p.stdout)
        self.assertEqual(self.git(self.target, 'status', '--porcelain').stdout, '')

    def test_import_regenerates_reviews_md_when_it_changes_the_imports_file(self):
        self.review_a()
        p = self.run_import()
        self.assertIn('updated %s, REVIEWS.md' % IMPORTS, p.stdout)
        self.assertIn('| src/a.py | mikal |', self.tread('REVIEWS.md'))

    # The other subcommands.

    def test_prune_drops_a_stale_import_and_removes_the_emptied_file(self):
        self.swrite('templates/a.py', 'a = 1\n')
        self.swrite('templates/b.py', 'b = 2\n')
        commit = self.source_review({'templates/a.py': '2026-01-02', 'templates/b.py': '2026-01-02'})
        self.run_import()
        self.tcommit('import')

        self.twrite('src/a.py', 'a = 2\n')
        self.tcommit('change a')
        p = self.run_tool('prune')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('src/a.py changed since its review', p.stdout)
        self.assertIn('imported from development@%s' % commit[:12], p.stdout)
        self.assertEqual(sorted(self.imported()), ['src/b.py'])
        self.assertNotIn('| src/a.py |', self.tread('REVIEWS.md'))
        self.tcommit('prune')

        self.git(self.target, 'rm', 'src/b.py')
        self.tcommit('remove b')
        p = self.run_tool('prune')
        self.assertIn('src/b.py changed since its review', p.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.target, IMPORTS)))

    def test_prune_keeps_a_valid_import(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        before = self.tread(IMPORTS)
        p = self.run_tool('prune')
        self.assertNotIn('changed since its review', p.stdout)
        self.assertEqual(self.tread(IMPORTS), before)

    def test_stamp_ignores_the_imports_file(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        # A stale import too: stamp must not report it as a stale stamp
        # either -- that is prune's to deal with.
        self.twrite('src/a.py', 'a = 2\n')
        self.tcommit('change a')
        before = self.tread(IMPORTS)

        p = self.run_tool('stamp')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stderr, '')
        self.assertEqual(self.tread(IMPORTS), before)

    def test_status_counts_valid_imports_and_reports_stale_ones(self):
        self.review_a()
        self.run_import()
        self.tcommit('import')
        p = self.run_tool('status', '--json')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), {
            'in_scope': 2,
            'reviewed': 1,
            'imported': 1,
            'needing_review': 1,
            'stale': [],
            'never_reviewed': ['src/b.py'],
        })
        p = self.run_tool('status')
        self.assertIn('1 of those reviews were imported from shakenfist/development', p.stdout)

        self.twrite('src/a.py', 'a = 2\n')
        self.tcommit('change a')
        p = self.run_tool('status', '--json')
        self.assertEqual(json.loads(p.stdout), {
            'in_scope': 2,
            'reviewed': 0,
            'imported': 0,
            'needing_review': 2,
            'stale': ['src/a.py'],
            'never_reviewed': ['src/b.py'],
        })

    def test_next_never_offers_an_imported_file(self):
        self.swrite('templates/a.py', 'a = 1\n')
        self.swrite('templates/b.py', 'b = 2\n')
        self.source_review({'templates/a.py': '2026-01-02', 'templates/b.py': '2026-01-02'})
        self.run_import()
        p = self.run_tool('next', '--no-open')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn('every in-scope file is reviewed', p.stdout)

    def test_reviews_md_gives_an_import_a_row_with_its_source(self):
        commit = self.review_a()
        self.target_review(['src/b.py'])
        self.run_import()
        reviews = self.tread('REVIEWS.md')
        self.assertIn('2 of 2 in-scope files are currently reviewed.', reviews)
        self.assertIn('| File | Reviewer | Date | Blob SHA | Source |', reviews)
        self.assertIn('| src/a.py | mikal | 2026-01-02 | %s | development@%s |'
                      % (self.tblob('src/a.py')[:12], commit[:12]), reviews)
        self.assertRegex(reviews, r'\| src/b\.py \| testuser \| [0-9-]+ \| [0-9a-f]{12} \| - \|')

    def test_reviews_md_shows_the_native_row_when_a_file_is_also_imported(self):
        """render_reviews_md keeps one row per file even before import removes its entry."""
        self.review_a()
        self.run_import()
        self.tcommit('import')
        # Marked natively after the import, and rendered before the next
        # import run has had the chance to remove the imported entry.
        self.target_review(['src/a.py'])
        self.assertIn('src/a.py', self.imported())
        self.run_tool('regen')
        reviews = self.tread('REVIEWS.md')
        rows = [line for line in reviews.splitlines() if line.startswith('| src/a.py ')]
        self.assertEqual(len(rows), 1, reviews)
        self.assertRegex(rows[0], r'^\| src/a\.py \| testuser \| .* \| - \|$')
        self.assertIn('1 of 2 in-scope files are currently reviewed.', reviews)


class ThisRepositoryTest(unittest.TestCase):
    """Checks against this repository's own review state, not a fixture.

    The fixture tests above prove the tooling behaves; these prove the
    committed state is the state the tooling would produce. They exist
    because a review-only pull request is exempted from CI by ci.yml's
    paths-ignore block, so the pre-commit run of this suite is the only
    thing that ever looks at a review commit -- which is why that hook
    carries no file filter.
    """

    root = os.path.dirname(SCRIPT_DIR)

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
        """REVIEWS.md's rows must be what the review state renders to.

        Everything but the header count is compared. The count itself
        trusts marks rather than stamps (see review_status), so a commit
        that lands the marks and forgets
        .vscode/<user>.weaudit-shas.json reports the right number while
        every Date and Blob SHA cell renders as '-'. That is not a
        cosmetic difference: prune-reviews regenerates and commits this
        file on every push to main, so the first thing such a merge
        produces is a bot commit blanking the attestation columns -- and
        review-tracking.py status, which the review-coverage audit check
        reads, counts an unstamped mark as needing review. Comparing the
        rows catches that, and catches a REVIEWS.md edited by hand,
        which its own header forbids.

        The count is excluded for the reason given on COUNT_LINE above.
        """
        with open(os.path.join(self.root, 'REVIEWS.md')) as f:
            committed = f.read()
        self.assertEqual(
            without_count(self.rt.render_reviews_md()),
            without_count(committed),
            'REVIEWS.md is not what the committed review state renders '
            'to, ignoring the header count. A difference in the Date or '
            'Blob SHA columns means the sidecar '
            '(.vscode/<user>.weaudit-shas.json) is missing from the '
            'commit: run `review-tracking.py stamp` and commit the '
            'sidecar and REVIEWS.md together. A difference in which rows '
            'are present means a mark was added or removed by hand. Note '
            'that a row for a file that has since changed is not an '
            'error here -- pruning stale marks is the prune-reviews '
            'workflow\'s job, not a pull request\'s',
        )

    def test_no_review_mark_is_missing_its_stamp(self):
        """Every committed mark must carry an attestation.

        A mark in `auditedFiles` with no entry in the sidecar is a
        review nothing can verify: REVIEWS.md counts it in the header
        and renders its row with '-' for both Date and Blob SHA, while
        review-tracking.py status -- what the review-coverage audit
        check reads -- treats it as needing review. The two disagree
        about the same file.

        The reproducibility test above cannot see this, because regen
        renders the dashes faithfully and the committed file matches.
        Nor will prune repair it: prune removes a mark whose stamp has
        gone stale, and there is no stamp here to be stale. The state
        is unreachable through the tooling -- stamp writes both halves
        and prune removes both -- so it means the sidecar was edited by
        hand, which is what this test exists to catch.
        """
        tracked = set(self.rt.tracked_files())
        for state_path in self.rt.state_files():
            state, _ = self.rt.load_json(state_path, {})
            sidecar, _ = self.rt.load_json(
                self.rt.sidecar_path(state_path), {'version': 1, 'files': {}})
            stamps = sidecar.get('files', {})
            audited, _partial = self.rt.marked_paths(state)
            for path in audited:
                if self.rt.is_dir_entry(path, tracked):
                    continue
                # assertTrue rather than assertIn: the stamp dictionary
                # holds every reviewed file in the repository, and
                # assertIn renders it in full ahead of the explanation.
                self.assertTrue(
                    path in stamps,
                    'the review mark on %s in %s has no stamp in %s, so '
                    'nothing binds it to any content. Either restore the '
                    'stamp by re-reading the file and running '
                    '`review-tracking.py stamp`, or drop the mark -- in '
                    'weAudit, or by removing its auditedFiles entry -- and '
                    'run `review-tracking.py regen`'
                    % (path, state_path, self.rt.sidecar_path(state_path)),
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
