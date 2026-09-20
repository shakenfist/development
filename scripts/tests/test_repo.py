#!/usr/bin/env python3

"""What `Repo` and the shared test machinery do with a checkout.

`Repo.read` is the one place every check reaches the filesystem, and
the paths it is handed are derived from the audited repository's own
files -- a workflow names a script, a script names its helper. So the
repository decides what is opened, and the two shapes that cost more
than a wrong answer are covered here: a path that is not a regular
file, which raises rather than returning None, and one that resolves
outside the checkout, which reads a file the audit was never pointed
at.

The helpers `tests/base.py` offers the check suites are covered here
too. They are not a check and have nowhere else to live, and this is
already the only suite whose subject is the machinery rather than a
criterion -- it builds a `FixtureRepo` to exercise `Repo.read`. The
ones tested below exist for the migration onto `CheckTestCase`, so
they acquire their callers a file at a time and would otherwise go
several commits with nothing asserting them.

Run with: python3 -m unittest tests.test_repo
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import Check  # noqa: E402
from audit.github import FakeGitHub  # noqa: E402
from audit.repo import Repo  # noqa: E402
from tests.base import (  # noqa: E402
    CheckTestCase, FixtureRepo, repo_file, repo_text,
)


class RepoReadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = FixtureRepo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # Somewhere for the tests that need a path the checkout does
        # not contain. A directory of its own rather than a name beside
        # the checkout: two suites running at once -- the pre-commit
        # hook while CI runs, or unittest with -j -- would otherwise
        # race over the same file in the shared temporary directory.
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)
        self.repo = Repo(self._tmp.name, 'testrepo', 'shakenfist',
                         github=FakeGitHub())

    def test_a_file_is_read(self):
        self.fixture.write('tools/ci/helper.sh', '#!/bin/bash\necho hi\n')
        self.assertEqual('#!/bin/bash\necho hi\n',
                         self.repo.read('tools/ci/helper.sh'))

    def test_an_absent_file_reads_as_none(self):
        self.assertIsNone(self.repo.read('tools/ci/helper.sh'))

    def test_a_directory_named_like_a_script_reads_as_none(self):
        """It exists, and opening it raises IsADirectoryError.

        A check that raises reports nothing about any of the other
        criteria for that repository, so a directory called helper.sh
        would cost the whole audit of a repository rather than one
        answer. Rare on its own, but the paths reaching read are
        speculative -- a name is tried against more than one
        directory -- so a repository need not have done anything odd
        for one of them to land on a directory.
        """
        os.makedirs(os.path.join(self._tmp.name, 'tools', 'ci', 'helper.sh'))
        self.assertIsNone(self.repo.read('tools/ci/helper.sh'))

    def test_a_symlink_inside_the_checkout_is_read_through(self):
        """Containment is about where the path lands, not how."""
        self.fixture.write('tools/ci/real.sh', 'gh issue create\n')
        os.symlink(os.path.join(self._tmp.name, 'tools', 'ci', 'real.sh'),
                   os.path.join(self._tmp.name, 'tools', 'ci', 'link.sh'))
        self.assertEqual('gh issue create\n',
                         self.repo.read('tools/ci/link.sh'))

    def test_a_symlink_out_of_the_checkout_reads_as_none(self):
        """A committed symlink is a path the repository chose.

        The repo-relative path is inside the checkout, so the guards
        the checks apply to the names they derive cannot see this one.
        exists() and open() both follow the link, so without a
        containment check here an audited repository could decide what
        the audit reads.
        """
        outside = os.path.join(self._outside.name, 'outside.txt')
        with open(outside, 'w') as f:
            f.write('not ours\n')
        os.makedirs(os.path.join(self._tmp.name, 'tools', 'ci'))
        os.symlink(outside,
                   os.path.join(self._tmp.name, 'tools', 'ci', 'leak.sh'))
        self.assertIsNone(self.repo.read('tools/ci/leak.sh'))

    def test_a_checkout_reached_through_a_symlink_is_still_readable(self):
        """Resolving one side only would refuse every file.

        Sibling clones are often reached through a symlinked parent,
        and comparing a resolved file against an unresolved root then
        matches nothing.
        """
        self.fixture.write('tools/ci/helper.sh', 'gh issue create\n')
        link = os.path.join(self._outside.name, 'checkout-link')
        os.symlink(self._tmp.name, link)
        through = Repo(link, 'testrepo', 'shakenfist', github=FakeGitHub())
        self.assertEqual('gh issue create\n',
                         through.read('tools/ci/helper.sh'))


class ConstructedCheck(Check):
    """A stand-in for the checks whose behaviour is set at construction.

    PushAudit, PlanTemplate and SfuiVendor all take a constructor
    argument, and their suites are the reason `check()` accepts
    check_args. A fixture check rather than one of those three: this is
    a test of the machinery, and borrowing a real criterion would tie
    it to whatever that criterion measures this month.
    """

    id = 'constructed-fixture'

    def __init__(self, detail='default'):
        self.detail = detail

    def run(self, repo):
        return self.ok(f'{self.detail}/{repo.props.get("is_docs_only")}')


class FixtureRepoBulkWriteTest(CheckTestCase):
    """The bulk writers the migrated `_repo` helpers hand their fixtures.

    On CheckTestCase rather than unittest.TestCase because the fixture
    is what is under test here; no check is run, so check_class stays
    unset.
    """

    def test_write_all_creates_the_directories_a_path_needs(self):
        self.fixture.write_all({
            'docs/index.md': '# Docs\n',
            'README.md': '# Project\n',
        })
        self.assertEqual(
            '# Docs\n', self.repo().read('docs/index.md'))
        self.assertEqual('# Project\n', self.repo().read('README.md'))

    def test_write_all_returns_the_paths_it_wrote(self):
        written = self.fixture.write_all({'a.md': 'a\n', 'b/c.md': 'c\n'})
        self.assertEqual(
            [os.path.join(self.fixture.path, 'a.md'),
             os.path.join(self.fixture.path, 'b', 'c.md')],
            written)

    def test_write_all_skips_a_none_content(self):
        """None means the file is absent, not that it is empty.

        PushAuditTest's cases opt out of its default AGENTS.md that
        way, and the check they drive distinguishes a missing file
        from an empty one. The skipped path is left out of the return
        value too, so a caller counting what it wrote is not told
        about a file that is not there.
        """
        written = self.fixture.write_all({'a.md': 'a\n', 'AGENTS.md': None})
        self.assertEqual([os.path.join(self.fixture.path, 'a.md')], written)
        self.assertFalse(
            os.path.exists(os.path.join(self.fixture.path, 'AGENTS.md')))

    def test_write_all_writes_an_empty_string_as_an_empty_file(self):
        """A link target only has to exist; DocsExternalLinksTest's
        fixtures say so with '' rather than with the absence
        sentinel."""
        self.fixture.write_all({'docs/target.md': ''})
        self.assertEqual('', self.repo().read('docs/target.md'))

    def test_workflows_writes_under_the_workflows_directory(self):
        self.fixture.workflows({'ci.yml': 'on:\n  push:\n',
                                'lint.yml': 'on:\n  pull_request:\n'})
        self.assertEqual(
            'on:\n  push:\n',
            self.repo().read('.github/workflows/ci.yml'))
        self.assertEqual(
            'on:\n  pull_request:\n',
            self.repo().read('.github/workflows/lint.yml'))

    def test_an_empty_mapping_still_creates_the_directory(self):
        # The helpers this replaces call makedirs() before the loop, so
        # a repository with a workflows directory and no workflows is a
        # fixture they can build and a check can tell from one with no
        # directory at all.
        self.fixture.workflows({})
        self.assertTrue(os.path.isdir(os.path.join(
            self.fixture.path, '.github', 'workflows')))


class CheckArgumentsTest(CheckTestCase):
    """`check()` separates constructing the check from describing the repo."""

    check_class = ConstructedCheck

    def test_the_check_is_constructed_with_its_defaults(self):
        self.assertEqual('default/False',
                         self.assert_pass(self.check())['details'])

    def test_check_args_reach_the_constructor(self):
        result = self.check(check_args={'detail': 'supplied'})
        self.assertEqual('supplied/False',
                         self.assert_pass(result)['details'])

    def test_properties_still_go_to_the_repository(self):
        # The guard on the split: a property named beside check_args
        # must not be handed to the check's constructor, which would
        # raise, and check_args must not land in repo.props.
        result = self.check(check_args={'detail': 'supplied'},
                            is_docs_only=True)
        self.assertEqual('supplied/True',
                         self.assert_pass(result)['details'])


class TempdirTest(CheckTestCase):
    """The second throwaway directory, for a fixture outside the repo."""

    def test_each_call_is_a_fresh_directory_outside_the_fixture(self):
        first = self.tempdir()
        second = self.tempdir()
        self.assertTrue(os.path.isdir(first))
        self.assertTrue(os.path.isdir(second))
        self.assertNotEqual(first, second)
        self.assertFalse(first.startswith(self.fixture.path + os.sep))

    def test_it_is_cleaned_up_when_the_test_ends(self):
        # Cleanup is registered with addCleanup, so no assertion inside
        # the test that asked for the directory can observe it. Run a
        # throwaway case and look at what it left behind instead.
        asked = []

        class Case(CheckTestCase):
            def runTest(inner):  # noqa: N805
                asked.append(inner.tempdir())

        outcome = unittest.TestResult()
        Case('runTest').run(outcome)
        self.assertEqual([], outcome.errors + outcome.failures)
        self.assertFalse(os.path.exists(asked[0]))


class RepoTextTest(unittest.TestCase):
    """The decoded sibling of repo_file()."""

    def test_it_is_repo_file_decoded(self):
        self.assertEqual(repo_file('README.md').decode('utf-8'),
                         repo_text('README.md'))

    def test_it_joins_its_parts_below_the_repository_root(self):
        self.assertIn('eol-distro.md',
                      repo_text('docs', 'audits', 'README.md'))


if __name__ == '__main__':
    unittest.main()
