#!/usr/bin/env python3

"""Tests for audit/checks/review.py.

Run with: python3 scripts/tests/test_review.py
"""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import check as check_module  # noqa: E402
from audit import github  # noqa: E402
from audit.checks import review  # noqa: E402
from audit.text import patterns  # noqa: E402
from tests.base import CheckTestCase, FixtureRepo  # noqa: E402


# review-coverage and review-scope-completeness shell out to
# review-tracking.py, which needs a real checkout; the suite drives it
# the way the check does.
SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'audit-check.py')


class StandInScriptMixin:
    """Swapping review-tracking.py for a script that misbehaves.

    The two review checks run review-tracking.py and read its JSON.
    What they do when it does not answer -- cannot start, times out,
    exits with a status that is not an answer, or prints something
    that is not its report -- is the tool failing rather than the
    repository, so each is a ToolFailure. These are the levers.
    """

    def use_script(self, stdout='', stderr='', returncode=0, refuse_credentials=False):
        """Stand in a script answering stdout, stderr and returncode.

        With refuse_credentials, it instead exits 3 saying so when any
        CREDENTIAL_ENV variable reaches it, which is not an answer
        either check accepts.
        """
        path = os.path.join(self.tempdir(), 'review-tracking.py')
        guard = ''
        if refuse_credentials:
            guard = ('import os\n'
                     f'if any(name in os.environ for name in {github.CREDENTIAL_ENV!r}):\n'
                     '    sys.stderr.write("saw a credential")\n'
                     '    sys.exit(3)\n')
        with open(path, 'w') as f:
            f.write('import sys\n'
                    + guard
                    + f'sys.stdout.write({stdout!r})\n'
                    f'sys.stderr.write({stderr!r})\n'
                    f'sys.exit({returncode})\n')
        patcher = unittest.mock.patch.object(review, 'REVIEW_TRACKING_SCRIPT', path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_raising(self, exception, **kwargs):
        """self.check() with every subprocess.run raising `exception`."""
        def boom(*args, **kwargs):
            raise exception
        with unittest.mock.patch.object(check_module.subprocess, 'run', boom):
            return self.check(**kwargs)


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

    def hooks(self, *entries, top=''):
        """Write a config from (hook id, exclude lines) pairs."""
        body = ''.join(
            f'      - id: {hook_id}\n{exclude}' for hook_id, exclude in entries)
        self.fixture.write(
            '.pre-commit-config.yaml',
            f'{top}repos:\n  - repo: local\n    hooks:\n{body}')

    # Spelled out rather than read from review.py, so that dropping an id
    # from the list under test cannot also drop it from the test.
    REWRITING_HOOKS = ('end-of-file-fixer', 'trailing-whitespace', 'mixed-line-ending',
                       'pretty-format-json', 'file-contents-sorter')

    def test_each_rewriting_hook_id_is_recognised(self):
        """Every id in the list makes the check apply, and fail unprotected."""
        for hook_id in self.REWRITING_HOOKS:
            with self.subTest(hook=hook_id):
                self.fixture.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
                self.config('', hooks=hook_id)
                self.assert_fail(self.check(), containing=hook_id)

    def test_per_hook_exclude_passes_on_each_rewriting_hook(self):
        for hook_id in self.REWRITING_HOOKS:
            with self.subTest(hook=hook_id):
                self.adopt()
                self.hooks((hook_id, '        exclude: ^\\.vscode/.*\\.weaudit\n'))
                self.assert_pass(self.check())

    def test_exclude_on_an_unrelated_hook_fails(self):
        """An exclude on gitleaks does nothing for end-of-file-fixer."""
        self.adopt()
        self.hooks(('gitleaks', '        exclude: ^\\.vscode/.*\\.weaudit\n'),
                   ('end-of-file-fixer', ''))
        self.assert_fail(self.check(), containing='end-of-file-fixer')

    def test_exclude_on_a_hook_before_the_rewriting_hook_does_not_leak(self):
        self.adopt()
        self.hooks(('check-yaml', '        exclude: ^\\.vscode/.*\\.weaudit\n'),
                   ('trailing-whitespace', ''))
        self.assert_fail(self.check(), containing='trailing-whitespace')

    def test_only_the_protected_rewriting_hook_passes(self):
        """Two rewriting hooks, one protected: the other still rewrites the marks."""
        self.adopt()
        self.hooks(('end-of-file-fixer', '        exclude: ^\\.vscode/.*\\.weaudit\n'),
                   ('trailing-whitespace', ''))
        result = self.check()
        self.assert_fail(result, containing='trailing-whitespace')
        self.assertNotIn('end-of-file-fixer', result['details'])

    def test_both_rewriting_hooks_protected_passes(self):
        self.adopt()
        exclude = '        exclude: ^\\.vscode/.*\\.weaudit\n'
        self.hooks(('end-of-file-fixer', exclude), ('trailing-whitespace', exclude))
        self.assert_pass(self.check())

    def test_top_level_exclude_protects_every_rewriting_hook(self):
        self.adopt()
        self.hooks(('end-of-file-fixer', ''), ('trailing-whitespace', ''),
                   top='exclude: ^\\.vscode/.*\\.weaudit\n')
        self.assert_pass(self.check())

    def test_block_scalar_exclude_matching_the_marks_passes(self):
        self.adopt()
        self.hooks(('end-of-file-fixer',
                    '        exclude: |\n'
                    '          (?x)^(\n'
                    '            docs/generated/|\n'
                    '            \\.vscode/.*\\.weaudit\n'
                    '          )\n'))
        self.assert_pass(self.check())

    def test_block_scalar_exclude_not_matching_the_marks_fails(self):
        """The `|` indicator is not the pattern, which as a regex matches everything."""
        self.adopt()
        self.hooks(('end-of-file-fixer',
                    '        exclude: |\n'
                    '          (?x)^(\n'
                    '            docs/generated/|\n'
                    '            vendor/\n'
                    '          )$\n'))
        self.assert_fail(self.check(), containing='end-of-file-fixer')

    def test_every_block_scalar_indicator_is_read_as_text(self):
        for indicator in ('|', '>', '|-', '>-', '|+', '>+', '| # why'):
            with self.subTest(indicator=indicator):
                self.adopt()
                self.hooks(('end-of-file-fixer',
                            f'        exclude: {indicator}\n'
                            '          ^vendor/\n'))
                self.assert_fail(self.check(), containing='end-of-file-fixer')

    def test_literal_block_scalar_keeps_its_trailing_newline(self):
        """`|` ends the pattern in a newline pre-commit would try to match; `|-` does not."""
        self.adopt()
        self.hooks(('end-of-file-fixer',
                    '        exclude: |\n'
                    '          ^\\.vscode/.*\\.weaudit\n'))
        self.assert_fail(self.check(), containing='end-of-file-fixer')
        self.hooks(('end-of-file-fixer',
                    '        exclude: |-\n'
                    '          ^\\.vscode/.*\\.weaudit\n'))
        self.assert_pass(self.check())

    def test_folded_block_scalar_joins_its_lines_with_a_space(self):
        self.adopt()
        self.hooks(('end-of-file-fixer',
                    '        exclude: >-\n'
                    '          ^\\.vscode/\n'
                    '          .*\\.weaudit\n'))
        self.assert_fail(self.check(), containing='end-of-file-fixer')

    def test_exclude_nested_inside_another_key_is_not_the_hooks_own(self):
        self.adopt()
        self.hooks(('end-of-file-fixer',
                    '        description:\n'
                    '          exclude: ^\\.vscode/.*\\.weaudit\n'))
        self.assert_fail(self.check(), containing='end-of-file-fixer')

    def test_block_scalar_top_level_exclude_matching_the_marks_passes(self):
        self.adopt()
        self.hooks(('end-of-file-fixer', ''),
                   top='exclude: >-\n  ^\\.vscode/.*\\.weaudit\n\n')
        self.assert_pass(self.check())

    def test_hook_lookalikes_inside_a_block_scalar_are_not_hooks(self):
        """A script body mentioning `- id:` or `exclude:` is not configuration."""
        self.adopt()
        self.hooks(('local-script',
                    '        entry: |\n'
                    '          echo "- id: end-of-file-fixer"\n'
                    '          exclude: ^\\.vscode/.*\\.weaudit\n'))
        self.assert_skip(self.check(), containing='No file-rewriting')

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

    def test_a_pathological_exclude_is_skipped_not_waited_on(self):
        # The repository wrote it, and against the sample paths it would
        # run past the job limit: one committer could hold back the
        # compliance page for the fleet.
        self.addCleanup(patterns._bounded_search.cache_clear)
        self.addCleanup(setattr, patterns, 'SEARCH_TIMEOUT', patterns.SEARCH_TIMEOUT)
        patterns.SEARCH_TIMEOUT = 0.5
        self.adopt()
        self.config('exclude: ^(.|.)*Z\n\n')
        self.assert_fail(self.check(), containing='weaudit')


class ReviewCoverageTest(StandInScriptMixin, CheckTestCase):
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

    def test_imported_reviews_count_and_are_named(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files[:2])
        # What import would write for two files whose bytes were
        # reviewed in shakenfist/development; status only checks the
        # blob SHA against HEAD.
        entries = {}
        for path in files[2:4]:
            sha = subprocess.run(['git', 'rev-parse', f'HEAD:{path}'],
                                 cwd=self.fixture.path, capture_output=True,
                                 text=True, check=True).stdout.strip()
            entries[path] = {'sha': sha, 'date': '2026-01-02',
                             'imported': {'repo': 'shakenfist/development',
                                          'reviewer': 'mikal', 'path': path,
                                          'commit': '0' * 40,
                                          'verified': True}}
        self.fixture.write('.vscode/imports.weaudit-shas.json',
                           json.dumps({'version': 1, 'files': entries}))
        self.fixture.commit('import')
        result = self.check()
        self.assert_pass(result)
        self.assertEqual(
            result['details'],
            '4 of 6 in-scope files reviewed at HEAD (2 imported from '
            'shakenfist/development); 2 need review (threshold 5)')

    def test_no_imported_note_without_imports(self):
        files = [f'f{i}.py' for i in range(2)]
        self.make_reviewed_repo(files, reviewed=files)
        self.assertEqual(
            self.check()['details'],
            '2 of 2 in-scope files reviewed at HEAD; 0 need review '
            '(threshold 5)')

    def test_an_unusable_checkout_is_a_tool_failure(self):
        # The window is narrow: the scope file is checked before the
        # exec, so reaching this needs the checkout to become unusable
        # between the two. It is the tooling failing either way.
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        self.assert_tool_failure(
            lambda: self.run_raising(PermissionError(13, 'Permission denied')),
            containing='review-tracking.py status could not run')

    def test_a_timeout_is_a_tool_failure(self):
        self.make_reviewed_repo(['a.py'], reviewed=[])
        self.assert_tool_failure(
            lambda: self.run_raising(subprocess.TimeoutExpired('python3', 60)),
            containing='review-tracking.py status timed out')

    def test_a_missing_script_is_a_tool_failure(self):
        # The neighbouring failure mode, asserted so the two do not get
        # conflated: a missing review-tracking.py does not fail to
        # start, because sys.executable is what subprocess looks for.
        # The interpreter exits non-zero and the returncode branch
        # raises.
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        with unittest.mock.patch.object(
                review, 'REVIEW_TRACKING_SCRIPT',
                os.path.join(self.fixture.path, 'moved-away.py')):
            self.assert_tool_failure(self.check, containing='review-tracking.py status failed')

    def test_review_tracking_sees_no_github_credential(self):
        # The audit step holds a GitHub token, and the script runs inside
        # the audited checkout.
        self.make_reviewed_repo(['a.py'], reviewed=[])
        self.use_script(stdout=json.dumps({'reviewed': 1, 'in_scope': 1, 'needing_review': 0,
                                           'stale': [], 'never_reviewed': []}),
                        refuse_credentials=True)
        with unittest.mock.patch.dict(os.environ, {
                'GH_TOKEN': 'audit-token', 'GITHUB_TOKEN': 'job-token'}):
            result = self.check()
        self.assert_pass(result)

    def test_a_state_file_linked_out_of_the_checkout_is_a_tool_failure(self):
        # The real script, refusing the link: an error, not a verdict.
        self.make_reviewed_repo(['a.py'], reviewed=[])
        outside = os.path.join(self.tempdir(), 'outside.weaudit')
        with open(outside, 'w') as f:
            f.write(json.dumps({'auditedFiles': [], 'partiallyAuditedFiles': []}))
        os.symlink(outside, os.path.join(self.fixture.path, '.vscode', 'evil.weaudit'))
        self.assert_tool_failure(
            self.check,
            containing="review-tracking.py status failed: review-tracking: ERROR: "
                       "'.vscode/evil.weaudit' resolves outside the repository")

    def test_unparseable_output_is_a_tool_failure(self):
        self.make_reviewed_repo(['a.py'], reviewed=[])
        self.use_script(stdout='not json')
        self.assert_tool_failure(self.check, containing='unparseable JSON')

    def test_json_without_the_counts_is_a_tool_failure(self):
        # Read with subscripts, any of these would raise as a bug.
        self.make_reviewed_repo(['a.py'], reviewed=[])
        complete = {'reviewed': 1, 'in_scope': 1, 'needing_review': 0,
                    'stale': [], 'never_reviewed': []}
        for body in ([], {'reviewed': 1}, dict(complete, needing_review='0'),
                     dict(complete, stale=None)):
            with self.subTest(body=body):
                self.use_script(stdout=json.dumps(body))
                self.assert_tool_failure(self.check, containing='without the counts')


class ReviewScopeCompletenessTest(StandInScriptMixin, CheckTestCase):
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

    def test_an_unusable_checkout_is_a_tool_failure(self):
        # The mirror of ReviewCoverageTest's test of the same name: a
        # different message, reached by a different call.
        self.commit('include = ["*.py"]\n', ['a.py', 'config.json'])
        self.assert_tool_failure(
            lambda: self.run_raising(PermissionError(13, 'Permission denied')),
            containing='review-tracking.py scope-orphans could not run')

    def test_a_timeout_is_a_tool_failure(self):
        self.commit('include = ["*.py"]\n', ['a.py'])
        self.assert_tool_failure(
            lambda: self.run_raising(subprocess.TimeoutExpired('python3', 60)),
            containing='review-tracking.py scope-orphans timed out')

    def test_an_exit_status_that_is_not_an_answer_is_a_tool_failure(self):
        # 0 and 1 are answers -- no orphans, and some -- so only a
        # status outside them is the run breaking.
        self.commit('include = ["*.py"]\n', ['a.py'])
        self.use_script(stdout='{"orphans": []}', stderr='usage: ...', returncode=2)
        self.assert_tool_failure(self.check, containing='scope-orphans failed: usage')

    def test_a_crash_is_a_tool_failure_carrying_its_stderr(self):
        # A traceback also exits 1, the status orphans use, so the
        # unparseable output is where a crash is caught, and stderr is
        # what says it was one.
        self.commit('include = ["*.py"]\n', ['a.py'])
        self.use_script(stdout='', stderr='Traceback: boom', returncode=1)
        self.assert_tool_failure(self.check, containing='Traceback: boom')

    def test_json_without_an_orphans_list_is_a_tool_failure(self):
        self.commit('include = ["*.py"]\n', ['a.py'])
        for body in ('[]', '{}', '{"orphans": "x.json"}'):
            with self.subTest(body=body):
                self.use_script(stdout=body, returncode=1)
                self.assert_tool_failure(self.check, containing='without an orphans list')

    def test_review_tracking_sees_no_github_credential(self):
        self.commit('include = ["*.py"]\n', ['a.py'])
        self.use_script(stdout='{"orphans": [], "orphan_count": 0}', refuse_credentials=True)
        with unittest.mock.patch.dict(os.environ, {
                'GH_TOKEN': 'audit-token', 'GITHUB_TOKEN': 'job-token'}):
            result = self.check()
        self.assert_pass(result)

    def test_orphans_reported_with_status_one_are_a_finding(self):
        self.commit('include = ["*.py"]\n', ['a.py'])
        self.use_script(stdout='{"orphans": ["x.json"]}', returncode=1)
        self.assertEqual(self.assert_fail(self.check())['missing'], ['x.json'])


class SfuiVendorTest(StandInScriptMixin, CheckTestCase):
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
                '[ -z "${GH_TOKEN:-}${GITHUB_TOKEN:-}" ] || exit 3\n'
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

    def _stamp_shaped(self, shape):
        """A vendored copy whose .sfui-commit is not a file to read.

        A link out of the clone points at a real sha, so that a check
        which followed it would go on to clone and judge it.
        """
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, self._head(canonical))
        stamp = os.path.join(self.fixture.path, 'static/sfui/.sfui-commit')
        outside = os.path.join(tmp, 'outside-stamp')
        with open(stamp) as f, open(outside, 'w') as g:
            g.write(f.read())
        os.remove(stamp)
        if shape == 'link':
            os.symlink(outside, stamp)
        elif shape == 'dangling':
            os.symlink(os.path.join(tmp, 'missing'), stamp)
        elif shape == 'directory':
            os.makedirs(stamp)
        return self.check(check_args={'canonical_url': canonical})

    def test_stamp_linked_out_of_the_clone_is_no_stamp(self):
        self.assert_skip(self._stamp_shaped('link'))

    def test_dangling_stamp_is_no_stamp(self):
        self.assert_skip(self._stamp_shaped('dangling'))

    def test_directory_named_like_the_stamp_is_no_stamp(self):
        self.assert_skip(self._stamp_shaped('directory'))

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

    def test_commit_off_the_default_branch_fails_without_running_it(self):
        # A full clone holds every pushed branch, so a commit nobody
        # merged is still "in the canonical repository". Its
        # tools/vendor.sh is unreviewed code, and must not run.
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._git(canonical, 'checkout', '--quiet', '-b', 'unmerged')
        marker = os.path.join(tmp, 'vendor-sh-ran')
        with open(os.path.join(canonical, 'tools', 'vendor.sh'), 'w') as f:
            f.write(f'#!/bin/bash\ntouch {marker}\n')
        self._git(canonical, 'commit', '--quiet', '-am', 'unreviewed')
        unmerged = self._head(canonical)
        self._git(canonical, 'checkout', '--quiet', '-')
        self._make_consumer(tmp, unmerged)
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result,
                         containing='not on the canonical default branch')
        self.assertFalse(os.path.exists(marker))

    def test_canonical_tooling_sees_no_github_credential(self):
        # The audit step holds a GitHub token. The stand-in vendor.sh exits
        # non-zero if either variable reaches it, which would read as an
        # edited copy.
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, self._head(canonical))
        with unittest.mock.patch.dict(os.environ, {
                'GH_TOKEN': 'audit-token', 'GITHUB_TOKEN': 'job-token'}):
            result = self.check(check_args={'canonical_url': canonical})
        self.assert_pass(result)

    def test_malformed_stamp_fails(self):
        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, 'not-a-sha')
        result = self.check(check_args={'canonical_url': canonical})
        self.assert_fail(result,
                         containing='does not contain a commit sha')

    def test_a_failed_clone_is_a_tool_failure(self):
        # Whatever the vendored copy holds, the audit has nothing to
        # compare it with: a fail would blame the consumer for an
        # outage, and closes nothing it should.
        tmp = self.tempdir()
        self._make_consumer(tmp, '0' * 40)
        self.assert_tool_failure(
            lambda: self.check(check_args={'canonical_url': os.path.join(tmp, 'nowhere')}),
            containing='Could not clone canonical sfui')

    def test_a_clone_timeout_is_a_tool_failure(self):
        tmp = self.tempdir()
        self._make_consumer(tmp, '0' * 40)
        self.assert_tool_failure(
            lambda: self.run_raising(subprocess.TimeoutExpired('git', 120),
                                     check_args={'canonical_url': tmp}),
            containing='git clone of canonical sfui timed out')

    def failing_git(self, subcommand):
        """subprocess.run, except that `git <subcommand>` exits 128."""
        real = subprocess.run

        def run(args, *a, **kw):
            if args[0] == 'git' and subcommand in args:
                return subprocess.CompletedProcess(args, 128, '', f'fatal: {subcommand} broke')
            return real(args, *a, **kw)
        return unittest.mock.patch.object(check_module.subprocess, 'run', run)

    def test_rev_list_answering_with_something_other_than_a_count_is_a_tool_failure(self):
        real = subprocess.run

        def run(args, *a, **kw):
            if args[0] == 'git' and 'rev-list' in args:
                return subprocess.CompletedProcess(args, 0, 'warning: odd\n', '')
            return real(args, *a, **kw)

        tmp = self.tempdir()
        canonical = self._make_canonical(tmp)
        self._make_consumer(tmp, self._head(canonical))
        with unittest.mock.patch.object(check_module.subprocess, 'run', run):
            self.assert_tool_failure(
                lambda: self.check(check_args={'canonical_url': canonical}),
                containing="returned 'warning: odd', not a count")

    def test_git_failing_after_the_clone_is_a_tool_failure(self):
        # Each of these exits 128 for git failing, as opposed to the
        # statuses that are answers: merge-base's 1 is "not merged",
        # and is a finding.
        for subcommand in ('merge-base', 'checkout', 'rev-list'):
            with self.subTest(subcommand=subcommand):
                tmp = self.tempdir()
                canonical = self._make_canonical(tmp)
                self._make_consumer(tmp, self._head(canonical))
                with self.failing_git(subcommand):
                    self.assert_tool_failure(
                        lambda: self.check(check_args={'canonical_url': canonical}),
                        containing=f'fatal: {subcommand} broke')


if __name__ == '__main__':
    unittest.main()
