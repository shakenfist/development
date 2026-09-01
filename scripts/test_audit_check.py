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
