#!/usr/bin/env python3

"""Tests for audit-check.py checks.

Run with: python3 scripts/test_audit_check.py
"""

# audit-ok: plan-reference-file
#
# Every plan path in this file is a fixture, not a pointer. The plan
# checks are tested by writing plans into a temporary directory and
# naming them, and their failing cases exist precisely to name plans
# that do not resolve. None of it is a trail a reader would follow
# into docs/plans/, and marking fifty individual lines would bury the
# lines the per-line marker is actually meant for.

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'audit-check.py'
)

# audit_common lives beside audit-check.py, which is only on sys.path
# by accident of how this suite happens to be invoked. Inserted
# explicitly, the same way test_audit_update_docs.py does it.
sys.path.insert(0, os.path.dirname(SCRIPT))

from audit import registry  # noqa: E402
from audit_common import AUDIT_METADATA, ISSUE_TITLES  # noqa: E402

# This repository, for the tests that check a check against the spec
# page or the canonical template it is supposed to agree with.
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

# These tests drive fixture git repositories, and the pre-commit hook
# runs them during `git commit`, when git exports GIT_INDEX_FILE and
# friends to hooks. Inherited by the fixture git subprocesses, those
# variables point git at the outer repository's index, so the tests
# wreck the real index instead of exercising their fixtures. Scrub
# them from this process so every child starts clean.
for _variable in [name for name in os.environ if name.startswith('GIT_')]:
    del os.environ[_variable]

# audit-check.py is not importable by name (the hyphen is not a valid
# module identifier), so load it from its path.
_spec = importlib.util.spec_from_file_location('audit_check', SCRIPT)
audit_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_check)


class CiReviewAutomationSpecTest(unittest.TestCase):
    """The check and its spec page name the same requirements.

    A rewrite of the page condensed the "What we check" list and
    dropped review-pr-with-claude@main from it, while the check went
    on filing "No workflow uses shared action
    review-pr-with-claude@main" against repositories -- so a
    maintainer following the issue link landed on a page that did not
    state the thing they were being measured against. Deriving the
    agreement is what stops that recurring in a new guise.
    """

    def _measured(self):
        # The "Measured" subsection only. Asserting against the whole
        # page passes on the strength of the auto-generated compliance
        # table at the bottom, which quotes the issue message verbatim
        # -- so the assertion would hold precisely while a repository
        # was being failed for a requirement the page never states.
        # And asserting against all of "What we check" would let a
        # requirement satisfy it from the list the check does *not*
        # measure, which is the opposite claim.
        with open(os.path.join(
                REPO_ROOT, 'docs', 'audits',
                'ci-review-automation.md')) as f:
            spec = f.read()
        start = spec.index('### Measured')
        return spec[start:spec.index('\n### ', start + 1)]

    def test_the_spec_names_every_requirement(self):
        spec = self._measured()
        for requirement in (
            audit_check.CI_REVIEW_DEVELOPER_WORKFLOWS
            + (audit_check.CI_REVIEW_SHARED_ACTION,
               audit_check.CI_REVIEW_TRIGGER_ACTION)
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, spec)


