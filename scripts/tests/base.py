"""Shared machinery for the check tests.

Before this existed each test class built its own temporary directory,
its own `_repo` helper and its own `_check` wrapper: about forty
near-identical helpers over a hundred and seven temporary-directory
sites. The duplication was not the worst of it -- the variation was.
Two classes testing the same criterion could disagree about what a
fixture repository looks like, and neither would fail.
"""

import os
import subprocess
import tempfile
import unittest

from audit.github import FakeGitHub
from audit.repo import Repo

#: This repository, for the tests that check a criterion against the
#: specification page or the canonical template it is supposed to agree
#: with. Shared because every suite that does so would otherwise
#: recompute the walk up from wherever it happens to sit, and get it
#: wrong the first time it moved.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def run_check(check, path, props=None):
    """Run a check against a directory, the way the scheduler does.

    The adapter the moved tests call. They were written against the old
    `check_*(repo_path, props)` functions and are kept verbatim -- they
    are the coverage this refactor must not lose, and rewriting
    thousands of lines of assertions by hand is how coverage goes
    missing quietly. This goes through applies() and run(), so they
    exercise the real path rather than a shortcut around it.
    """
    repo = Repo(path, 'testrepo', 'shakenfist', github=FakeGitHub())
    if props:
        repo.props.update(props)
    reason = check.applies(repo)
    if reason is not None:
        return check.skip(reason)
    return check.run(repo)


class FixtureRepo:
    """A throwaway checkout to run a check against."""

    def __init__(self, path):
        self.path = path

    def write(self, relative, content):
        """Write a file, creating any directories it needs."""
        full = os.path.join(self.path, relative)
        directory = os.path.dirname(full)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return full

    def workflow(self, name, content):
        """Write a workflow under .github/workflows/."""
        return self.write(os.path.join('.github', 'workflows', name),
                          content)

    def git(self, *args):
        return subprocess.run(
            ['git'] + list(args), cwd=self.path,
            check=True, capture_output=True, text=True,
        )

    def init_git(self):
        """Make it a real checkout, for the checks that need one."""
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test User')
        self.git('config', 'commit.gpgsign', 'false')

    def commit(self, message='fixture'):
        self.git('add', '-A')
        self.git('commit', '-m', message)


class CheckTestCase(unittest.TestCase):
    """Base for a check's tests: a fixture repo and result assertions."""

    #: Subclasses set this to the Check subclass under test.
    check_class = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = FixtureRepo(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def repo(self, name='testrepo', org='shakenfist', github=None,
             **props):
        """A Repo over the fixture, with properties supplied directly.

        Properties are given rather than detected so that a test can
        say what it is testing. Anything not named falls back to what
        detection would have found, so a test that does not care about
        a property does not have to enumerate it.
        """
        detected = Repo(self.fixture.path, name, org).props
        detected.update(props)
        return Repo(self.fixture.path, name, org,
                    github=github if github is not None else FakeGitHub(),
                    props=detected)

    def check(self, **kwargs):
        """Run the check under test against the fixture."""
        instance = self.check_class()
        repo = self.repo(**kwargs)
        reason = instance.applies(repo)
        if reason is not None:
            return instance.skip(reason)
        return instance.run(repo)

    def assert_pass(self, result):
        self.assertEqual(result['status'], 'pass', result['details'])
        return result

    def assert_fail(self, result, containing=None):
        self.assertEqual(result['status'], 'fail', result['details'])
        if containing is not None:
            self.assertIn(containing, result['details'])
        return result

    def assert_skip(self, result, containing=None):
        self.assertEqual(result['status'], 'not_applicable',
                         result['details'])
        if containing is not None:
            self.assertIn(containing, result['details'])
        return result
