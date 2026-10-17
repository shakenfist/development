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
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import Check  # noqa: E402
from audit.github import FakeGitHub  # noqa: E402
from audit import scope  # noqa: E402
from audit.repo import READ_MAX_BYTES, Repo, detect_repo_properties  # noqa: E402
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

    def test_a_file_is_read_as_utf8_whatever_the_locale(self):
        """The runner's locale does not decide what a file says.

        A subprocess, because the locale encoding is fixed when the
        interpreter starts: the C locale with coercion and UTF-8 mode
        both off is the one where text mode would decode as ASCII.
        """
        self.fixture.write('pyproject.toml', 'name = "caf\u00e9"\n')
        reader = ('import sys; sys.path.insert(0, sys.argv[1]); '
                  'from audit.repo import Repo; '
                  'print(ascii(Repo(sys.argv[2], "t", props={})'
                  '.read("pyproject.toml")))')
        env = dict(os.environ, LC_ALL='C', PYTHONCOERCECLOCALE='0',
                   PYTHONUTF8='0')
        result = subprocess.run(
            [sys.executable, '-c', reader,
             os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
             self._tmp.name],
            capture_output=True, text=True, env=env, check=True)
        self.assertEqual(ascii('name = "caf\u00e9"\n'), result.stdout.strip())

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

    def outside_file(self, content='not ours\n'):
        path = os.path.join(self._outside.name, 'outside.txt')
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_a_dangling_symlink_reads_as_none(self):
        os.symlink(os.path.join(self._outside.name, 'gone'),
                   os.path.join(self._tmp.name, 'README.md'))
        self.assertIsNone(self.repo.read('README.md'))
        self.assertFalse(self.repo.isfile('README.md'))

    def test_a_relative_or_absolute_escape_reads_as_none(self):
        """The path a check derives from content is the repository's.

        A workflow naming `../x`, or a pyproject.toml entry point whose
        dotted name becomes `//etc/x.py`, is joined onto the checkout
        and lands outside it; os.path.join takes the second as
        absolute.
        """
        outside = self.outside_file()
        self.assertIsNone(self.repo.read(os.path.relpath(
            outside, self._tmp.name)))
        self.assertIsNone(self.repo.read(outside))

    def test_a_file_at_the_cap_is_read_and_one_past_it_is_not(self):
        """Declined rather than truncated: a prefix is a wrong verdict."""
        self.fixture.write('at.txt', 'x' * READ_MAX_BYTES)
        self.fixture.write('over.txt', 'x' * (READ_MAX_BYTES + 1))
        self.assertEqual(READ_MAX_BYTES, len(self.repo.read('at.txt')))
        self.assertIsNone(self.repo.read('over.txt'))

    def test_the_cap_counts_bytes_rather_than_characters(self):
        """A multi-byte character is more than one byte of the cap."""
        self.fixture.write('wide.txt', '\u00e9' * (READ_MAX_BYTES // 2 + 1))
        self.assertIsNone(self.repo.read('wide.txt'))

    def test_a_link_to_a_device_reads_as_none(self):
        """README.md -> /dev/zero is a read that never ends.

        A device is not a regular file, so isfile() refuses it before a
        byte is read, and it is outside the checkout in any case. The
        cap is the guard for the case neither catches.
        """
        if not os.path.exists('/dev/zero'):
            self.skipTest('no /dev/zero here')
        os.symlink('/dev/zero', os.path.join(self._tmp.name, 'README.md'))
        self.assertIsNone(self.repo.read('README.md'))

    def test_a_fifo_in_the_checkout_is_never_opened(self):
        """isfile() is checked before open(), not left to open() to fail.

        A dangling link and a directory both make open() raise, and a
        raise is caught, so either order would read them as None. A
        FIFO does not raise: opening one blocks until something writes
        to it, which in a scheduled audit is never. git cannot commit
        one, but a check should not need that to be true.
        """
        if not hasattr(os, 'mkfifo'):
            self.skipTest('no FIFOs here')
        os.mkfifo(os.path.join(self._tmp.name, 'README.md'))
        with mock.patch('builtins.open', side_effect=AssertionError(
                'opened a FIFO')):
            self.assertIsNone(self.repo.read('README.md'))

    def test_newlines_are_translated_as_text_mode_would(self):
        """The bytes are read for the cap and decoded like open() would."""
        with open(os.path.join(self._tmp.name, 'crlf.txt'), 'wb') as f:
            f.write(b'one\r\ntwo\rthree\n')
        self.assertEqual('one\ntwo\nthree\n', self.repo.read('crlf.txt'))

    def test_exists_does_not_follow_a_link_out_of_the_checkout(self):
        """Presence is a question about the clone too."""
        os.symlink(self._outside.name, os.path.join(self._tmp.name, '.claude'))
        self.assertFalse(self.repo.exists('.claude'))
        os.makedirs(os.path.join(self._tmp.name, 'docs'))
        self.assertTrue(self.repo.exists('docs'))

    def test_a_directory_named_like_a_file_is_not_a_file(self):
        os.makedirs(os.path.join(self._tmp.name, 'AGENTS.md'))
        self.assertTrue(self.repo.exists('AGENTS.md'))
        self.assertFalse(self.repo.isfile('AGENTS.md'))

    def test_workflows_lists_only_what_workflow_reads(self):
        directory = os.path.join(self._tmp.name, '.github', 'workflows')
        self.fixture.write('.github/workflows/ci.yml', 'on: push\n')
        os.symlink(os.path.join(self._outside.name, 'gone.yml'),
                   os.path.join(directory, 'dangling.yml'))
        os.symlink(self.outside_file(),
                   os.path.join(directory, 'leak.yml'))
        os.makedirs(os.path.join(directory, 'folder.yml'))
        self.assertEqual(['ci.yml'], self.repo.workflows())

    def test_a_workflows_directory_out_of_the_checkout_lists_nothing(self):
        """Its names would be another directory's names, quoted as ours."""
        elsewhere = os.path.join(self._outside.name, 'workflows')
        os.makedirs(elsewhere)
        with open(os.path.join(elsewhere, 'ci.yml'), 'w') as f:
            f.write('on: push\n')
        os.makedirs(os.path.join(self._tmp.name, '.github'))
        os.symlink(elsewhere,
                   os.path.join(self._tmp.name, '.github', 'workflows'))
        self.assertEqual([], self.repo.workflows())

    def test_properties_do_not_follow_a_link_out_of_the_checkout(self):
        """A property may not say a file is there that every read refuses."""
        os.symlink(self.outside_file('[project]\n'),
                   os.path.join(self._tmp.name, 'pyproject.toml'))
        self.assertFalse(
            detect_repo_properties(self._tmp.name, 'testrepo')[
                'has_pyproject_toml'])


class ScopeReadTest(unittest.TestCase):
    """audit.scope parses the checkout under audit through the same guard."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._outside = tempfile.TemporaryDirectory()
        self.addCleanup(self._outside.cleanup)

    def test_a_scope_document_linked_out_of_the_checkout_is_not_read(self):
        target = os.path.join(self._outside.name, 'README.md')
        with open(target, 'w') as f:
            f.write('not ours\n')
        os.makedirs(os.path.join(self._tmp.name, 'docs', 'audits'))
        os.symlink(target, os.path.join(
            self._tmp.name, 'docs', 'audits', 'README.md'))
        with self.assertRaises(OSError) as raised:
            scope.read(self._tmp.name, 'docs/audits/README.md')
        self.assertNotIn('not ours', str(raised.exception))

    def test_a_scope_document_is_read(self):
        os.makedirs(os.path.join(self._tmp.name, 'docs'))
        with open(os.path.join(self._tmp.name, 'docs', 'x.md'), 'w') as f:
            f.write('ours\n')
        self.assertEqual('ours\n', scope.read(self._tmp.name, 'docs/x.md'))


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

    def test_workflows_skips_a_none_content(self):
        """The absence sentinel survives the composition.

        workflows() delegates to write_all(), so None has to keep
        meaning "absent" across two helpers rather than one. A check
        that walks .github/workflows/ counts what is there, so a
        sentinel that leaked through as an empty file would be
        counted -- and the case meaning "this workflow is missing"
        would pass for the wrong reason.
        """
        written = self.fixture.workflows({'ci.yml': 'on:\n  push:\n',
                                          'absent.yml': None})
        workflows = os.path.join(self.fixture.path, '.github', 'workflows')
        self.assertEqual([os.path.join(workflows, 'ci.yml')], written)
        self.assertFalse(
            os.path.exists(os.path.join(workflows, 'absent.yml')))
        self.assertEqual(['ci.yml'], sorted(os.listdir(workflows)))

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


class FreshFixtureTest(CheckTestCase):
    """The rebuild a helper called twice in one method needs."""

    def test_it_replaces_the_fixture_with_an_empty_one(self):
        self.fixture.write('docs/index.md', '# Docs\n')
        first = self.fixture.path

        returned = self.fresh_fixture()

        self.assertIs(returned, self.fixture)
        self.assertNotEqual(first, self.fixture.path)
        self.assertEqual([], os.listdir(self.fixture.path))
        self.assertIsNone(self.repo().read('docs/index.md'))

    def test_the_repository_follows_the_new_fixture(self):
        # The failure this guards is silent rather than loud: if
        # self.repo() kept pointing at the old directory, a case
        # meaning "and now without that file" would still find it and
        # pass for the wrong reason.
        self.fresh_fixture()
        self.fixture.write('AGENTS.md', '# Agents\n')
        self.assertEqual('# Agents\n', self.repo().read('AGENTS.md'))

    def test_the_replaced_directory_is_still_cleaned_up(self):
        # Both directories are registered with addCleanup, so the
        # discarded one has to be observed from outside the test that
        # discarded it, the same way TempdirTest does.
        paths = []

        class Case(CheckTestCase):
            def runTest(inner):  # noqa: N805
                paths.append(inner.fixture.path)
                inner.fresh_fixture()
                paths.append(inner.fixture.path)

        outcome = unittest.TestResult()
        Case('runTest').run(outcome)
        self.assertEqual([], outcome.errors + outcome.failures)
        self.assertEqual(2, len(set(paths)))
        for path in paths:
            self.assertFalse(os.path.exists(path))


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