class ReviewCoverageTest(unittest.TestCase):
    """Tests check_review_coverage against fixture git repositories.

    The check shells out to review-tracking.py status, which needs a
    real repository: committed files so blob SHAs resolve, weAudit
    state, and a stamped sidecar.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test User')
        self.git('config', 'commit.gpgsign', 'false')
        os.mkdir(os.path.join(self.repo, '.vscode'))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git'] + list(args), cwd=self.repo, check=True,
                              capture_output=True, text=True)

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def review_tracking(self, *args):
        script = os.path.join(os.path.dirname(SCRIPT), 'review-tracking.py')
        return subprocess.run([sys.executable, script] + list(args),
                              cwd=self.repo, capture_output=True, text=True)

    def make_reviewed_repo(self, files, reviewed):
        """Create and commit files, mark some reviewed, stamp, commit."""
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')
        for path in files:
            self.write(path, f'# {path}\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')
        if reviewed:
            self.write('.vscode/testuser.weaudit', json.dumps({
                'auditedFiles': [{'path': p, 'author': 'testuser'} for p in reviewed],
                'partiallyAuditedFiles': [],
            }))
            self.review_tracking('stamp')
            self.git('add', '-A')
            self.git('commit', '-m', 'reviews')

    def make_stale(self, files):
        for path in files:
            self.write(path, f'# {path} changed\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'changes')

    def check(self):
        return audit_check.check_review_coverage(self.repo, {})

    def test_not_applicable_without_scope_config(self):
        self.write('a.py', 'a = 1\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')
        result = self.check()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('review-scope.toml', result['details'])

    def test_backlog_under_threshold_passes(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:4])
        result = self.check()
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('4 need review (threshold 5)', result['details'])
        self.assertNotIn('missing', result)

    def test_backlog_at_threshold_fails_with_work_queue(self):
        files = [f'f{i}.py' for i in range(6)]
        self.make_reviewed_repo(files, reviewed=files)
        self.make_stale(files[:5])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('5 need review (threshold 5)', result['details'])
        self.assertEqual(result['missing'],
                         [f'stale: f{i}.py' for i in range(5)])

    def test_never_reviewed_files_count(self):
        files = [f'f{i}.py' for i in range(5)]
        self.make_reviewed_repo(files, reviewed=[])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertIn('0 of 5 in-scope files reviewed', result['details'])
        self.assertEqual(result['missing'],
                         [f'never reviewed: f{i}.py' for i in range(5)])


class ReviewScopeCompletenessTest(unittest.TestCase):
    """Tests check_review_scope_completeness against fixture repos.

    The check shells out to review-tracking.py scope-orphans, which
    needs a real repository: the orphan set is computed from git
    ls-files, so uncommitted files do not count.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test User')
        self.git('config', 'commit.gpgsign', 'false')
        os.mkdir(os.path.join(self.repo, '.vscode'))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git'] + list(args), cwd=self.repo, check=True,
                              capture_output=True, text=True)

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def commit(self, scope, files):
        if scope is not None:
            self.write('.vscode/review-scope.toml', scope)
        for path in files:
            self.write(path, f'# {path}\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'initial')

    def check(self):
        return audit_check.check_review_scope_completeness(self.repo, {})

    def test_not_applicable_without_scope_config(self):
        self.commit(None, ['a.py'])
        result = self.check()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('review-scope.toml', result['details'])

    def test_a_file_no_include_pattern_names_fails(self):
        self.commit('include = ["*.py"]\n', ['a.py', 'config.json'])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertEqual(result['missing'], ['config.json'])
        self.assertIn('1 tracked file(s)', result['details'])

    def test_an_excluded_file_passes(self):
        # Excluding is a decision. The check is about omission, not
        # about how much of the repository gets reviewed.
        self.commit('include = ["*.py"]\nexclude = ["config.json"]\n',
                    ['a.py', 'config.json'])
        result = self.check()
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertNotIn('missing', result)

    def test_an_empty_include_passes(self):
        # An empty include means every tracked file, so there is
        # nothing left to omit. This is the trivially compliant
        # configuration the spec offers to small repositories.
        self.commit('exclude = ["config.json"]\n', ['a.py', 'config.json'])
        result = self.check()
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_every_orphan_is_listed_not_a_sample(self):
        # The issue body is the work queue, and the fix is per file.
        self.commit('include = ["*.py"]\n',
                    ['a.py'] + [f'f{i}.json' for i in range(9)])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertEqual(result['missing'],
                         [f'f{i}.json' for i in range(9)])

    def test_full_coverage_does_not_excuse_a_narrow_scope(self):
        # The failure mode this check exists for: review-coverage sees
        # a fully reviewed repository precisely because the scope was
        # narrowed to the one file that was reviewed.
        self.commit('include = ["a.py"]\n', ['a.py', 'b.py', 'c.py'])
        result = self.check()
        self.assertEqual(result['status'], 'fail', result['details'])
        self.assertEqual(result['missing'], ['b.py', 'c.py'])


class ReviewMarksPreCommitTest(unittest.TestCase):
    """Tests check_review_marks_pre_commit against config fixtures."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        os.mkdir(os.path.join(self.repo, '.vscode'))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, content):
        with open(os.path.join(self.repo, path), 'w') as f:
            f.write(content)

    def adopt(self):
        self.write('.vscode/review-scope.toml', 'include = ["*.py"]\n')

    def check(self):
        return audit_check.check_review_marks_pre_commit(self.repo, {})

    def config(self, body, hooks='end-of-file-fixer'):
        """Write a config running `hooks`, prefixed by `body`."""
        hook_lines = ''.join(
            f'      - id: {h}\n' for h in hooks.split() if h
        )
        self.write(
            '.pre-commit-config.yaml',
            f'{body}repos:\n  - repo: local\n    hooks:\n{hook_lines}'
        )

    def test_not_applicable_without_scope_config(self):
        self.config('')
        self.assertEqual(self.check()['status'], 'not_applicable')

    def test_not_applicable_without_pre_commit_config(self):
        self.adopt()
        self.assertEqual(self.check()['status'], 'not_applicable')

    def test_not_applicable_without_a_rewriting_hook(self):
        """ryll's shape: scanners and linters, but no formatter.

        Nothing rewrites the marks, so there is nothing to exclude --
        and demanding a blanket exclude here would hide review prose
        from gitleaks and the bidi scanner.
        """
        self.adopt()
        self.config('', hooks='gitleaks bidi-check shellcheck')
        result = self.check()
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('No file-rewriting', result['details'])

    def test_top_level_exclude_passes(self):
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit\n\n')
        self.assertEqual(self.check()['status'], 'pass')

    def test_per_hook_exclude_passes(self):
        """A hook-level exclude protects the files just as well."""
        self.adopt()
        self.write('.pre-commit-config.yaml',
                   'repos:\n'
                   '  - repo: local\n'
                   '    hooks:\n'
                   '      - id: end-of-file-fixer\n'
                   '        exclude: ^\\.vscode/.*\\.weaudit\n')
        self.assertEqual(self.check()['status'], 'pass')

    def test_quoted_exclude_passes(self):
        self.adopt()
        self.config("exclude: '^\\.vscode/.*\\.weaudit'\n\n")
        self.assertEqual(self.check()['status'], 'pass')

    def test_trailing_whitespace_hook_also_counts(self):
        self.adopt()
        self.config('', hooks='trailing-whitespace')
        result = self.check()
        self.assertEqual(result['status'], 'fail')
        self.assertIn('trailing-whitespace', result['details'])

    def test_no_exclude_fails(self):
        self.adopt()
        self.config('')
        result = self.check()
        self.assertEqual(result['status'], 'fail')
        self.assertIn('weaudit', result['details'])

    def test_exclude_missing_the_sidecar_fails(self):
        """An anchored pattern catches the weaudit file but not its json."""
        self.adopt()
        self.config('exclude: ^\\.vscode/.*\\.weaudit$\n\n')
        self.assertEqual(self.check()['status'], 'fail')

    def test_unrelated_exclude_fails(self):
        self.adopt()
        self.config('exclude: ^kerbside/api/static/\n\n')
        self.assertEqual(self.check()['status'], 'fail')

    def test_uncompilable_exclude_is_skipped_not_raised(self):
        self.adopt()
        self.config('exclude: ^[unclosed\n\n')
        self.assertEqual(self.check()['status'], 'fail')


class AuditScopeIsStatedOnceTest(unittest.TestCase):
    """The three places that say who is audited must agree.

    Scope is written down three times: the matrix in
    .github/workflows/consistency-audit.yml is what actually runs,
    and the in-scope and excluded lists in docs/audits/README.md are
    what a reader is told. Nothing else ties them together, so a
    repository added to the matrix alone is audited while the
    documentation says it is not -- and one dropped from the matrix
    alone silently stops being measured while the documentation says
    it is.

    Reading two of the three means splitting prose on a literal
    phrase, so this class also holds those phrases to their job. See
    bulleted_block() for what a phrase has to keep doing to stay a
    usable anchor.
    """

    root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    # Each list below is found by splitting a file on a literal phrase:
    # two sentences of prose and one line of YAML indentation, in files
    # nobody edits with a parser in mind. A start phrase that gets
    # reworded away is loud, because the split raises. An end phrase
    # that gets reworded away is the dangerous one -- the block simply
    # runs on to the end of the file and collects every bullet after
    # it, which the comparisons here can still pass on. So the phrases
    # are named constants and bulleted_block() asserts they still
    # delimit a list of repository names before anything trusts them.
    EXCLUDED_DOC = 'docs/audits/README.md'
    EXCLUDED_START = 'are **excluded**'
    EXCLUDED_END = 'The `actions` repository'
    EXCLUDED_BULLET = '* '

    IN_SCOPE_DOC = 'docs/audits/README.md'
    IN_SCOPE_START = '## In-scope projects'
    IN_SCOPE_END = 'One project is in scope'
    IN_SCOPE_BULLET = '- '

    MATRIX_WORKFLOW = '.github/workflows/consistency-audit.yml'
    MATRIX_START = '        repo:\n'
    MATRIX_BULLET = '          - '

    # What a GitHub repository in any of these lists looks like. The
    # point is not to validate the name but to notice a parse that has
    # started collecting prose: a swallowed paragraph brings back
    # bullets like "The configured version file path must be covered".
    #
    # Anchored at both ends. assertRegex is re.search, so an
    # end-anchor alone matches any sentence closing on a lowercase
    # word -- including that exact example, which is what this guard
    # exists to reject.
    REPO_NAME = re.compile(r'^[a-z0-9][a-z0-9.-]*$')

    def read(self, relative):
        with open(os.path.join(self.root, relative)) as f:
            return f.read()

    def bulleted_block(self, path, start, end, bullet):
        """Return the bullet list delimited by two literal phrases.

        Every assertion here is about the parse rather than the
        content, so that a reworded document fails with the phrase it
        needs to carry rather than with a comparison of two sets of
        repository names that no longer means anything.
        """
        text = self.read(path)
        self.assertEqual(
            text.count(start), 1,
            f'{path} must contain the phrase "{start}" exactly once: '
            f'it is where this suite starts reading the list that '
            f'follows it',
        )
        after = text.split(start, 1)[1]
        self.assertEqual(
            after.count(end), 1,
            f'{path} must contain the phrase "{end}" exactly once '
            f'after "{start}": it is where this suite stops reading, '
            f'and without it the parse runs to the end of the file',
        )
        block = after.split(end, 1)[0]
        # Any heading level, not just '## '. The excluded-projects
        # list this guards sits under a '### ', so a '###' subsection
        # inserted inside the block would have slipped past a check
        # for '## ' alone.
        self.assertIsNone(
            re.search(r'^#{1,6} ', block, re.MULTILINE),
            f'the list after "{start}" in {path} now runs past a '
            f'heading, so "{end}" is no longer the end of it',
        )
        entries = [
            line[len(bullet):].strip() for line in block.splitlines()
            if line.startswith(bullet)
        ]
        self.assertTrue(
            entries,
            f'no "{bullet}" bullets between "{start}" and "{end}" in '
            f'{path}; the list has moved or changed its bullet style',
        )
        for entry in entries:
            self.assertRegex(
                entry, self.REPO_NAME,
                f'"{entry}" was read as a repository name from the '
                f'list after "{start}" in {path}, so the parse is '
                f'picking up something that is not that list',
            )
        return entries

    def matrix_repos(self):
        text = self.read(self.MATRIX_WORKFLOW)
        self.assertEqual(
            text.count(self.MATRIX_START), 1,
            f'{self.MATRIX_WORKFLOW} must contain the matrix key '
            f'"{self.MATRIX_START.strip()}" at exactly one '
            f'indentation this suite recognises',
        )
        block = text.split(self.MATRIX_START, 1)[1]
        repos = []
        for line in block.splitlines():
            if line.startswith(self.MATRIX_BULLET):
                repos.append(line[len(self.MATRIX_BULLET):].strip())
            elif line.strip() and not line.lstrip().startswith('#'):
                break
        self.assertTrue(
            repos,
            f'no matrix entries read from {self.MATRIX_WORKFLOW}; the '
            f'list is indented differently to "{self.MATRIX_BULLET}"',
        )
        for repo in repos:
            self.assertRegex(
                repo, self.REPO_NAME,
                f'"{repo}" was read as a repository name from the '
                f'audit matrix, so the parse has overrun the list',
            )
        return repos

    def documented_in_scope(self):
        return self.bulleted_block(
            self.IN_SCOPE_DOC, self.IN_SCOPE_START, self.IN_SCOPE_END,
            self.IN_SCOPE_BULLET,
        )

    def documented_excluded(self):
        return self.bulleted_block(
            self.EXCLUDED_DOC, self.EXCLUDED_START, self.EXCLUDED_END,
            self.EXCLUDED_BULLET,
        )

    def partially_scoped(self):
        return {
            name for name, overrides
            in audit_check.REPO_OVERRIDES.items()
            if overrides.get('only_checks')
        }

    def test_a_parse_that_overruns_its_list_is_rejected(self):
        """The REPO_NAME guard must fire, not merely exist.

        Reading an assertion cannot distinguish one that holds from one
        that cannot fail, so this hands bulleted_block() the failure it
        was written for. The loud cases are already covered by the
        count assertions: a start or end phrase that vanishes raises
        naming the phrase. The quiet case is an end phrase that has
        drifted further down the page, so the block still terminates
        but now spans a prose list on the way -- with no heading
        crossed, REPO_NAME is the only thing left to notice.

        The bullet used here is the example named in the comment above
        REPO_NAME, which an end-anchored pattern accepted: re.search
        found 'covered' at the end of it and passed.
        """
        overrun = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '* ryll\n'
            '\n'
            'Some criterion, described in a paragraph that grew a list:\n'
            '\n'
            '* The configured version file path must be covered\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(overrun)
            self.root = tmp
            with self.assertRaisesRegex(
                    AssertionError,
                    'The configured version file path must be covered'):
                self.bulleted_block(
                    'drifted.md', self.EXCLUDED_START, self.EXCLUDED_END,
                    self.EXCLUDED_BULLET,
                )

    def test_repo_name_rejects_a_sentence_ending_in_a_word(self):
        # assertRegex is re.search, so this is the whole point of the
        # leading anchor. Kept separate from the parse above because it
        # is the property, not the plumbing: if REPO_NAME ever loses
        # its '^' again, this is the test that says so in one line.
        self.assertIsNone(
            self.REPO_NAME.search(
                'The configured version file path must be covered'),
            'REPO_NAME matched a sentence, so it is not anchored at '
            'the start and cannot notice a parse collecting prose',
        )
        for name in ['shakenfist', 'client-python', 'kerbside-patches']:
            self.assertIsNotNone(
                self.REPO_NAME.search(name),
                f'REPO_NAME no longer matches the repository name '
                f'"{name}"',
            )

    def test_a_subsection_heading_inside_the_block_is_caught(self):
        # The list this guards sits under a '### ', so a guard that
        # only knew '## ' would not have noticed a '###' subsection
        # appearing inside the parsed span.
        drifted = (
            'Two repositories are **excluded** from the conventions:\n'
            '\n'
            '* imago\n'
            '\n'
            '### Some new subsection\n'
            '\n'
            '* ryll\n'
            '\n'
            'The `actions` repository is a library of composite actions.\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'drifted.md'), 'w') as f:
                f.write(drifted)
            self.root = tmp
            with self.assertRaisesRegex(AssertionError, 'runs past a heading'):
                self.bulleted_block(
                    'drifted.md', self.EXCLUDED_START, self.EXCLUDED_END,
                    self.EXCLUDED_BULLET,
                )

    def test_the_parse_anchors_still_delimit_their_lists(self):
        # The comparisons below are worth no more than the parses that
        # feed them, and all three parses are anchored to phrases in
        # documents that get rewritten for reasons that have nothing
        # to do with this suite -- the page holding both lists was
        # rewritten wholesale more than once already. Run
        # them here on their own so that a reworded anchor fails as a
        # reworded anchor, naming the phrase and the file, rather than
        # as a mysterious disagreement about which repositories are
        # audited. Each parse asserts its own delimiting; this test is
        # what makes sure all three are exercised even if a comparison
        # below is one day rewritten not to call them.
        self.matrix_repos()
        self.documented_in_scope()
        self.documented_excluded()

    def test_matrix_matches_the_documented_scope(self):
        matrix = set(self.matrix_repos())
        self.assertIn('development', matrix)
        self.assertEqual(
            matrix - self.partially_scoped(),
            set(self.documented_in_scope()),
            'the audit matrix and the in-scope list in '
            'docs/audits/README.md disagree',
        )

    def test_no_audited_repo_is_also_documented_as_excluded(self):
        # A repository scoped to a subset of the checks is the one
        # exception: private-ci is excluded from the conventions but
        # audited for sfui-vendor, and both statements are true.
        overlap = (
            set(self.matrix_repos())
            & set(self.documented_excluded())
            - self.partially_scoped()
        )
        self.assertEqual(
            overlap, set(),
            'docs/audits/README.md lists these as excluded but '
            'the audit matrix runs every check against them',
        )


class ExpensiveLanePathFilterTest(unittest.TestCase):
    """Which expensive lanes are allowed to skip a path filter."""

    LINT_JOB = """  lint:
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
    SCAN_JOB = """  gitleaks:
    runs-on: [self-hosted, vm, debian-13, s]
    steps:
      - run: gitleaks detect
"""
    SKILLSAW_JOB = """  agent-context:
    runs-on: [self-hosted, vm]
    steps:
      - run: pre-commit run skillsaw --all-files
"""

    def _repo(self, tmp, workflows, docs=True):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        if docs:
            os.makedirs(os.path.join(tmp, 'docs'))
            with open(os.path.join(tmp, 'docs', 'index.md'), 'w') as f:
                f.write('# Docs\n')
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return audit_check.check_expensive_lane_path_filter(
            tmp, {'has_workflows_dir': True}
        )

    def test_dedicated_scanner_workflow_needs_no_filter(self):
        # Reading the text a filter would skip is the whole job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'gitleaks.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SCAN_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_agent_context_lint_is_a_content_scanner(self):
        # skillsaw reads the text a filter would skip for the same
        # reason gitleaks does: a prompt aimed at an agent lands in a
        # document as readily as a credential.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_scanner_and_a_context_lint_together_are_exempt(self):
        # The shape client-python arrived at: one ungated workflow
        # holding the credential scan and the context lint, and
        # nothing else.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_context_lint_does_not_exempt_the_lanes_beside_it(self):
        # Widening the scanner list must not widen the hole the
        # per-job rule exists to close.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SKILLSAW_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('beside it', result['details'])

    def test_a_context_lint_named_only_in_a_comment_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # skillsaw runs in the supply chain workflow, not here.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_scanner_does_not_exempt_the_lanes_beside_it(self):
        # shakenfist/actions ran lint, unit tests and the LLM
        # reviewer on ephemeral VMs for every documentation typo,
        # and passed this check, because a gitleaks job sat beside
        # them in the same unfiltered workflow.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('beside it', result['details'])

    def test_a_scanner_named_only_in_a_comment_does_not_count(self):
        # Otherwise one comment in an unrelated lane makes a whole
        # workflow look like a dedicated scanner.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # gitleaks-scan.sh is a separate workflow's business.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_filtered_mixed_workflow_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\n    paths-ignore:\n'
                "      - 'docs/**'\njobs:\n"
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_an_unfiltered_lane_with_no_scanner_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'functional-tests.yml': (
                'on:\n  pull_request:\njobs:\n' + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')

    def test_static_runner_lanes_are_not_expensive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                '  lint:\n'
                '    runs-on: [self-hosted, static]\n'
                '    steps:\n      - run: tox -e pep8\n'
            )})
        self.assertEqual(result['status'], 'pass')


