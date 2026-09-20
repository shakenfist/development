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


def repo_file(*parts):
    """The bytes of a file in this repository.

    The deployment tests compare a copy this repository runs against
    the template it was copied from, so they read the real tree rather
    than a fixture. Shared for the same reason REPO_ROOT is: each of
    them otherwise carries its own walk up out of scripts/tests/, and
    the second copy of that chain is the one a move leaves behind.
    """
    with open(os.path.join(REPO_ROOT, *parts), 'rb') as f:
        return f.read()


def repo_text(*parts):
    """The same file, decoded, for the suites that read prose.

    The spec-agreement tests compare a page under docs/audits/ with the
    constants the check measures against, and each carries its own
    open() over a REPO_ROOT join because repo_file() hands back bytes:
    PushAuditTest and PlanTemplateTest read the page their block list
    has to be named on, and VmRunnerSizeTest reads the size vocabulary
    out of workflow-standards.md. The walk is the part that goes wrong
    when a file moves, and there is no reason for three more copies of
    it to exist than there are for the byte-comparing ones.
    """
    return repo_file(*parts).decode('utf-8')


def run_check(check, path, props=None, name='testrepo',
              org='shakenfist', github=None):
    """Run a check against a directory, the way the scheduler does.

    The adapter the moved tests call. They were written against the old
    `check_*(repo_path, props)` functions and are kept verbatim -- they
    are the coverage this refactor must not lose, and rewriting
    thousands of lines of assertions by hand is how coverage goes
    missing quietly. This goes through applies() and run(), so they
    exercise the real path rather than a shortcut around it.
    """
    repo = Repo(path, name, org,
                github=github if github is not None else FakeGitHub())
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

    def write_all(self, files):
        """Write a {repository-relative path: content} mapping.

        The shape most of the private `_repo` and `_check` helpers were
        built around -- DocsExternalLinksTest, DiagramFormatTest,
        MermaidLintCiTest, ConsoleLoggingTest, HeaderSanitizationTest,
        PlanPhaseReferencesTest, PlanSourceReferenceTest,
        PlanAuditPhaseTest, OrphanSkillMarkdownTest and
        LlmContextLintCiTest each carry their own copy of this loop,
        and the copies already disagree about whether a directory is
        created with makedirs(path) or makedirs(path or tmp).

        A None content writes an empty file, which is what
        DocsExternalLinksTest's fixtures mean by it. PushAuditTest
        spells absence the same way, so it filters before calling
        rather than having this guess which of the two a caller meant.
        """
        return [self.write(relative, content or '')
                for relative, content in files.items()]

    def workflow(self, name, content):
        """Write a workflow under .github/workflows/."""
        return self.write(os.path.join('.github', 'workflows', name),
                          content)

    def workflows(self, files):
        """Write a {workflow name: content} mapping.

        ExpensiveLanePathFilterTest, MergeGroupCancellationTest,
        VmRunnerSizeTest and PrAutoReviewSecretsInheritTest all drive
        their fixtures from a mapping of workflows, and each builds the
        directory and the loop itself.

        The directory is created even when the mapping is empty,
        because those helpers create it unconditionally and a check
        that walks .github/workflows/ distinguishes an empty one from
        an absent one.
        """
        os.makedirs(os.path.join(self.path, '.github', 'workflows'),
                    exist_ok=True)
        return self.write_all({
            os.path.join('.github', 'workflows', name): content
            for name, content in files.items()
        })

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

    def tempdir(self):
        """A second throwaway directory, cleaned up with the fixture.

        PushAuditTest and PlanTemplateTest each build a canonical
        shared-blocks directory that is deliberately not part of the
        repository under test, so that their cases do not depend on
        the real templates/shared-blocks/ content. That is a second
        TemporaryDirectory and a second addCleanup in each of them,
        which is the duplication this module exists to end rather than
        an exception to it.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

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

    def check(self, check_args=None, **kwargs):
        """Run the check under test against the fixture.

        Everything else named here is a property of the repository;
        check_args are constructor arguments for the check itself.
        Three checks take one: PushAudit and PlanTemplate read their
        canonical blocks from blocks_dir, and SfuiVendor clones
        canonical_url. This method is the only thing that instantiates
        check_class, so without check_args PushAuditTest,
        PlanTemplateTest and SfuiVendorTest could not use it at all.
        """
        instance = self.check_class(**(check_args or {}))
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
