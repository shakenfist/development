#!/usr/bin/env python3

"""Tests for the issue body audit-manage-issues.py files.

The body is the one thing a fleet-wide consumer actually reads: it is
what somebody picking up a `consistency` issue in another repository
sees, and neither of its per-item lists had any coverage at all. The
script's hyphenated name makes it unimportable, so it is loaded the way
test_metadata.py loads audit-update-docs.py.

Run with: python3 scripts/tests/test_manage_issues.py
"""

import importlib.util
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import REPO_ROOT  # noqa: E402


def _manage_issues():
    """Load audit-manage-issues.py, whose hyphen makes it unimportable."""
    path = os.path.join(REPO_ROOT, 'scripts', 'audit-manage-issues.py')
    spec = importlib.util.spec_from_file_location('audit_manage_issues', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IssueBodyTest(unittest.TestCase):
    def setUp(self):
        self.build = _manage_issues().build_issue_body

    def result(self, **extra):
        built = {'id': 'eol-distro', 'status': 'fail',
                 'details': 'two references'}
        built.update(extra)
        return built

    def test_the_body_names_the_check_and_links_its_spec(self):
        body = self.build('eol-distro', self.result())
        self.assertIn('End-of-life distributions', body)
        self.assertIn('docs/audits/eol-distro.md', body)
        self.assertIn('two references', body)

    def test_a_result_with_neither_list_renders_neither_heading(self):
        body = self.build('eol-distro', self.result())
        self.assertNotIn('**Findings:**', body)
        self.assertNotIn('**Missing items:**', body)

    def test_findings_render_one_bullet_each(self):
        body = self.build('eol-distro', self.result(
            findings=['ci.yml:3 (debian-12)', 'Dockerfile:1 (debian:12)']))
        self.assertIn('**Findings:**', body)
        self.assertIn('- `ci.yml:3 (debian-12)`', body)
        self.assertIn('- `Dockerfile:1 (debian:12)`', body)

    def test_missing_renders_under_its_own_heading(self):
        """The pre-existing sibling, which also had no coverage."""
        body = self.build('review-scope-completeness', self.result(
            missing=['scripts/thing.py']))
        self.assertIn('**Missing items:**', body)
        self.assertIn('- `scripts/thing.py`', body)
        self.assertNotIn('**Findings:**', body)

    def test_the_two_lists_are_independent(self):
        """Nothing stops a check carrying both; they must not merge."""
        body = self.build('eol-distro', self.result(
            missing=['a.py'], findings=['b.yml:1 (debian-12)']))
        self.assertIn('**Missing items:**', body)
        self.assertIn('**Findings:**', body)
        self.assertIn('- `a.py`', body)
        self.assertIn('- `b.yml:1 (debian-12)`', body)
        self.assertLess(body.index('**Missing items:**'),
                        body.index('**Findings:**'))


class IssueBodyLimitTest(unittest.TestCase):
    """The lists are unbounded, and GitHub's issue body is not."""

    #: GitHub rejects a create with a body longer than this.
    GITHUB_LIMIT = 65536

    def setUp(self):
        self.module = _manage_issues()

    def body(self, count):
        return self.module.build_issue_body('eol-distro', {
            'id': 'eol-distro', 'status': 'fail',
            'details': 'many references',
            'findings': ['.github/workflows/ci.yml:%d (debian-12)' % n
                         for n in range(count)],
        })

    def test_a_realistic_list_is_rendered_whole(self):
        """The fleet's worst repository has twenty-one findings."""
        body = self.body(21)
        self.assertNotIn('omitted', body)
        self.assertIn('.github/workflows/ci.yml:20 (debian-12)', body)

    def test_a_pathological_list_still_files_an_issue(self):
        body = self.body(5000)
        self.assertLess(len(body), self.GITHUB_LIMIT)
        self.assertIn('more, omitted', body)

    def test_every_item_is_either_rendered_or_counted(self):
        """A short list that does not say so is the failure to avoid."""
        body = self.body(5000)
        rendered = body.count('(debian-12)')
        omitted = re.search(r'\.\.\.and (\d+) more', body)
        self.assertIsNotNone(omitted, body[-500:])
        self.assertEqual(5000, rendered + int(omitted.group(1)))


if __name__ == '__main__':
    unittest.main()