class RunnerLabelParsingTest(unittest.TestCase):
    """The two label parsers must agree about what an expression is.

    They answer different questions -- parse_runner_labels() gives up
    on a line it cannot fully resolve, literal_runner_labels() drops
    what it cannot resolve and keeps the rest -- but they must not
    disagree about which *elements* are unresolvable. Nothing tested
    these before, which is how a refactor changed one of them without
    a failure.
    """

    def test_an_expression_in_a_trailing_comment_is_not_a_label(self):
        # The comment is stripped before the expression test, so a
        # perfectly literal runs-on stays judgeable no matter what its
        # comment mentions. Testing the raw value here would skip the
        # line, and a skip in check_static_runner_tags() reports pass.
        value = '[self-hosted, static, s]  # was ${{ matrix.runner }}'
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            audit_check.parse_runner_labels(value))
        self.assertEqual(
            ['self-hosted', 'static', 's'],
            audit_check.literal_runner_labels(value))

    def test_a_bare_expression_is_unjudgeable(self):
        self.assertIsNone(
            audit_check.parse_runner_labels('${{ matrix.runner }}'))

    def test_one_expression_element_makes_the_line_unjudgeable(self):
        self.assertIsNone(audit_check.parse_runner_labels(
            "[self-hosted, '${{ matrix.os }}', s]"))

    def test_a_comma_inside_an_expression_is_not_a_separator(self):
        # Splitting on it would leave fragments which no longer carry
        # the '${{' marking them unresolvable, so literal_runner_labels
        # would take them for labels -- and a fragment which happened
        # to read 'm' would excuse a sizeless job.
        value = ("[self-hosted, vm, "
                 "'${{ format('{0},{1}', matrix.a, matrix.b) }}']")
        self.assertEqual(
            ['self-hosted', 'vm'],
            audit_check.literal_runner_labels(value))
        self.assertIsNone(audit_check.parse_runner_labels(value))


