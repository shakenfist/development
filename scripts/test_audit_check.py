#!/usr/bin/env python3

"""Tests for audit-check.py checks.

Run with: python3 scripts/test_audit_check.py
"""

import importlib.util
import os
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


if __name__ == '__main__':
    unittest.main()
