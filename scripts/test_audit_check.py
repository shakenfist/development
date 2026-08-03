#!/usr/bin/env python3

"""Tests for audit-check.py checks.

Run with: python3 scripts/test_audit_check.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest


SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'audit-check.py'
)

# audit-check.py is not importable by name (the hyphen is not a valid
# module identifier), so load it from its path.
_spec = importlib.util.spec_from_file_location('audit_check', SCRIPT)
audit_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_check)


class CanonicalNameTest(unittest.TestCase):
    def test_collapses_separators_and_case(self):
        canonical = audit_check.canonical_dependency_name
        self.assertEqual(canonical('typing_extensions'), 'typing-extensions')
        self.assertEqual(canonical('typing-extensions'), 'typing-extensions')
        self.assertEqual(canonical('Zope.Interface'), 'zope-interface')
        self.assertEqual(canonical('prometheus__client'), 'prometheus-client')


class DependencyNameNormalizationTest(unittest.TestCase):
    def _check(self, pyproject_body):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write(pyproject_body)
            return audit_check.check_dependency_name_normalization(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = audit_check.check_dependency_name_normalization(
            '/nonexistent', {'has_pyproject_toml': False}
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_clean_dependencies_pass(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.16.0",\n'
            '    "requests==2.34.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_hyphen_underscore_duplicate_fails(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.15.0",\n'
            '    "typing_extensions==4.15.0",\n'
            ']\n'
        )
        result = self._check(body)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('typing-extensions', result['details'])

    def test_diverged_exact_versions_fail(self):
        body = (
            'dependencies = [\n'
            '    "typing-extensions==4.15.0",\n'
            '    "typing_extensions==4.16.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'fail')

    def test_floor_plus_exact_pin_passes(self):
        # A direct floor constraint plus the exact pin appended by the
        # indirect-dependency workflow is intentional.
        body = (
            'dependencies = [\n'
            '    "psutil>=5.9.4",\n'
            '    "psutil==7.2.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_base_plus_extras_same_version_passes(self):
        body = (
            'dependencies = [\n'
            '    "gunicorn[gevent]==25.3.0",\n'
            '    "gunicorn==25.3.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_base_plus_extras_conflicting_version_fails(self):
        body = (
            'dependencies = [\n'
            '    "gunicorn[gevent]==25.3.0",\n'
            '    "gunicorn==26.0.0",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'fail')

    def test_same_name_across_separate_arrays_passes(self):
        # A name pinned in the main array and in an optional group is
        # two separate install-time scopes, not a conflict.
        body = (
            '[project]\n'
            'dependencies = [\n'
            '    "requests==2.34.2",\n'
            ']\n'
            '[project.optional-dependencies]\n'
            'pinned = [\n'
            '    "requests==2.34.2",\n'
            ']\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')

    def test_non_dependency_quoted_strings_ignored(self):
        # URLs, script entry points and classifiers must not be parsed
        # as dependency pins.
        body = (
            '[project]\n'
            'dependencies = [\n'
            '    "typing-extensions==4.16.0",\n'
            ']\n'
            '[project.urls]\n'
            '"Homepage" = "https://shakenfist.com"\n'
            '[project.scripts]\n'
            'sf-ctl = "shakenfist.client.ctl:cli"\n'
        )
        self.assertEqual(self._check(body)['status'], 'pass')


class ReadmeStructureTest(unittest.TestCase):
    PITCH = (
        '# Project\n\nA short pitch.\n\n'
        '[docs](https://github.com/shakenfist/x/blob/develop/'
        'docs/index.md)\n'
    )

    def _check(self, readme=None, with_docs_dir=False):
        with tempfile.TemporaryDirectory() as tmp:
            if readme is not None:
                with open(os.path.join(tmp, 'README.md'), 'w') as f:
                    f.write(readme)
            if with_docs_dir:
                os.mkdir(os.path.join(tmp, 'docs'))
            return audit_check.check_readme_structure(tmp, {})

    def test_not_applicable_without_readme(self):
        self.assertEqual(self._check()['status'], 'not_applicable')

    def test_short_readme_with_docs_link_passes(self):
        result = self._check(self.PITCH, with_docs_dir=True)
        self.assertEqual(result['status'], 'pass')

    def test_too_many_lines_fails(self):
        readme = self.PITCH + ('filler\n' * 200)
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('lines', result['details'])

    def test_too_many_words_fails(self):
        # Few lines, but far over the word cap.
        readme = self.PITCH + (('word ' * 300) + '\n') * 5
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('words', result['details'])

    def test_missing_docs_link_fails_when_docs_exist(self):
        result = self._check(
            '# Project\n\nA short pitch.\n', with_docs_dir=True
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no link into docs/', result['details'])

    def test_docs_link_not_required_without_docs_dir(self):
        result = self._check('# Project\n\nA short pitch.\n')
        self.assertEqual(result['status'], 'pass')

    def test_docs_link_in_code_block_does_not_count(self):
        readme = (
            '# Project\n\nA short pitch.\n\n'
            '```\n[docs](docs/index.md)\n```\n'
        )
        result = self._check(readme, with_docs_dir=True)
        self.assertEqual(result['status'], 'fail')


class PushAuditTest(unittest.TestCase):
    def setUp(self):
        # A private canonical blocks directory so the tests do not
        # depend on the real templates/shared-blocks/ content.
        self._blocks = tempfile.TemporaryDirectory()
        self.addCleanup(self._blocks.cleanup)
        self.readme_block = (
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
            '<!-- shared-block-end -->\n'
        )
        # A different version number, so the tests prove versions are
        # tracked per block rather than globally.
        self.comment_block = (
            '<!-- shared-block: comment-proportion v3 -->\n'
            'Comment wording.\n'
            '<!-- shared-block-end -->\n'
        )
        for name, block in (
            ('readme-discipline', self.readme_block),
            ('comment-proportion', self.comment_block),
        ):
            with open(
                os.path.join(self._blocks.name, f'{name}.md'), 'w'
            ) as f:
                f.write(block)
        self.canonical = f'{self.readme_block}\n{self.comment_block}'

    def _check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            for name, content in files.items():
                with open(os.path.join(tmp, name), 'w') as f:
                    f.write(content)
            return audit_check.check_push_audit(
                tmp, {}, blocks_dir=self._blocks.name
            )

    def test_not_applicable_without_file(self):
        self.assertEqual(self._check({})['status'], 'not_applicable')

    def test_current_block_passes(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_legacy_filename_fails(self):
        result = self._check({
            'PUSH-TEMPLATE.md': f'# Audit\n\n{self.canonical}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])

    def test_missing_block_fails(self):
        result = self._check({'PUSH-AUDIT.md': '# Audit\n'})
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block readme-discipline',
            result['details'],
        )
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_missing_comment_proportion_fails(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.readme_block}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'missing shared block comment-proportion',
            result['details'],
        )

    def test_stale_version_fails(self):
        stale = self.canonical.replace('v2', 'v1')
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{stale}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('stale (v1 embedded, v2 current)',
                      result['details'])

    def test_drifted_content_fails(self):
        drifted = self.canonical.replace(
            'Canonical wording.', 'Mutated wording.'
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{drifted}\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('drifted', result['details'])

    def test_trailing_whitespace_is_ignored(self):
        padded = self.canonical.replace(
            'Canonical wording.', 'Canonical wording.   '
        )
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{padded}\n',
        })
        self.assertEqual(result['status'], 'pass')

    def test_unknown_block_fails(self):
        content = (
            f'# Audit\n\n{self.canonical}\n'
            '<!-- shared-block: no-such-block v1 -->\n'
            'Words.\n'
            '<!-- shared-block-end -->\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('unknown shared block no-such-block',
                      result['details'])

    def test_missing_end_marker_fails(self):
        content = (
            '# Audit\n\n'
            '<!-- shared-block: readme-discipline v2 -->\n'
            'Canonical wording.\n'
        )
        result = self._check({'PUSH-AUDIT.md': content})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no <!-- shared-block-end -->',
                      result['details'])

    def test_both_files_fails_even_with_current_block(self):
        result = self._check({
            'PUSH-AUDIT.md': f'# Audit\n\n{self.canonical}\n',
            'PUSH-TEMPLATE.md': '# Old\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('legacy filename', result['details'])


class PinIndirectDepsScopeTest(unittest.TestCase):
    """Tests that indirect pinning only applies to projects which pin.

    A project that exactly pins its own direct dependencies is declaring
    it controls its runtime environment, which is the condition under
    which pinning transitive versions is safe. A library that constrains
    loosely is deliberately leaving resolution to its consumers, and
    pinning on their behalf takes that away.
    """

    def _check(self, dependencies, files=None):
        with tempfile.TemporaryDirectory() as tmp:
            body = 'dependencies = [\n'
            for dependency in dependencies:
                body += f'    "{dependency}",\n'
            body += ']\n'
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write('[project]\nname = "example"\n' + body)
            for path in files or []:
                full = os.path.join(tmp, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, 'w') as f:
                    f.write('')
            return audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )

    def test_not_applicable_without_pyproject(self):
        result = audit_check.check_pin_indirect_deps(
            '/nonexistent', {'has_pyproject_toml': False}
        )
        self.assertEqual(result['status'], 'not_applicable')

    def test_library_with_loose_constraints_is_out_of_scope(self):
        result = self._check([
            'click>=8.0.0', 'distro', 'psutil>5.9.0', 'grpcio>=1.70.0',
        ])
        self.assertEqual(result['status'], 'not_applicable')
        self.assertIn('library', result['details'])

    def test_bare_name_is_not_a_pin(self):
        result = self._check(['python-debian'])
        self.assertEqual(result['status'], 'not_applicable')

    def test_application_with_exact_pins_is_in_scope(self):
        # In scope, and missing the tooling, so it fails rather than
        # dropping out as not applicable.
        result = self._check(['click==8.4.2', 'requests==2.34.2'])
        self.assertEqual(result['status'], 'fail')

    def test_extras_on_an_exact_pin_still_count(self):
        result = self._check(['gunicorn[gevent]==26.0.0'])
        self.assertEqual(result['status'], 'fail')

    def test_a_few_loose_pins_do_not_exempt_an_application(self):
        # shakenfist and kerbside each leave a couple of dependencies
        # loose on purpose; that must not read as a library.
        result = self._check([
            'psutil>=5.9.4', 'uv>=0.8.0', 'click==8.4.2',
            'requests==2.34.2', 'PyYAML==6.0.3',
        ])
        self.assertEqual(result['status'], 'fail')

    def test_in_scope_project_with_the_tooling_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, '.github', 'workflows'))
            os.makedirs(os.path.join(tmp, 'tools'))
            open(os.path.join(
                tmp, '.github', 'workflows',
                'pin-indirect-dependencies.yml'), 'w').close()
            open(os.path.join(
                tmp, 'tools', 'pin-indirect-dependencies.sh'), 'w').close()
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write(
                    '[project]\nname = "example"\ndependencies = [\n'
                    '    "click==8.4.2",\n'
                    '    # START_OF_INDIRECT_DEPS\n'
                    '    # END_OF_INDIRECT_DEPS\n'
                    ']\n'
                )
            result = audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'pass')

    def test_unparseable_pyproject_is_out_of_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'pyproject.toml'), 'w') as f:
                f.write('this is not = valid toml [\n')
            result = audit_check.check_pin_indirect_deps(
                tmp, {'has_pyproject_toml': True}
            )
        self.assertEqual(result['status'], 'not_applicable')


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


class CanonicalSharedBlocksTest(unittest.TestCase):
    def test_real_canonical_blocks_parse(self):
        # Every canonical file in templates/shared-blocks/ must
        # contain a block whose name matches the filename.
        blocks_dir = audit_check.SHARED_BLOCKS_DIR
        names = [
            f[:-3] for f in os.listdir(blocks_dir)
            if f.endswith('.md') and f != 'README.md'
        ]
        self.assertIn('readme-discipline', names)
        self.assertIn('comment-proportion', names)
        for name in names:
            canonical = audit_check.load_canonical_block(name)
            self.assertIsNotNone(
                canonical,
                f'templates/shared-blocks/{name}.md has no '
                f'shared-block marker matching its filename',
            )


if __name__ == '__main__':
    unittest.main()