class VmRunnerSizeTest(unittest.TestCase):
    """Every 'vm' runs-on has to name a size, and 'xs' is an answer."""

    # The phrases delimiting the size list in the specification. Named
    # rather than inlined so a reword fails with an explanation.
    SIZE_SENTENCE_START = '* Sizes are '
    SIZE_SENTENCE_END = 'variants.'

    def _repo(self, tmp, workflows):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return audit_check.check_vm_runner_size(
            tmp, {'has_workflows_dir': True}
        )

    def _job(self, runs_on):
        return (
            'on:\n  pull_request:\njobs:\n  build:\n'
            '    runs-on: %s\n'
            '    steps:\n      - run: true\n' % runs_on
        )

    def test_a_sized_vm_job_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, s]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_sizeless_vm_job_is_a_finding(self):
        # The defect this check exists for: no size element, so the
        # conductor falls back to xs and nobody chose it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_a_vm_job_with_no_os_label_is_still_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm]')})
        self.assertEqual('fail', result['status'])

    def test_xs_counts_as_naming_a_size(self):
        # The rule is that the size is chosen, not that it is large.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12, xs]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_bigdisk_variants_count_as_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, 'debian-13', 'xl-bigdisk']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_literal_size_beside_a_matrix_expression_passes(self):
        # kerbside-patches' shape: the OS comes from the matrix, the
        # size is written literally beside it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}', 'xl']")})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_matrix_expression_alone_does_not_excuse_a_missing_size(self):
        # The sibling job in the same file writes the size literally,
        # so an expression here is not evidence a size arrives -- and
        # skipping the line would hide a real sizeless deploy job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                "[self-hosted, vm, '${{ matrix.test.runs_on }}']")})
        self.assertEqual('fail', result['status'])

    def test_static_jobs_are_not_in_scope(self):
        # A static runner must name no size; that is the complementary
        # check's business, and this one must not contradict it.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, static]')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_the_offending_line_is_named(self):
        # A finding has to say where, because the fix is per-line.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {
                'a.yml': self._job('[self-hosted, vm, debian-12]'),
                'b.yml': self._job('[self-hosted, vm, debian-12, m]'),
            })
        self.assertEqual('fail', result['status'])
        self.assertIn('a.yml:5', result['details'])
        self.assertNotIn('b.yml', result['details'])

    def test_a_repository_without_workflows_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit_check.check_vm_runner_size(
                tmp, {'has_workflows_dir': False})
        self.assertEqual('not_applicable', result['status'])

    def test_a_trailing_comment_does_not_hide_the_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  # sized later')})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])

    def test_every_offending_line_is_reported(self):
        # The fix is per-line, so a file with two sizeless jobs has to
        # name both -- stopping at the first would hide the second
        # until the next run.
        job = ('    runs-on: [self-hosted, vm, debian-12]\n'
               '    steps:\n      - run: true\n')
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n' + job +
                '  deploy:\n' + job)})
        self.assertEqual('fail', result['status'])
        self.assertIn('ci.yml:5', result['details'])
        self.assertIn('ci.yml:9', result['details'])

    def test_the_offender_names_the_labels_it_found(self):
        # Matching check_static_runner_tags(): the issue body is what
        # somebody fixing this reads, and the labels say which job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        self.assertIn('(self-hosted, vm, debian-12)', result['details'])

    def test_the_remediation_names_every_size_accepted(self):
        # A job which needs the disk must be able to find its answer
        # in the issue body, not only in the specification.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]')})
        for label in audit_check.VM_SIZE_LABELS:
            self.assertIn(label, result['details'])

    def test_an_audit_ok_marker_exempts_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': self._job(
                '[self-hosted, vm, debian-12]  '
                '# audit-ok: vm-runner-size, sized by the caller')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_an_audit_ok_marker_on_the_line_above_exempts_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    # audit-ok: vm-runner-size, sized by the caller\n'
                '    runs-on: [self-hosted, vm, debian-12]\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'], result['details'])

    def test_a_block_sequence_runs_on_is_not_examined(self):
        # A documented limitation rather than a behaviour we want:
        # RUNS_ON_RE needs a value on the same line, so this shape is
        # invisible to the check. Nothing in scope writes one. If that
        # changes, this test is the place the decision is recorded.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n  build:\n'
                '    runs-on:\n      - self-hosted\n      - vm\n'
                '      - debian-12\n'
                '    steps:\n      - run: true\n')})
        self.assertEqual('pass', result['status'])

    def test_the_size_vocabulary_matches_the_specification(self):
        # VM_SIZE_LABELS is a copy of CI_SIZES in shakenfist/private-ci
        # which this repository cannot reach, so the least it can do is
        # keep its own two copies in step.
        spec = os.path.join(
            REPO_ROOT, 'docs', 'audits', 'workflow-standards.md')
        with open(spec) as f:
            content = f.read()
        start = content.find(self.SIZE_SENTENCE_START)
        self.assertNotEqual(
            -1, start,
            f'{self.SIZE_SENTENCE_START!r} no longer introduces the '
            f'size list in workflow-standards.md')
        end = content.find(self.SIZE_SENTENCE_END, start)
        self.assertNotEqual(
            -1, end,
            f'{self.SIZE_SENTENCE_END!r} no longer ends the size list '
            f'in workflow-standards.md')
        documented = set(re.findall(r'`([^`]+)`', content[start:end]))
        self.assertEqual(set(audit_check.VM_SIZE_LABELS), documented)


class WorkflowJobBlocksTest(unittest.TestCase):
    def test_jobs_are_split_at_top_level_keys(self):
        blocks = audit_check.workflow_job_blocks(
            'name: CI\n'
            'on:\n  pull_request:\n'
            'jobs:\n'
            '  lint:\n    runs-on: a\n'
            '  test:\n    runs-on: b\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint', 'test'])
        self.assertIn('runs-on: a', blocks[0][1])
        self.assertIn('runs-on: b', blocks[1][1])

    def test_keys_outside_jobs_are_not_jobs(self):
        # 'pull_request:' under 'on:' is indented exactly like a job
        # key, so a naive split would invent a job called
        # pull_request and decide the workflow is not all scanners.
        blocks = audit_check.workflow_job_blocks(
            'on:\n  pull_request:\n'
            'jobs:\n  lint:\n    runs-on: a\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint'])

    def test_a_workflow_with_no_jobs_is_not_a_scanner(self):
        self.assertFalse(
            audit_check.is_dedicated_scanner_workflow('on:\n  push:\n')
        )


class RepoOverridesTest(unittest.TestCase):
    def test_actions_repo_properties(self):
        # The actions repository carries Python helper scripts but has
        # nothing to package, and keeps "main" because every consumer
        # pins to @main.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'actions'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('@main', props['default_branch_exception'])

    def test_development_audits_itself(self):
        # development holds the audit tooling and is audited by it.
        # Its Python is never packaged, and it publishes no releases,
        # so it has no release branch for "develop" to be distinct
        # from -- but the exemption has to be a stated reason, not an
        # absence from the matrix.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'development'
        )
        self.assertTrue(props['not_python'])
        self.assertIn('releases', props['default_branch_exception'])

    def test_ordinary_repo_has_no_default_branch_exception(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['default_branch_exception'], '')

    def test_shakenfist_excludes_imported_docs(self):
        # docs/components/ is an automated import of the other
        # repositories' documentation directories.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'shakenfist'
        )
        self.assertEqual(
            props['doc_content_excludes'], ['docs/components/']
        )

    def test_ordinary_repo_has_no_doc_content_excludes(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['doc_content_excludes'], [])

    def test_ordinary_repo_is_scoped_to_no_checks(self):
        # An empty only_checks means the whole audit applies, so the
        # override cannot narrow a repository by accident.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertEqual(props['only_checks'], [])

    def test_private_ci_is_scoped_to_the_sfui_check(self):
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'private-ci'
        )
        self.assertEqual(props['only_checks'], ['sfui-vendor'])


