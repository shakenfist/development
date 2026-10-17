#!/usr/bin/env python3

"""Every check reads the audited repository through Repo.read.

`Repo.read` is the one guarded open of an audited repository's files
(see `audit.repo.read_within`): nothing outside the clone, nothing that
is not a regular file, nothing over the size cap. A check that opens a
file any other way escapes all three at once, and the cost is not a
wrong verdict but a published one -- a committed symlink pointing out
of the clone is read and quoted into an issue on the audited
repository, and a dangling one raises and is reported as `error`.

Asking each check's suite to remember a symlink case is how the forty
or so raw open() sites accumulated, so this is driven from the
registry instead. Every scheduled check is run against a fixture, the
files it opened are recorded, and then:

* every open() of a file in the fixture must have been made by
  read_within(). That is the structural assertion, and the one that
  catches a raw open() whatever the check goes on to do with what it
  read;
* each of those files is replaced by a symlink to a copy outside the
  fixture carrying a sentinel, and the check must neither open
  anything there nor say the sentinel in its result;
* each is replaced by a dangling symlink, and then by a directory of
  the same name, and the check must still reach a verdict rather than
  `error`;
* each top-level directory holding one is replaced by a symlink out
  of the fixture, which is the shape a walk follows: os.walk descends
  into its root even when the root is a link.

Running a check sees only the reads its fixture reaches, and only
through builtins.open, so SourceOpenTest also parses the package and
fails on any call that opens a file -- open(), os.open(), io.open(),
Path.open(), read_text(), read_bytes() -- outside an allowlist. That
covers the exempt checks and the branches no fixture reaches.

A check added to the registry is covered without anything being added
here. The fixture is this repository's own tracked tree, which carries
real workflows, plans, docs, templates and Python, with a small overlay
for the ecosystems it does not have (a Python package, a Rust crate, a
Node project). What the tree says does not matter -- no verdict is
asserted -- only that it makes the checks read far enough to exercise
their reads, so the suite's hook need not fire on every file in it.

Run with: python3 -m unittest tests.test_containment
"""

import ast
import builtins
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit import registry, repo as repo_module  # noqa: E402
from audit.check import ERROR  # noqa: E402
from audit.github import FakeGitHub  # noqa: E402
from audit.repo import Repo  # noqa: E402
from tests.base import REPO_ROOT  # noqa: E402


#: Checks this suite does not run, and why. Each needs a reason a
#: reader can check, because an entry here is a check whose reads
#: nothing holds to the rule.
EXEMPT = {
    # It clones canonical sfui to compare a vendored copy against, so
    # a fixture that reaches its reads needs a vendored copy and a
    # canonical repository to clone. Its reads go through Repo like
    # every other check's, which SourceOpenTest holds it to without
    # running it, and SfuiVendorTest covers the stamp's shapes with its
    # own fixtures.
    'sfui-vendor': 'clones canonical sfui; covered by its own suite',
}

#: In the path of every file outside the fixture, and in its content,
#: so that a check quoting either is caught.
SENTINEL = 'CONTAINMENT-SENTINEL-7f3a'

