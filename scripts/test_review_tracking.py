#!/usr/bin/env python3

"""Tests for review-tracking.py, run against a fixture git repository.

Run with: python3 scripts/test_review_tracking.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review-tracking.py')


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
        p = self.run_tool('stamp')
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

    def test_regen_deterministic(self):
        self.mark_reviewed(['src/a.py'])
        self.run_tool('stamp')
        first = self.read('REVIEWS.md')
        p = self.run_tool('regen')
        self.assertIn('already up to date', p.stdout)
        self.assertEqual(first, self.read('REVIEWS.md'))


if __name__ == '__main__':
    unittest.main()