class CheckScopeTest(unittest.TestCase):
    """The only_checks scoping in run_all_checks."""

    def _ids(self):
        # The whole schedule, not just the legacy half. While the
        # migration runs some criteria come from registry.CHECKS and
        # some from check_calls(); the invariant this class protects is
        # about what actually gets scheduled, so it has to read both.
        return [
            check_id for check_id, _ in registry.scheduled(
                legacy=audit_check.check_calls(
                    tempfile.mkdtemp(), {}, 'occystrap', 'shakenfist'
                )
            )
        ]

    def test_every_scheduled_id_is_a_known_check(self):
        # A typo in the id table would make a check unschedulable
        # while still reporting a plausible looking result, so the
        # table has to agree with the issue title map. ISSUE_TITLES is
        # that map itself rather than a copy of it: audit-manage-issues
        # reads it as .get(check_id, check_id), so an id missing from it
        # files under the bare check id and orphans every open issue for
        # that check across the fleet, and audit-update-docs subscripts
        # it directly, so the same omission raises KeyError during docs
        # regeneration.
        #
        # AUDIT_METADATA is the third corner of the same triangle:
        # audit-update-docs iterates it to emit one compliance section
        # per check, and audit-manage-issues reads it for the spec link
        # in each filed issue. Asserting both closes the loop, so a new
        # check cannot be scheduled while missing from either map.
        ids = self._ids()
        self.assertEqual(sorted(ids), sorted(set(ids)))
        self.assertEqual(
            sorted(ids), sorted(ISSUE_TITLES.keys())
        )
        self.assertEqual(
            sorted(ids), sorted(AUDIT_METADATA.keys())
        )

    def test_scoped_repo_runs_only_its_check(self):
        # private-ci is scoped to sfui-vendor. Every other check must
        # be reported not_applicable with the scoping reason, and must
        # not have run: a check that ran would have written its own
        # details, and several of them would reach for the network.
        with tempfile.TemporaryDirectory() as tmp:
            results = audit_check.run_all_checks(
                tmp, 'private-ci', 'shakenfist'
            )

        reason = 'private-ci is audited for sfui-vendor only'
        by_id = {c['id']: c for c in results['checks']}
        self.assertEqual(len(by_id), len(ISSUE_TITLES))

        for check_id, check in by_id.items():
            if check_id == 'sfui-vendor':
                self.assertNotEqual(check['details'], reason)
                continue
            self.assertEqual(check['status'], 'not_applicable')
            self.assertEqual(check['details'], reason)

        # Nothing is dropped from the results, because a check missing
        # from the JSON renders as "unknown" in the docs/audits/ tables.
        self.assertEqual(
            results['summary']['total'], len(ISSUE_TITLES)
        )
        self.assertEqual(results['summary']['fail'], 0)

    def test_unscoped_repo_schedules_everything(self):
        # The scoping is opt in: with no override, no check is
        # replaced by the not_applicable stand-in.
        props = audit_check.detect_repo_properties(
            tempfile.mkdtemp(), 'occystrap'
        )
        self.assertFalse(props['only_checks'])


class SfuiVendorTest(unittest.TestCase):
    """Exercise check_sfui_vendor against fixture repositories.

    The canonical fixture is a tiny git repo carrying a stand-in
    tools/vendor.sh that honours the real script's --check contract
    (diff the distributable files, exit non-zero on difference); the
    real script lives in shakenfist/sfui and is not vendored here.
    """

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
        consumer = os.path.join(tmp, 'consumer')
        vendored = os.path.join(consumer, 'static', 'sfui')
        os.makedirs(vendored)
        with open(os.path.join(vendored, 'tokens.css'), 'w') as f:
            f.write(tokens if tokens is not None else self.TOKENS)
        with open(os.path.join(vendored, '.sfui-commit'), 'w') as f:
            f.write(sha + '\n')
        return consumer

    def test_not_applicable_without_sfui_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            consumer = os.path.join(tmp, 'consumer')
            os.makedirs(consumer)
            result = audit_check.check_sfui_vendor(consumer, {})
            self.assertEqual(result['status'], 'not_applicable')

    def test_verbatim_copy_at_head_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, self._head(canonical))
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'pass')
            self.assertIn('verbatim', result['details'])

    def test_edited_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(
                tmp, self._head(canonical),
                tokens=':root { --sf-bg: #fff; }\n',
            )
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn('edited in place', result['details'])

    def test_copy_behind_canonical_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, self._head(canonical))
            with open(
                os.path.join(canonical, 'tokens.css'), 'a'
            ) as f:
                f.write('/* a change the consumer lacks */\n')
            self._git(canonical, 'commit', '--quiet', '-am', 'more')
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn('behind canonical', result['details'])

    def test_unknown_commit_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, '0' * 40)
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn(
                'not in the canonical repository', result['details']
            )

    def test_malformed_stamp_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            canonical = self._make_canonical(tmp)
            consumer = self._make_consumer(tmp, 'not-a-sha')
            result = audit_check.check_sfui_vendor(
                consumer, {}, canonical_url=canonical
            )
            self.assertEqual(result['status'], 'fail')
            self.assertIn(
                'does not contain a commit sha', result['details']
            )


class MergeQueueConfigTest(unittest.TestCase):
    def _rule(self, **params):
        return {'type': 'merge_queue', 'parameters': params}

    def test_no_merge_queue_rule_returns_none(self):
        self.assertIsNone(audit_check.evaluate_merge_queue_rules([]))
        self.assertIsNone(audit_check.evaluate_merge_queue_rules(
            [{'type': 'deletion'}, {'type': 'non_fast_forward'}]
        ))

    def test_serialized_queue_passes(self):
        problems = audit_check.evaluate_merge_queue_rules([
            {'type': 'pull_request'},
            self._rule(
                max_entries_to_build=1, min_entries_to_merge=1,
                max_entries_to_merge=5,
                min_entries_to_merge_wait_minutes=5,
            ),
        ])
        self.assertEqual(problems, [])

    def test_speculative_stacking_fails(self):
        problems = audit_check.evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=2, min_entries_to_merge=1),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('max_entries_to_build is 2', problems[0])

    def test_batched_merging_fails(self):
        problems = audit_check.evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=1, min_entries_to_merge=2),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('min_entries_to_merge is 2', problems[0])

    def test_missing_parameters_flags_both(self):
        problems = audit_check.evaluate_merge_queue_rules([
            {'type': 'merge_queue'},
        ])
        self.assertEqual(len(problems), 2)