#: The ecosystems this repository does not have, so that the checks
#: for them read something.
OVERLAY = {
    'pyproject.toml': (
        '[project]\n'
        'name = "fixture"\n'
        'requires-python = ">=3.10"\n'
        'dependencies = [\n'
        '    "click==8.1.0",\n'
        '    # START_OF_INDIRECT_DEPS\n'
        '    "six==1.16.0",\n'
        '    # END_OF_INDIRECT_DEPS\n'
        ']\n'
        '\n'
        '[project.scripts]\n'
        'fixture = "fixture.main:main"\n'
        '\n'
        '[tool.setuptools_scm]\n'
        'version_file = "fixture/_version.py"\n'
    ),
    'fixture/__init__.py': '',
    'fixture/main.py': (
        'import http.server\n'
        'import logging\n'
        '\n'
        'import click\n'
        'from shakenfist_utilities import logs\n'
        '\n'
        'LOG = logs.setup_console(__name__)\n'
        '\n'
        '\n'
        'class Handler(http.server.BaseHTTPRequestHandler):\n'
        '    def do_GET(self):\n'
        '        self.send_header("X", self.path)\n'
        '\n'
        '\n'
        'def main():\n'
        '    logging.basicConfig()\n'
        '    click.echo("hi")\n'
    ),
    'tools/flake8wrap.sh': '#!/bin/bash\nflake8 $@\n',
    'Cargo.toml': (
        '[workspace]\nmembers = ["crate"]\n\n'
        '[workspace.lints.clippy]\nunwrap_used = "deny"\n'
    ),
    'clippy.toml': 'allow-unwrap-in-tests = true\n',
    'crate/Cargo.toml': '[package]\nname = "crate"\n',
    'fuzz/fuzz_targets/one.rs': 'fn main() {}\n',
    'package.json': json.dumps({
        'name': 'fixture',
        'dependencies': {'left-pad': '1.3.0'},
        'devDependencies': {'typescript': '5.0.0'},
        'scripts': {'build': 'tsc'},
    }),
    'package-lock.json': json.dumps({
        'name': 'fixture', 'lockfileVersion': 3,
        'packages': {
            '': {'dependencies': {'left-pad': '1.3.0'}},
            'node_modules/left-pad': {'version': '1.3.0'},
            'node_modules/typescript': {
                'version': '5.0.0', 'bin': {'tsc': 'bin/tsc'}},
        },
    }),
    'tsconfig.json': '{"compilerOptions": {"outDir": "out"}}\n',
    'src/index.ts': "import pad from 'left-pad';\nimport fs from 'fs';\n",
    'Dockerfile': 'FROM debian:buster\n',
    '.github/workflows/release.yml': (
        'name: release\n'
        'on:\n  workflow_dispatch:\n  push:\n    tags: ["v*"]\n'
        'permissions: {}\n'
        'jobs:\n'
        '  build:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n'
        '      - uses: actions/download-artifact@v4\n'
        '        with:\n          path: dist/\n'
        '      - uses: pypa/gh-action-pypi-publish@release/v1\n'
        '      - run: tools/release-helper.sh\n'
    ),
    '.github/workflows/pin-indirect-dependencies.yml': (
        'on: push\npermissions: {}\njobs:\n  pin:\n'
        '    runs-on: ubuntu-latest\n'
        '    steps:\n      - run: tools/pin-indirect-dependencies.sh\n'
    ),
    'tools/pin-indirect-dependencies.sh': '#!/bin/bash\n',
}


def tracked(root):
    out = subprocess.run(
        ['git', 'ls-files', '-z'], cwd=root, check=True,
        capture_output=True, text=True)
    return [path for path in out.stdout.split('\0') if path]


def git(path, *args):
    subprocess.run(['git', '-C', path] + list(args), check=True,
                   capture_output=True, text=True)


