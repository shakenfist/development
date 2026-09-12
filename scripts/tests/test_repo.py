#!/usr/bin/env python3

"""What `Repo` will and will not read from a repository under audit.

`Repo.read` is the one place every check reaches the filesystem, and
the paths it is handed are derived from the audited repository's own
files -- a workflow names a script, a script names its helper. So the
repository decides what is opened, and the two shapes that cost more
than a wrong answer are covered here: a path that is not a regular
file, which raises rather than returning None, and one that resolves
outside the checkout, which reads a file the audit was never pointed
at.

Run with: python3 -m unittest tests.test_repo
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.github import FakeGitHub  # noqa: E402
from audit.repo import Repo  # noqa: E402
from tests.base import FixtureRepo  # noqa: E402


class RepoReadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = FixtureRepo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
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
        outside = os.path.join(self._tmp.name, os.pardir, 'outside.txt')
        with open(outside, 'w') as f:
            f.write('not ours\n')
        self.addCleanup(os.unlink, outside)
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
        link = os.path.join(self._tmp.name, os.pardir,
                            os.path.basename(self._tmp.name) + '-link')
        os.symlink(self._tmp.name, link)
        self.addCleanup(os.unlink, link)
        through = Repo(link, 'testrepo', 'shakenfist', github=FakeGitHub())
        self.assertEqual('gh issue create\n',
                         through.read('tools/ci/helper.sh'))


if __name__ == '__main__':
    unittest.main()