class SelfHostedRunnerLabelPositionTest(unittest.TestCase):
    """A GitHub-hosted label only counts where a runner can be named.

    The check scans every line rather than only 'runs-on:' lines, so
    that matrix values feeding 'runs-on: ${{ matrix.os }}' are caught.
    That breadth is what made it read image names, artifact names and
    job names as runner references.
    """

    def _check(self, workflow):
        with tempfile.TemporaryDirectory() as tmp:
            workflows = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(workflows)
            with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
                f.write(workflow)
            return audit_check.check_self_hosted_runners(
                tmp, {'has_workflows_dir': True}
            )

    def test_a_bare_runs_on_is_reported(self):
        result = self._check('jobs:\n  a:\n    runs-on: ubuntu-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-latest', result['details'])

    def test_a_list_element_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [foo, windows-latest]\n')
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_item_is_reported(self):
        # The reason the check scans every line: this feeds
        # runs-on: ${{ matrix.os }} somewhere else in the file.
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        os:\n          - ubuntu-24.04\n'
            '          - macos-latest\n')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ubuntu-24.04', result['details'])

    def test_a_quoted_matrix_item_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            "        os:\n          - 'windows-11-arm'\n")
        self.assertEqual(result['status'], 'fail')

    def test_a_matrix_include_mapping_is_reported(self):
        result = self._check(
            'jobs:\n  a:\n    strategy:\n      matrix:\n'
            '        include:\n          - os: ubuntu-24.04-arm\n')
        self.assertEqual(result['status'], 'fail')

    def test_self_hosted_on_the_same_line_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: [self-hosted, ubuntu-24.04]\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_marked_exception_is_not_reported(self):
        result = self._check(
            'jobs:\n  a:\n'
            '    # audit-ok: github-hosted-runner\n'
            '    runs-on: macos-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_an_image_path_is_not_a_runner(self):
        # shakenfist's functional-tests.yml names Shaken Fist image
        # labels this way. The trailing path separator before the label
        # is what distinguishes it from a value position.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'sf://label/ci-images/ubuntu-2404'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_job_name_containing_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            "      - run: echo 'ubuntu-2404-slim-primary'\n")
        self.assertEqual(result['status'], 'pass')

    def test_a_label_inside_a_shell_command_is_not_a_runner(self):
        # shakenfist/actions uploads an artifact named ubuntu-2004 from
        # inside an ssh command. Reporting it asked for an audit-ok
        # marker on a line which never described a runner.
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - run: |\n'
            '          ssh host "${setup} ubuntu-2004 /srv/ci/ubuntu:20.04"\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_name_ending_in_a_label_is_not_a_runner(self):
        result = self._check(
            'jobs:\n  a:\n    steps:\n'
            '      - uses: actions/upload-artifact@v7\n'
            '        with:\n          name: build-ubuntu-latest\n')
        self.assertEqual(result['status'], 'pass')

    def test_a_trailing_comment_does_not_hide_a_real_runner(self):
        result = self._check(
            'jobs:\n  a:\n    runs-on: ubuntu-latest  # why not\n')
        self.assertEqual(result['status'], 'fail')

    def test_no_workflows_directory_is_not_applicable(self):
        result = audit_check.check_self_hosted_runners(
            '/nonexistent', {'has_workflows_dir': False}
        )
        self.assertEqual(result['status'], 'not_applicable')


class RetiredCommentAddresserTest(unittest.TestCase):
    """The comment addresser is retired and must not still be deployed.

    It was never used -- review items are worked through interactively
    instead -- and what it leaves behind is a workflow triggered by
    issue_comment holding contents: write on the pull request branch.
    The scripts go with it: address-comments-with-claude.sh was its only
    entry point, and render-review.py plus review-schema.json were only
    ever there for that script to call.
    """

    def _repo(self, tmp, leftovers=()):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        for wf in ('pr-re-review.yml', 'pr-retest.yml'):
            with open(os.path.join(workflows, wf), 'w') as f:
                f.write('uses: shakenfist/actions/pr-bot-trigger@main\n'
                        'uses: shakenfist/actions/'
                        'review-pr-with-claude@main\n')
        for path in leftovers:
            full = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as f:
                f.write('x\n')
        return tmp

    def _check(self, leftovers=(), docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, leftovers)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_repository_without_the_addresser_passes(self):
        result = self._check()
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_workflow_alone_fails(self):
        result = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_scripts_alone_fail(self):
        # Deleting the trigger but keeping the scripts is a half-done
        # job, and the scripts are what the next person copies.
        result = self._check(['tools/address-comments-with-claude.sh'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('address-comments-with-claude.sh', result['details'])

    def test_render_review_and_its_schema_are_reaped_too(self):
        # Nothing else in a project calls render-review.py: the reviewer
        # uses the copy inside shakenfist/actions.
        result = self._check(
            ['tools/render-review.py', 'tools/review-schema.json']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])
        self.assertIn('review-schema.json', result['details'])

    def test_the_whole_chain_is_one_finding(self):
        chain = [audit_check.RETIRED_ADDRESSER_WORKFLOW] + [
            'tools/%s' % name
            for name in audit_check.RETIRED_ADDRESSER_SCRIPTS
        ]
        result = self._check(chain)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['details'].count('still deployed'), 1)
        for path in chain:
            self.assertIn(os.path.basename(path), result['details'])

    def test_scripts_outside_tools_are_found_too(self):
        # tools/ is the canonical home, but deployments put them
        # elsewhere; the check this replaced found a contrib/ copy.
        result = self._check(['contrib/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('contrib/render-review.py', result['details'])

    def test_the_git_directory_is_not_walked(self):
        # .git can hold checked-out state from another branch. Findings
        # from in there are not actionable.
        result = self._check(['.git/stash/render-review.py'])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_docs_only_project_is_checked_too(self):
        # cloudgood is exempt from most of this audit, but a workflow
        # holding contents: write is not a documentation concern.
        result = self._check(
            ['.github/workflows/pr-address-comments.yml'], docs_only=True
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_reviewer_actions_own_copies_are_not_leftovers(self):
        # shakenfist/actions is in the matrix and is where
        # render-review.py and its schema actually live -- the copies
        # every project's reviewer runs, and the ones this retirement
        # sends projects to instead of their own. The finding says to
        # remove the whole chain in one commit, so reporting these would
        # be telling the maintainer to delete the renderer out from
        # under the reviewer in every repository at once.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            'review-pr-with-claude/review-schema.json',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_exemption_does_not_cover_the_rest_of_the_repository(self):
        # shakenfist/actions carries genuine leftovers of its own next
        # to the action. Exempting the action's directory must not
        # exempt the repository, or the one repository that hosts the
        # replacement is the one that never gets told to clean up.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            '.github/workflows/pr-address-comments.yml',
            'tools/address-comments-with-claude.sh',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])
        self.assertIn('address-comments-with-claude.sh', result['details'])
        self.assertNotIn('review-pr-with-claude', result['details'])

    def test_any_composite_action_is_exempt_not_just_the_reviewer(self):
        # The exemption keys on action.yml rather than on the reviewer's
        # directory name, so a second action which vendors a renderer of
        # its own does not have to be added here to avoid a false
        # finding. Hardcoding the one name we know about today is how a
        # check acquires a maintenance burden nobody remembers.
        result = self._check([
            'some-other-action/action.yml',
            'some-other-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_yaml_spelling_of_the_manifest_counts(self):
        # Actions accepts action.yaml as readily as action.yml. Missing
        # the spelling produces the exact false finding the exemption
        # exists to prevent, and the finding says to delete everything
        # it names.
        result = self._check([
            'vendored-action/action.yaml',
            'vendored-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_template_copy_of_the_workflow_is_named(self):
        # The workflow is matched by name anywhere, not only at
        # .github/workflows/. A template directory's copy does not run,
        # but it is the one the next project installs, and the
        # remediation is "remove everything the finding names in one
        # commit" -- so a finding which skipped it would have the
        # maintainer delete the scripts, leave the template, and pass
        # the audit from then on while still propagating the chain.
        result = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'templates/ci-review-automation/pr-address-comments.yml',
            result['details'])

    def test_only_the_installed_workflow_claims_contents_write(self):
        # The finding is the whole content of an auto-filed issue on
        # another repository. Only .github/workflows/ actually runs, so
        # asserting a privileged workflow for a template copy sends the
        # maintainer hunting for one that is not there.
        installed = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertIn('contents: write', installed['details'])
        template = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertNotIn('contents: write', template['details'])
        self.assertIn('dead weight', template['details'])

    def test_leftover_scripts_alone_do_not_claim_contents_write(self):
        # The normal state after a partial cleanup: the workflow is
        # gone, the scripts are not.
        result = self._check(['tools/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertNotIn('contents: write', result['details'])

    def test_the_schema_alone_is_found(self):
        # review-schema.json is only ever exercised beside
        # render-review.py elsewhere in this suite, so a regression
        # which matched only the .py suffix would pass. It is dead on
        # its own too: nothing else in a project reads it.
        result = self._check(['tools/review-schema.json'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('review-schema.json', result['details'])

    def test_a_docs_only_project_is_checked_for_scripts_too(self):
        # The docs-only branch returns early on the addresser finding.
        # The workflow leftover pins that branch elsewhere; a script
        # leftover takes the same return and had nothing holding it.
        result = self._check(['tools/render-review.py'], docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])

    def test_the_exemption_is_the_directory_not_the_name(self):
        # An action.yml exempts the directory it sits in and nothing
        # below it, so a leftover parked one level down is still found.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/old/render-review.py',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'review-pr-with-claude/old/render-review.py', result['details'])


class PrReReviewTriggerTest(unittest.TestCase):
    """pr-re-review.yml must use pr-bot-trigger, not hand-rolled shell.

    The shared action refuses fork pull requests. Its pr-ref output is
    .head.ref -- a branch name in the head repository, with nothing to
    say which repository that is -- and callers check that name out and
    push to it in their own. A fork pull request opened from the fork's
    default branch names "main". A hand-rolled copy of the trigger
    handling does not get that guard, and did not get any of the other
    fixes made to the action either.
    """

    INLINE = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  check_and_review:\n'
        '    runs-on: [self-hosted, claude-code]\n'
        '    steps:\n'
        '      - name: Check commenter permissions\n'
        '        run: gh api repos/x/collaborators/y/permission\n'
    )
    USES_ACTION = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  trigger-re-review:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - uses: shakenfist/actions/pr-bot-trigger@main\n'
    )

    def _repo(self, tmp, re_review_body=None):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        if re_review_body is not None:
            with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
                f.write(re_review_body)
        return tmp

    def _check(self, re_review_body=None, docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, re_review_body)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_using_the_shared_action_passes(self):
        result = self._check(self.USES_ACTION)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_hand_rolled_trigger_handling_fails(self):
        result = self._check(self.INLINE)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])
        self.assertIn('fork', result['details'])

    def test_an_absent_workflow_is_reported_once_not_twice(self):
        # Its absence is already a finding. Saying "missing" and "does
        # not use the action" about the same missing file is two
        # findings for one problem.
        result = self._check(None)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Missing pr-re-review.yml', result['details'])
        self.assertNotIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INLINE, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_passes_when_the_action_is_used(self):
        result = self._check(self.USES_ACTION, docs_only=True)
        self.assertEqual(result['status'], 'pass', result['details'])


class GitHooksDisabledTest(unittest.TestCase):
    """The workflows that check out PR code must neuter core.hooksPath.

    Layer 4 of the security model in docs/ci-review-automation.md
    names these three files and asserts the control is set in them.
    Nothing in check_ci_review_automation inspects checkout steps, so
    without this a template edit could drop the step and leave the
    document claiming a control that is not there -- which is the
    defect this test's own pull request existed to fix. The assertion
    is on this repository's files rather than on a synthetic tree
    because the templates are the fleet's source of truth: a repo that
    copies them inherits whatever is here.
    """

    WORKFLOWS = [
        os.path.join('.github', 'workflows', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'ci-review-automation', 'pr-re-review.yml'),
        os.path.join(
            'templates', 'test-drift-fix', 'test-drift-fix.yml'),
    ]

    # Matched as a pattern rather than as one exact spelling. This
    # test is the fleet's guard, and its failures are read by people
    # who did not write it: `git config --local core.hooksPath` sets
    # the same thing, and reporting it as a missing line would send
    # them to delete a correct one.
    HOOKS_PATH = re.compile(r'git config (--local )?core\.hooksPath')

    def test_hooks_path_is_set_after_the_checkout(self):
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    lines = f.read().splitlines()

                config = [
                    i for i, line in enumerate(lines)
                    if self.HOOKS_PATH.search(line)
                    and not line.lstrip().startswith('#')
                ]
                self.assertEqual(
                    len(config), 1,
                    f'{name} must set core.hooksPath exactly once')

                # Ordering matters as much as presence: "git config"
                # outside a work tree fails, and hooks set before the
                # checkout would be overwritten by it. Against the
                # last checkout rather than the first, because a
                # second one added after the config step would
                # re-clone the tree and discard .git/config.
                checkout = [
                    i for i, line in enumerate(lines)
                    if 'actions/checkout@' in line
                ]
                self.assertTrue(
                    checkout, f'{name} has no checkout step')
                self.assertGreater(
                    config[0], checkout[-1],
                    f'{name} sets core.hooksPath before its last '
                    'checkout, which would discard the setting')

    def test_the_setting_is_repository_local(self):
        # --global would outlive the job on the shared claude-code
        # pool and disable hooks for every later job on that machine.
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                with open(os.path.join(REPO_ROOT, name)) as f:
                    body = f.read()
                self.assertNotIn(
                    'git config --global core.hooksPath', body)

    def test_the_document_still_names_these_workflows(self):
        # The test and the claim have to move together: a workflow
        # dropped from the list here but left in the document is the
        # same unbacked claim in the other direction.
        #
        # Scoped to layer 4 rather than the whole document on purpose.
        # "test-drift-fix.yml" also appears under Workflow Templates,
        # so a document-wide assertIn would stay green after the name
        # was struck from the security model -- a guard that passes
        # for a reason unrelated to what it defends.
        layer = self._security_model_layer_four()
        self.assertIn('core.hooksPath=/dev/null', layer)
        for name in self.WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertIn(os.path.basename(name), layer)

    # AGENTS.md: a document parsed by phrase gets named constants
    # and an assertion, not a bare index() that raises ValueError
    # without naming the phrase that stopped matching. Same treatment
    # as AuditScopeIsStatedOnceTest.bulleted_block(), including the
    # count assertions -- they report the phrase rather than dumping
    # the document the way assertIn would.
    DOC = os.path.join('docs', 'ci-review-automation.md')
    LAYER_FOUR = '4. **Git hooks disabled**'
    LAYER_FIVE = '5. **'

    def _security_model_layer_four(self):
        with open(os.path.join(REPO_ROOT, self.DOC)) as f:
            doc = f.read()
        self.assertEqual(
            doc.count(self.LAYER_FOUR), 1,
            f'{self.DOC} must contain "{self.LAYER_FOUR}" exactly '
            f'once: it is where this test starts reading the layer, '
            f'and a renumbered or reworded security model has to fail '
            f'as that rather than as a missing control')
        after = doc.split(self.LAYER_FOUR, 1)[1]
        self.assertEqual(
            after.count(self.LAYER_FIVE), 1,
            f'{self.DOC} must contain "{self.LAYER_FIVE}" exactly '
            f'once after "{self.LAYER_FOUR}": it is where this test '
            f'stops reading, and without it the parse runs to the end '
            f'of the file')
        return self.LAYER_FOUR + after.split(self.LAYER_FIVE, 1)[0]


class PrAutoReviewSecretsInheritTest(unittest.TestCase):
    """The reviewer job must not pass "secrets: inherit".

    pr-auto-review.yml reads no secrets -- it and review-pr-with-claude
    authenticate with github.token from the caller's permissions block
    -- so inheriting buys nothing and hands every secret the calling
    repository holds, publishing tokens included, to a workflow in
    another repository.
    """

    REVIEWER = (
        '  automated_reviewer:\n'
        '    permissions:\n'
        '      contents: read\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'pr-auto-review.yml@main\n'
    )
    INHERITS = REVIEWER + '    secrets: inherit\n'
    # smoke-cluster.yml genuinely needs the cluster secrets. Only the
    # reviewer job is the finding.
    SMOKE_INHERITS = (
        '  smoke:\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'smoke-cluster.yml@main\n'
        '    secrets: inherit\n'
    )

    def _repo(self, tmp, reviewer_job):
        # A compliant repository apart from whatever the reviewer job
        # under test does: both required workflows present, the shared
        # trigger action used, and none of the retired addresser's
        # files deployed. Anything else here shows up as an unrelated
        # finding and masks the one being tested.
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
            f.write('  - uses: shakenfist/actions/pr-bot-trigger@main\n')
        with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
            f.write('jobs:\n' + reviewer_job)
        return tmp

    def _check(self, reviewer_job, docs_only=False, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, reviewer_job)
            for name, content in (extra or {}).items():
                path = os.path.join(tmp, '.github', 'workflows', name)
                with open(path, 'w') as f:
                    f.write(content)
            return audit_check.check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_reviewer_without_inherit_passes(self):
        result = self._check(self.REVIEWER)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_reviewer_which_inherits_fails(self):
        result = self._check(self.INHERITS)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])
        self.assertIn('ci.yml', result['details'])

    def test_other_callers_may_inherit(self):
        # smoke-cluster.yml reads real secrets. Sweeping it up in this
        # finding would be telling projects to break their own CI.
        result = self._check(self.REVIEWER + self.SMOKE_INHERITS)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_commented_out_inherit_is_not_a_finding(self):
        commented = self.REVIEWER + '    # secrets: inherit\n'
        result = self._check(commented)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_trailing_comment_does_not_hide_it(self):
        # The realistic evasion. Someone who reads the template text or
        # receives the audit issue is likelier to annotate the line than
        # to delete it, and Actions treats this as plain inherit.
        annotated = self.REVIEWER + (
            '    secrets: inherit  # TODO: drop once migrated\n')
        result = self._check(annotated)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])

    def test_a_quoted_inherit_does_not_hide_it(self):
        for quoted in ("    secrets: 'inherit'\n",
                       '    secrets: "inherit"\n'):
            result = self._check(self.REVIEWER + quoted)
            self.assertEqual(result['status'], 'fail', quoted)
            self.assertIn('ci.yml', result['details'])

    def test_a_named_secret_is_not_inherit(self):
        # The explicit mapping form passes only what it names, which is
        # the false positive worth declining.
        named = self.REVIEWER + (
            '    secrets:\n      MY_TOKEN: ${{ secrets.MY_TOKEN }}\n')
        result = self._check(named)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INHERITS, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])

    def test_every_offending_workflow_is_named(self):
        # Most projects carry the reviewer job in functional-tests.yml
        # rather than ci.yml, so the finding has to name whichever file
        # it found rather than the one the fixtures happen to use. Two
        # at once also exercises the sorted join, which is what the
        # audit issue body shows the person doing the work.
        result = self._check(self.INHERITS, extra={
            'functional-tests.yml': 'jobs:\n' + self.INHERITS,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('functional-tests.yml', result['details'])
        self.assertLess(result['details'].index('ci.yml'),
                        result['details'].index('functional-tests.yml'))

    def test_a_workflow_with_no_jobs_key_is_skipped(self):
        # workflow_job_blocks finds nothing in a file with no top-level
        # jobs: key. That must skip the file rather than throw, or one
        # malformed workflow stops the check measuring the rest of the
        # repository -- and a check which does not run reports pass.
        result = self._check(self.INHERITS, extra={
            'dependabot-notes.yml': 'on:\n  push:\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertNotIn('dependabot-notes.yml', result['details'])


class MergeGroupCancellationTest(unittest.TestCase):
    """Which merge group jobs must be able to cancel each other."""

    QUEUE_REF_KEY = """    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-cluster
      cancel-in-progress: true
"""

    STABLE_KEY = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""

    def _job(self, concurrency='', runs_on='[self-hosted, vm, debian-12, l]',
             condition=''):
        return (
            '  cluster:\n'
            f'    runs-on: {runs_on}\n'
            + (f'    if: {condition}\n' if condition else '')
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    # Whether the repository's merge queue builds one entry at a
    # time. The check asks GitHub; these tests answer for it, both to
    # stay offline and because the interesting case -- a queue that
    # stacks speculatively, where the base_ref key would cancel a live
    # entry -- does not exist in the fleet to point at.
    serial_queue = True

    def setUp(self):
        self._real_serial = audit_check.merge_queue_is_serial
        audit_check.merge_queue_is_serial = (
            lambda repo_name, org, github=None: self.serial_queue
        )

    def tearDown(self):
        audit_check.merge_queue_is_serial = self._real_serial

    def _check(self, workflows):
        with tempfile.TemporaryDirectory() as tmp:
            wdir = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(wdir)
            for name, content in workflows.items():
                with open(os.path.join(wdir, name), 'w') as f:
                    f.write(content)
            return audit_check.check_merge_group_cancellation(
                tmp, {'has_workflows_dir': True}, 'testrepo', 'shakenfist'
            )

    def _merge_group_workflow(self, job):
        return 'on:\n  pull_request:\n  merge_group:\njobs:\n' + job

    def test_a_queue_ref_key_fails(self):
        # The bug this audit exists for: on merge_group github.ref is
        # gh-readonly-queue/<base>/pr-N-<SHA>, unique per rebuild, so
        # cancel-in-progress never matches.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def test_a_merge_group_aware_key_passes(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_no_concurrency_block_at_all_fails(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job())})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no concurrency block', result['details'])

    def test_cancel_in_progress_must_be_on(self):
        # A stable key that queues instead of cancelling still leaves
        # the superseded run holding the runner.
        block = """    concurrency:
      group: ${{ github.workflow }}-merge
      cancel-in-progress: false
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('cancel-in-progress is not true', result['details'])

    def test_a_workflow_level_block_covers_a_bare_job(self):
        content = (
            'on:\n  merge_group:\n'
            'concurrency:\n'
            "  group: ${{ github.workflow }}-${{ github.event_name =="
            " 'merge_group' && 'queue' || github.ref }}\n"
            '  cancel-in-progress: true\n'
            'jobs:\n' + self._job()
        )
        result = self._check({'ci.yml': content})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_job_that_cannot_run_on_merge_group_is_out_of_scope(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      condition="github.event_name != 'merge_group'"))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_static_pool_is_out_of_scope(self):
        # Gate jobs and path filters are seconds long on an
        # always-on shared pool; there is nothing to starve.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, static]'))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_self_hosted_pool_without_the_vm_label_is_in_scope(self):
        # instar's ephemeral runners are [self-hosted, debian-12, xl].
        # The sibling path-filter audit's 'vm' test would miss them
        # while an abandoned merge group holds one for two hours.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, debian-12, xl]'))})
        self.assertEqual(result['status'], 'fail')

    def test_a_github_hosted_runner_is_out_of_scope(self):
        # No fleet runner to starve, so the workflow is examined and
        # reports nothing rather than being skipped entirely.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY, runs_on='ubuntu-latest'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_an_unresolvable_runs_on_expression_is_out_of_scope(self):
        # ryll's cross-platform build matrix is runs-on:
        # ${{ matrix.os }}; there is nothing to resolve it against.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='${{ matrix.os }}'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_a_reusable_workflow_is_audited(self):
        # It inherits the caller's event, and a callee published for
        # the fleet cannot know what that event is. This is
        # shakenfist/actions' smoke-cluster.yml.
        result = self._check({'smoke-cluster.yml': (
            'on:\n  workflow_call:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'fail')

    def test_a_reusable_workflow_is_audited_despite_an_in_repo_caller(self):
        # Inferring reachability from in-repo callers exempted
        # smoke-cluster.yml on the strength of a scheduled canary
        # calling it, while every shakenfist merge group ran four
        # nested clusters through it from another repository.
        result = self._check({
            'smoke-cluster.yml': (
                'on:\n  workflow_call:\njobs:\n'
                + self._job(self.QUEUE_REF_KEY)
            ),
            'canary.yml': (
                'on:\n  schedule:\n    - cron: "0 3 * * *"\njobs:\n'
                '  canary:\n'
                '    uses: ./.github/workflows/smoke-cluster.yml\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('smoke-cluster.yml:cluster', result['details'])

    def test_calling_a_reusable_workflow_is_out_of_scope(self):
        # The caller job has no runner of its own; the group that
        # matters is in the callee, audited where it is defined.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_marked_exception_is_allowed(self):
        result = self._check({'test-drift-fix.yml': (
            'on:\n  workflow_call:\n'
            '# audit-ok: merge-group-cancellation -- issue_comment only\n'
            'jobs:\n' + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_comment_quoting_the_bad_key_does_not_count(self):
        # Every fixed workflow explains itself with a comment naming
        # github.ref directly above the corrected key.
        block = """    # github.ref is wrong here on merge_group.
    concurrency:
      group: ${{ github.workflow }}-merge-queue
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_github_sha_key_fails(self):
        # The same defect wearing a different name: on merge_group
        # github.sha is the per-attempt merge commit, not the pull
        # request head, so it is minted afresh on every rebuild.
        block = """    concurrency:
      group: ${{ github.workflow }}-${{ github.sha }}-cluster
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def _matrix_job(self, concurrency, key='${{ matrix.topology }}'):
        return (
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    def test_matrix_lanes_sharing_one_group_fails(self):
        # The expensive half of getting this wrong: the lanes cancel
        # each other inside a single run, the queue sees a cancelled
        # required check, and the pull request is ejected.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('lanes cancel each other', result['details'])

    def test_a_matrix_key_in_the_group_passes(self):
        block = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{ matrix.topology }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_matrix_of_self_hosted_runners_is_in_scope(self):
        # runs-on: ${{ matrix.runner }} is only unresolvable in the
        # sense that a regex cannot read it. The matrix says what it
        # resolves to, and a whole matrix of cloud builds should not
        # drop out of the audit because of the indirection.
        job = (
            '  cluster:\n'
            '    strategy:\n'
            '      matrix:\n'
            '        runner: [[self-hosted, vm, debian-12, l],\n'
            '                 [self-hosted, vm, debian-12, xl]]\n'
            '    runs-on: ${{ matrix.runner }}\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: deploy.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(job)})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    REUSABLE_HEAD = (
        'on:\n'
        '  workflow_call:\n'
        '    inputs:\n'
        '      concurrency_key:\n'
        '        type: string\n'
        '        default: \'\'\n'
        'jobs:\n'
    )

    def test_a_callee_group_made_only_of_caller_contexts_fails(self):
        # Every invocation on one ref renders the same group, so a
        # matrix of four callers cancels itself down to one.
        block = """    concurrency:
      group: >-
        smoke-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('every invocation on a ref', result['details'])

    def test_a_callee_keyed_on_an_input_passes(self):
        block = """    concurrency:
      group: >-
        smoke-cluster-${{ inputs.concurrency_key }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def _caller(self, name, extra_with='', matrix=''):
        return (
            f'  {name}:\n'
            + matrix
            + '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
            '    with:\n'
            '      component: shakenfist\n'
            + extra_with
        )

    def test_two_invocations_of_one_callee_need_distinct_keys(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier')
            + self._caller('ansible_modules')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('more than once per ref', result['details'])

    def test_distinct_concurrency_keys_pass(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier', '      concurrency_key: merge-tier\n')
            + self._caller(
                'ansible_modules',
                '      concurrency_key: ansible-modules\n')
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_same_concurrency_key_twice_fails(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier', '      concurrency_key: full\n')
            + self._caller(
                'ansible_modules', '      concurrency_key: full\n')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('passes the same concurrency_key', result['details'])

    def test_a_matrix_caller_must_vary_its_key(self):
        # shakenfist runs four nested clusters through one callee from
        # a single matrix job. Varying topology and base image is not
        # enough: the callee keys its group on concurrency_key, and
        # what does not vary there does not separate the lanes.
        matrix = (
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
        )
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      topology: ${{ matrix.topology }}\n'
                '      concurrency_key: full\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('same for every matrix lane', result['details'])

        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      concurrency_key: ${{ matrix.topology }}\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_callee_outside_the_fleet_is_reported(self):
        # Nothing here can see its concurrency group, and the caller
        # cannot fix it either.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    uses: someone-else/ci/.github/workflows/build.yml@v1\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('outside the audited fleet', result['details'])

    def test_a_marked_exception_only_exempts_its_own_job(self):
        # The marker used to be read against the whole file, so one
        # job's stated exception silently stopped the other fourteen
        # in an eight hundred line workflow being measured.
        exempt = (
            '  drift:\n'
            '    # audit-ok: merge-group-cancellation -- comment only\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: drift.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(
            exempt + self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml:cluster', result['details'])
        self.assertNotIn('ci.yml:drift', result['details'])

    def test_a_stacking_merge_queue_makes_the_base_ref_key_unsafe(self):
        # The pattern this audit requires aliases every live entry in
        # the queue. That is only safe while the queue builds one at a
        # time, which merge-queue-config is what enforces -- so the
        # precondition is checked rather than left as a note.
        self.serial_queue = False
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('aliases live entries', result['details'])

    def test_a_repo_with_no_merge_group_is_not_applicable(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