class OpenRecorder:
    """Every open() made while it is installed, and who made it."""

    def __init__(self):
        self.calls = []
        self._open = builtins.open

    def __call__(self, file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            caller = sys._getframe(1).f_code
            self.calls.append((os.fsdecode(file), caller))
        return self._open(file, *args, **kwargs)

    def install(self):
        return mock.patch('builtins.open', self)


def under(root, path):
    """Is path textually or actually inside root?"""
    real_root = os.path.realpath(root)
    for candidate in (os.path.abspath(path), os.path.realpath(path)):
        if candidate == real_root or candidate.startswith(real_root + os.sep):
            return True
    return False


class ContainmentTest(unittest.TestCase):
    """Each scheduled check, against each shape a read must refuse."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.template = os.path.join(cls._tmp.name, 'template')
        for relative in tracked(REPO_ROOT):
            source = os.path.join(REPO_ROOT, relative)
            target = os.path.join(cls.template, relative)
            if not os.path.lexists(source):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        for relative, content in OVERLAY.items():
            target = os.path.join(cls.template, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'w') as f:
                f.write(content)
        git(cls.template, 'init', '-q', '-b', 'main')
        git(cls.template, 'add', '-A')
        git(cls.template, '-c', 'user.email=test@example.com',
            '-c', 'user.name=Test', '-c', 'commit.gpgsign=false',
            'commit', '-q', '-m', 'fixture')

        cls.checks = [c for c in registry.CHECKS if c.id not in EXEMPT]
        cls.opened = {}
        cls.violations = {}
        work = cls.copy('baseline')
        for check in cls.checks:
            recorder, _result = cls.run_one(check, work)
            opened, raw = set(), []
            for path, caller in recorder.calls:
                if not under(work, path):
                    continue
                relative = os.path.relpath(os.path.abspath(path), work)
                if caller is repo_module.read_within.__code__:
                    opened.add(relative)
                else:
                    raw.append(f'{relative} from {caller.co_name}() in '
                               f'{os.path.basename(caller.co_filename)}')
            cls.opened[check.id] = opened
            cls.violations[check.id] = raw

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def copy(cls, name):
        target = os.path.join(cls._tmp.name, name)
        shutil.copytree(cls.template, target, symlinks=True)
        return target

    @classmethod
    def run_one(cls, check, path):
        repo = Repo(path, 'testrepo', github=FakeGitHub())
        recorder = OpenRecorder()
        with recorder.install():
            result = registry.run_check(check, repo)
        return recorder, result

    def outside(self, name):
        """A directory outside every fixture, named with the sentinel."""
        path = os.path.join(self._tmp.name, f'{SENTINEL}-{name}')
        os.makedirs(path, exist_ok=True)
        return path

    def replace(self, work, relative, shape, outside=None):
        """Replace one fixture file with a shape a read must refuse."""
        full = os.path.join(work, relative)
        original = b''
        if os.path.isfile(full):
            with open(full, 'rb') as f:
                original = f.read()
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full)
        elif os.path.lexists(full):
            os.remove(full)
        if shape == 'link':
            target = os.path.join(outside, relative)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(original + f'\n{SENTINEL}\n'.encode())
            os.symlink(target, full)
        elif shape == 'dangling':
            os.symlink(os.path.join(outside, 'missing', relative), full)
        elif shape == 'directory':
            os.makedirs(full)

    def test_the_exemptions_name_scheduled_checks(self):
        ids = {check.id for check in registry.CHECKS}
        self.assertEqual(set(EXEMPT) - ids, set())

    def test_the_fixture_makes_the_checks_read(self):
        """Guard the fixture: a check that read nothing tested nothing.

        Most checks read something here. Requiring every one to would
        tie this suite to which criteria apply to the fixture, so the
        floor is a count rather than a list, and it is far enough above
        zero that a fixture which stopped being read would fail it.
        """
        reading = [cid for cid, opened in self.opened.items() if opened]
        self.assertGreater(len(reading), len(self.checks) * 2 // 3,
                           f'only {sorted(reading)} read anything')

    def test_every_read_goes_through_read_within(self):
        for check in self.checks:
            with self.subTest(check=check.id):
                self.assertEqual(self.violations[check.id], [])

    def assert_contained(self, check, recorder, result, outside):
        self.assertNotEqual(result['status'], ERROR, result['details'])
        self.assertNotIn(SENTINEL, json.dumps(result))
        escaped = sorted({path for path, _caller in recorder.calls
                          if under(outside, path)})
        self.assertEqual(escaped, [], 'opened outside the checkout')

    def test_a_symlink_out_of_the_checkout_is_not_followed(self):
        for check in self.checks:
            if not self.opened[check.id]:
                continue
            with self.subTest(check=check.id):
                work = self.copy(f'link-{check.id}')
                outside = self.outside(check.id)
                for relative in self.opened[check.id]:
                    self.replace(work, relative, 'link', outside)
                recorder, result = self.run_one(check, work)
                self.assert_contained(check, recorder, result, outside)

    def test_a_directory_linked_out_of_the_checkout_is_not_walked(self):
        for check in self.checks:
            tops = {relative.split(os.sep)[0]
                    for relative in self.opened[check.id]
                    if os.sep in relative}
            if not tops:
                continue
            with self.subTest(check=check.id):
                work = self.copy(f'dirlink-{check.id}')
                outside = self.outside(f'dir-{check.id}')
                for top in tops:
                    target = os.path.join(outside, top)
                    shutil.copytree(os.path.join(work, top), target,
                                    symlinks=True)
                    for dirpath, _dirnames, filenames in os.walk(target):
                        for filename in filenames:
                            full = os.path.join(dirpath, filename)
                            if os.path.islink(full):
                                continue
                            with open(full, 'a') as f:
                                f.write(f'\n{SENTINEL}\n')
                    shutil.rmtree(os.path.join(work, top))
                    os.symlink(target, os.path.join(work, top))
                recorder, result = self.run_one(check, work)
                self.assert_contained(check, recorder, result, outside)

    def test_a_dangling_symlink_is_no_file(self):
        for check in self.checks:
            if not self.opened[check.id]:
                continue
            with self.subTest(check=check.id):
                work = self.copy(f'dangling-{check.id}')
                outside = self.outside(f'dangling-{check.id}')
                for relative in self.opened[check.id]:
                    self.replace(work, relative, 'dangling', outside)
                _recorder, result = self.run_one(check, work)
                self.assertNotEqual(result['status'], ERROR,
                                    result['details'])

    def test_a_directory_named_like_a_file_is_no_file(self):
        for check in self.checks:
            if not self.opened[check.id]:
                continue
            with self.subTest(check=check.id):
                work = self.copy(f'directory-{check.id}')
                for relative in self.opened[check.id]:
                    self.replace(work, relative, 'directory')
                _recorder, result = self.run_one(check, work)
                self.assertNotEqual(result['status'], ERROR,
                                    result['details'])


#: The calls in the audit package allowed to open a file, as
#: (path relative to scripts/audit, enclosing function). Each needs a
#: reason, because an entry here is an open nothing else checks.
OPEN_ALLOWED = {
    # The one guarded open of an audited repository.
    ('repo.py', 'read_within'),
    # Reads this repository's own templates/shared-blocks/, never the
    # repository under audit.
    ('text/shared_blocks.py', 'load_canonical_block'),
}

#: Call names that open a file, whether as a builtin, a module
#: function (os.open, io.open, os.fdopen) or a method (Path.open).
OPENERS = {'open', 'fdopen', 'read_text', 'read_bytes'}


class SourceOpenTest(unittest.TestCase):
    """No code in the audit package opens a file except through the guard.

    Matched on the call's name rather than resolved, so it errs towards
    a false alarm -- a method called open() on something that is not a
    file would need an entry above -- and not towards a miss. Reads by
    a subprocess (git show, a canonical repository's vendor.sh) are not
    calls this can see, and are out of its scope.
    """

    def opens(self):
        package = os.path.join(REPO_ROOT, 'scripts', 'audit')
        found = set()
        for root, _dirnames, filenames in os.walk(package):
            for name in filenames:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(root, name)
                relative = os.path.relpath(path, package)
                with open(path) as f:
                    tree = ast.parse(f.read(), path)
                for function, node in self.calls(tree, '<module>'):
                    func = node.func
                    called = (func.id if isinstance(func, ast.Name)
                              else getattr(func, 'attr', None))
                    if called in OPENERS:
                        found.add((relative, function, node.lineno))
        return found

    def calls(self, node, function):
        """Every Call under node, with the function it is in."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from self.calls(child, child.name)
                continue
            if isinstance(child, ast.Call):
                yield function, child
            yield from self.calls(child, function)

    def test_only_the_allowlist_opens_files(self):
        found = self.opens()
        unexpected = sorted(f'{path}:{line} in {function}()'
                            for path, function, line in found
                            if (path, function) not in OPEN_ALLOWED)
        self.assertEqual(unexpected, [])

    def test_the_allowlist_is_still_needed(self):
        found = {(path, function) for path, function, _line in self.opens()}
        self.assertEqual(OPEN_ALLOWED - found, set())


if __name__ == '__main__':
    unittest.main()
