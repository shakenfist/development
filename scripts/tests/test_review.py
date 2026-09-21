#!/usr/bin/env python3

"""Tests for audit/checks/review.py.

Run with: python3 scripts/tests/test_review.py
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import review  # noqa: E402
from tests.base import CheckTestCase, FixtureRepo  # noqa: E402

# review-coverage and review-scope-completeness shell out to
# review-tracking.py, which needs a real checkout; the suite drives it
# the way the check does.
SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'audit-check.py')


class ReviewMarksPreCommitTest(CheckTestCase):
    """Tests ReviewMarksPreCommit against config fixtures."""

    check_class = review.ReviewMarksPreCommit

    def adopt(self):
        self.fixture.write('.vscode/review-scope.toml',
                           'include = ["*.py"]\n')

    def config(self, body, hooks='end-of-file-fixer'):
        """Write a config running `hooks`, prefixed by `body`."""
        hook_lines = ''.join(
            f'      - id: {h}\n' for h in hooks.split() if h
        )
        self.fixture.write(
            '.pre-commit-config.yaml',
            f'{body}repos:\n  - repo: local\n    hooks:\n{hook_lines}'
        )

    def test_not_applicable_without_scope_config(self):
        self.config('')
        self.assert_skip(self.check())

    def test_not_applicable_without_pre_commit_config(self):
        self.adopt()
        self.assert_skip(self.check())

    def test_not_applicable_without_a_rewriting_hook(self):
        """ryll's shape: scanners and linters, but no formatter.

        Nothing rewrites the marks, so there is nothing to exclude --
        and demanding a blanket exclude here would hide review prose
        from gitleaks and the bidi scanner.
        """
        self.adopt()
        self.config('', hooks='gitleaks bidi-check shellcheck')
        self.assert_skip(self.check(), containing='No file-rewriting')

    def test_top_level_exclude_passes(self):
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit\n\n')
        self.assert_pass(self.check())

    def test_per_hook_exclude_passes(self):
        """A hook-level exclude protects the files just as well."""
        self.adopt()
        self.fixture.write('.pre-commit-config.yaml',
                           'repos:\n'
                           '  - repo: local\n'
                           '    hooks:\n'
                           '      - id: end-of-file-fixer\n'
                           '        exclude: ^\\.vscode/.*\\.weaudit\n')
        self.assert_pass(self.check())

    def test_quoted_exclude_passes(self):
        self.adopt()
        self.config("exclude: '^\\.vscode/.*\\.weaudit'\n\n")
        self.assert_pass(self.check())

    def test_trailing_whitespace_hook_also_counts(self):
        self.adopt()
        self.config('', hooks='trailing-whitespace')
        self.assert_fail(self.check(), containing='trailing-whitespace')

    def test_no_exclude_fails(self):
        self.adopt()
        self.config('')
        self.assert_fail(self.check(), containing='weaudit')

    def test_exclude_missing_the_sidecar_fails(self):
        """An anchored pattern catches the weaudit file but not its json."""
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit$\n\n')
        self.assert_fail(self.check())

    def test_unrelated_exclude_fails(self):
        self.adopt()
        self.config('exclude: ^kerbside/api/static/\n\n')
        self.assert_fail(self.check())

    def test_uncompilable_exclude_is_skipped_not_raised(self):
        self.adopt()
        self.config('exclude: ^[unclosed\n\n')
        self.assert_fail(self.check())


class ReviewCoverageTest(CheckTestCase):
    """Tests ReviewCoverage against fixture git repositories.

    The check shells out to review-tracking.py status, which needs a
    real repository: committed files so blob SHAs resolve, weAudit
    state, and a stamped sidecar.
    """

    check_class = review.ReviewCoverage

    def setUp(self):
        super().setUp()
        self.fixture.init_git()

    def review_tracking(self, *args):
        script = os.path.join(os.path.dirname(SCRIPT), 'review-tracking.py')
        return subprocess.run([sys.executable, script] + list(args),
                              cwd=self.fixture.path, capture_output=True,
                              text=True)

    def make_reviewed_repo(self, files, reviewed):
        """Create and commit files, mark some reviewed, stamp, commit."""
        self.fixture.write('.vscode/review-scope.toml',
                           'include = ["*.py"]\n')
        for path in files:
            self.fixture.write(path, f'# {path}\n')
        self.fixture.commit('initial')
        if reviewed:
            self.fixture.write('.vscode/testuser.weaudit', json.dumps({
                'auditedFiles': [{'path': p, 'author': 'testuser'} for p in reviewed],
                'partiallyAuditedFiles': [],
            }))
            self.review_tracking('stamp')
            self.fixture.commit('reviews')

    def make_stale(self, files):
        for path in files:
            self.fixture.write(path, f'# {path} changed\n')
        self.fixture.commit('changes')

    def test_not_applicable_without_scope_config(self):
        self.fixture.write('a.py', 'a = 1\n')
        self.fixture.commit('initial')
        self.assert_skip(self.check(), containing='review-scope.toml')

    def test_backlog_under_threshold_passes(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:4])
        result = self.check()
        self.assert_pass(result)
        self.assertIn('4 need review (threshold 5)', result['details'])
        self.assertNotIn('missing', result)

    def test_backlog_at_threshold_fails_with_work_queue(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:5])
        result = self.check()
        self.assert_fail(result, containing='5 need review (threshold 5)')
        self.assertEqual(result['missing'],
                         [f'stale: f{i}.py' for i in range(5)])

    def test_never_reviewed_files_count(self):
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        result = self.check()
        self.assert_fail(result,
                         containing='0 of 5 in-scope files reviewed')
        self.assertEqual(result['missing'],
                         [f'never reviewed: f{i}.py' for i in range(5)])

    def test_oserror_is_reported_rather_than_raised(self):
        # run_check() has no exception handler, so anything raised out
        # of a check costs the repository all of its other criteria as
        # well -- the failure mode recorded in checks/packaging.py and
        # text/python_source.py. The window here is narrow: the scope
        # file is checked before the exec, so reaching this needs the
        # checkout to become unusable between the two. The guard is
        # the same one every other shelling-out check already carries.
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])

        def boom(*args, **kwargs):
            raise PermissionError(13, 'Permission denied')

        original = review.subprocess.run
        review.subprocess.run = boom
        try:
            result = self.check()
        finally:
            review.subprocess.run = original
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('could not run', result['details'])

    def test_missing_script_is_reported_not_raised(self):
        # The neighbouring failure mode, asserted so the two do not get
        # conflated: a missing review-tracking.py does not raise,
        # because sys.executable is what subprocess looks for. The
        # interpreter exits non-zero and the returncode branch reports
        # it. This is why the guard above is hardening rather than a
        # fix for a live defect.
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        original = review.REVIEW_TRACKING_SCRIPT
        review.REVIEW_TRACKING_SCRIPT = os.path.join(
            self.fixture.path, 'moved-away.py')
        try:
            result = self.check()
        finally:
            review.REVIEW_TRACKING_SCRIPT = original
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('failed', result['details'])


class ReviewScopeCompletenessTest(CheckTestCase):
    """Tests ReviewScopeCompleteness against fixture repos.

    The check shells out to review-tracking.py scope-orphans, which
    needs a real repository: the orphan set is computed from git
    ls-files, so uncommitted files do not count.
    """

    check_class = review.ReviewScopeCompleteness

    def setUp(self):
        super().setUp()
        self.fixture.init_git()

    def commit(self, scope, files):
        if scope is not None:
            self.fixture.write('.vscode/review-scope.toml', scope)
        for path in files:
            self.fixture.write(path, f'# {path}\n')
        self.fixture.commit('initial')

    def test_not_applicable_without_scope_config(self):
        self.commit(None, ['a.py'])
        self.assert_skip(self.check(), containing='review-scope.toml')

    def test_a_file_no_include_pattern_names_fails(self):
        self.commit('include = ["*.py"]\n', ['a.py', 'config.json'])
        result = self.check()
        self.assert_fail(result)
        self.assertEqual(result['missing'], ['config.json'])
        self.assertIn('1 tracked file(s)', result['details'])

    def test_an_excluded_file_passes(self):
        # Excluding is a decision. The check is about omission, not
        # about how much of the repository gets reviewed.
        self.commit('include = ["*.py"]\nexclude = ["config.json"]\n',
                    ['a.py', 'config.json'])
        result = self.check()
        self.assert_pass(result)
        self.assertNotIn('missing', result)

    def test_an_empty_include_passes(self):
        # An empty include means every tracked file, so there is
        # nothing left to omit. This is the trivially compliant
        # configuration the spec offers to small repositories.
        self.commit('exclude = ["config.json"]\n', ['a.py', 'config.json'])
        self.assert_pass(self.check())

    def test_every_orphan_is_listed_not_a_sample(self):
        # The issue body is the work queue, and the fix is per file.
        self.commit('include = ["*.py"]\n',
                    ['a.py'] + [f'f{i}.json' for i in range(9)])
        result = self.check()
        self.assert_fail(result)
        self.assertEqual(result['missing'],
                         [f'f{i}.json' for i in range(9)])

    def test_full_coverage_does_not_excuse_a_narrow_scope(self):
        # The failure mode this check exists for: review-coverage sees
        # a fully reviewed repository precisely because the scope was
        # narrowed to the one file that was reviewed.
        self.commit('include = ["a.py"]\n', ['a.py', 'b.py', 'c.py'])
        result = self.check()
        self.assert_fail(result)
        self.assertEqual(result['missing'], ['b.py', 'c.py'])

    def test_oserror_is_reported_rather_than_raised(self):
        # The mirror of ReviewCoverageTest's test of the same name.
        # Both checks shell out and both grew the same handler, but
        # only the coverage half was exercised, so the scope-orphans
        # message -- a different string, reached by a different call
        # -- was the untested one. Same reasoning as there: run_check()
        # has no handler, so anything raised out of a check costs the
        # repository all of its other criteria too.
        self.commit('include = ["*.py"]\n', ['a.py', 'config.json'])

        def boom(*args, **kwargs):
            raise PermissionError(13, 'Permission denied')

        original = review.subprocess.run
        review.subprocess.run = boom
        try:
            result = self.check()
        finally:
            review.subprocess.run = original
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('scope-orphans could not run', result['details'])


class SfuiVendorTest(CheckTestCase):
    """Exercise SfuiVendor against fixture repositories.

    The canonical fixture is a tiny git repo carrying a stand-in
    tools/vendor.sh that honours the real script's --check contract
    (diff the distributable files, exit non-zero on difference); the
    real script lives in shakenfist/sfui and is not vendored here.
    """

    check_class = review.SfuiVendor

    TOKENS = ':root { --sf-bg: #000; }\n'

    def _git(self, repo, *args):
        subprocess.run(
            [
                'git', '-C', repo,
                '-c', 'user.name=test',
                '-c', 'user.email=test@example.com',
            ] + list(args),
            check=True, capture_output=True,
        )

    def _head(self, repo):
        return subprocess.run(
            ['git', '-C', repo, 'rev-parse', 'HEAD'],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _make_canonical(self, tmp):
        repo = os.path.join(tmp, 'canonical')
        os.makedirs(os.path.join(repo, 'tools'))
        with open(os.path.join(repo, 'tokens.css'), 'w') as f:
            f.write(self.TOKENS)
        with open(os.path.join(repo, 'tools', 'vendor.sh'), 'w') as f:
            f.write(
                '#!/bin/bash\n'
                'src="$(cd "$(dirname "$0")/.." && pwd)"\n'
                '[ "$1" = "--check" ] || exit 2\n'
                'diff -u "$2/tokens.css" "$src/tokens.css"\n'
            )
        self._git(repo, 'init', '--quiet')
        self._git(repo, 'add', '-A')
        self._git(repo, 'commit', '--quiet', '-m', 'initial')
        return repo

    def _make_consumer(self, tmp, sha, tokens=None):
        """The vendored copy, which becomes the repository under test.

        This is the one fixture made of two independent git
        repositories in one directory, so the consumer is built beside
        the canonical one and the fixture is repointed at it rather
        than being the directory setUp made.
        """
        self.fixture = FixtureRepo(os.path.join(tmp, 'consumer'))
        self.fixture.write('static/sfui/tokens.css',
                           tokens if tokens is not None else self.TOKENS)
        self.fixture.write('static/sfui/.sfui-commit', sha + '\n')

    def test_not_applicable_without_sfui_commit(self):
        self.assert_skip(self.check())

    def test_verbatim_copy_at_head_passes(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, self._head(canonical))
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_pass(result)
        self.assertIn('verbatim', result['details'])

    def test_edited_copy_fails(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(
            tmp, self._head(canonical),
            tokens=':root { --sf-bg: #fff; }\n',
        )
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result, containing='edited in place')

    def test_copy_behind_canonical_fails(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, self._head(canonical))
        with open(
            os.path.join(canonical, 'tokens.css'), 'a'
        ) as f:
            f.write('/* a change the consumer lacks */\n')
        self._git(canonical, 'commit', '--quiet', '-am', 'more')
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result, containing='behind canonical')

    def test_unknown_commit_fails(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, '0' * 40)
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result,
                         containing='not in the canonical repository')

    def test_malformed_stamp_fails(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, 'not-a-sha')
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result,
                         containing='does not contain a commit sha')


if __name__ == '__main__':
    unittest.main()
