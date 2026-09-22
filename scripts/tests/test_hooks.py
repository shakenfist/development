#!/usr/bin/env python3

"""Every test hook must fire on the files its suite reads, and every
suite must sit where a hook looks for it.

A `local` pre-commit hook carries a `files:` pattern, and a suite whose
pattern is narrower than its dependencies is worse than no hook: it
reports "Passed" on the commit that breaks it, and the failure surfaces
later, against somebody else's change. This repository has now shipped
that bug twice -- the package suite lost the `.github/workflows/`
trigger its predecessor carried, and the snapshot suite kept a pattern
naming `audit-check.py` alone after the source it derives from moved
into `scripts/audit/` -- so it is worth a test rather than another
comment.

Dependencies are derived rather than listed, from three things a suite
can name without ambiguity:

* the modules under `scripts/` it imports;
* paths built from `REPO_ROOT` or `SCRIPT_DIR`, which is how a suite
  reaches this repository rather than a fixture;
* paths bound to a module- or class-level constant. That is the idiom
  for a real file here -- `MATRIX_WORKFLOW`, `IN_SCOPE_DOC` -- while a
  fixture is written from an inline literal, so the distinction is the
  one the suites already draw.

A path a suite computes at runtime is invisible to all three, so this
is a floor rather than a proof. It is not an over-approximation: a
false dependency here would fail a green tree.

The second class is the other half of the same property. A pattern
that covers everything a suite reads is worth nothing if no hook runs
the suite at all, which is what a test module written outside
scripts/tests/ amounts to: discovery looks there and nowhere else.

Run with: python3 -m unittest tests.test_hooks
"""

import ast
import os
import re
import subprocess
import unittest

CONFIG = '.pre-commit-config.yaml'

#: Module-level anchors the suites build repository paths from, as
#: repository-relative directories. Both are spelled as
#: `os.path.dirname(__file__)` walks, which mean nothing to a parser.
ANCHORS = {'REPO_ROOT': '', 'SCRIPT_DIR': 'scripts'}


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def tracked_files():
    out = subprocess.run(
        ['git', 'ls-files'], cwd=repo_root(),
        capture_output=True, text=True, check=True).stdout
    return set(out.split('\n')) - {''}


def hooks():
    """Read the local hooks as {id: {...}}.

    Line-based, like the rest of this tree's YAML reading: the audit
    ships without PyYAML on purpose and the shape here is flat.
    """
    found = {}
    current = None
    with open(os.path.join(repo_root(), CONFIG), 'r') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('- id: '):
                current = {'id': stripped[len('- id: '):].strip(),
                           'files': None, 'entry': '', 'always_run': False}
                found[current['id']] = current
            elif current is None:
                continue
            elif stripped.startswith('files: '):
                current['files'] = stripped[len('files: '):].strip()
            elif stripped.startswith('entry: '):
                current['entry'] = stripped[len('entry: '):].strip()
            elif stripped.startswith('always_run: '):
                current['always_run'] = 'true' in stripped
    return found


def suite_sources(entry):
    """The test modules a hook's entry command runs."""
    match = re.search(r'discover -s (\S+)', entry)
    if match:
        directory = os.path.join(repo_root(), match.group(1))
        return sorted(os.path.join(match.group(1), name)
                      for name in os.listdir(directory)
                      if name.endswith('.py'))
    match = re.search(r'(scripts/\S+\.py)', entry)
    return [match.group(1)] if match else []


def _literal_join(node):
    """`os.path.join(...)` of literals, or None if anything else."""
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'join'):
        return None
    parts = []
    for index, arg in enumerate(node.args):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            parts.append(arg.value)
        elif (index == 0 and isinstance(arg, ast.Name)
                and arg.id in ANCHORS):
            if ANCHORS[arg.id]:
                parts.append(ANCHORS[arg.id])
        else:
            return None
    return os.path.normpath(os.path.join(*parts)) if parts else None


def _paths_named(source):
    """Paths a module names unambiguously. See the module docstring."""
    tree = ast.parse(source)
    found = set()

    def value_paths(node):
        """The path(s) a bound value spells out, if any."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        joined = _literal_join(node)
        if joined:
            return [joined]
        if isinstance(node, (ast.List, ast.Tuple)):
            out = []
            for item in node.elts:
                out.extend(value_paths(item))
            return out
        return []

    for node in ast.walk(tree):
        # Anchored anywhere: only this repository is reachable from
        # REPO_ROOT, so an anchored join is never a fixture path.
        joined = _literal_join(node)
        if joined:
            found.add(joined)
        # Bound to a name at module or class level.
        if isinstance(node, (ast.Module, ast.ClassDef)):
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                found.update(value_paths(statement.value))
    return found


def _imported_modules(source):
    """Modules under scripts/ that a suite imports, as paths."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        for name in names:
            relative = os.path.join('scripts', *name.split('.'))
            for candidate in (relative + '.py',
                              os.path.join(relative, '__init__.py')):
                if os.path.exists(os.path.join(repo_root(), candidate)):
                    found.add(candidate)
    return found


def dependencies(sources, tracked):
    """Tracked paths a suite reads, expanding any directory it names."""
    directories = {os.path.dirname(p) for p in tracked if os.path.dirname(p)}
    found = set(sources)
    for source in sources:
        with open(os.path.join(repo_root(), source), 'r') as handle:
            text = handle.read()
        found.update(_imported_modules(text))
        for path in _paths_named(text):
            if path in tracked:
                found.add(path)
            elif path in directories:
                # A suite that names a directory is scanning it for
                # modules: test_audit_snapshot walks scripts/audit/ to
                # re-derive which checks reach the network, and
                # test_audit_update_docs lists scripts/ for test_*.py.
                # Only the Python files, because neither reads the
                # shell scripts sitting beside them.
                found.update(p for p in tracked
                             if p.startswith(path + os.sep)
                             and p.endswith('.py'))
    return found


class HookTriggerTest(unittest.TestCase):
    """The hooks that run a Python test suite watch what it reads."""

    def setUp(self):
        self.tracked = tracked_files()
        self.hooks = {
            hook_id: hook for hook_id, hook in hooks().items()
            if 'unittest' in hook['entry'] or re.search(
                r'python3 scripts/tests/test_\S+\.py', hook['entry'])
        }

    def test_every_python_suite_hook_was_found(self):
        """Guard the parse: a silent zero here would pass everything.

        Named rather than counted, because the count is now two and a
        count of two is satisfied by any two lines that happen to
        parse.
        """
        self.assertIn('audit-package-tests', self.hooks)
        self.assertIn('review-tracking-tests', self.hooks)

    def test_each_hook_fires_on_everything_its_suite_reads(self):
        for hook_id, hook in sorted(self.hooks.items()):
            with self.subTest(hook=hook_id):
                if hook['always_run']:
                    continue
                self.assertIsNotNone(
                    hook['files'],
                    f'{hook_id} has neither files: nor always_run:')
                pattern = re.compile(hook['files'])
                sources = suite_sources(hook['entry'])
                self.assertTrue(sources, f'{hook_id}: no suite found')
                missed = sorted(
                    path for path in dependencies(sources, self.tracked)
                    if not pattern.search(path))
                self.assertEqual(
                    missed, [],
                    f'{hook_id} reads these but does not fire on them:\n  '
                    + '\n  '.join(missed))


class SuiteLocationTest(unittest.TestCase):
    """Every Python suite lives under scripts/tests/, where a hook looks.

    The suites were split across scripts/ and scripts/tests/ for a
    while, for no better reason than that a migration moved the check
    tests and stopped. That is confusing to read -- two directories
    with no rule distinguishing them -- and it is also the shape in
    which a suite nothing runs goes unnoticed, because the hook that
    runs everything discovers scripts/tests/ and nowhere else. A test
    module written beside the script it tests is then a file that runs
    only when somebody thinks to run it.

    Asserted here rather than in a consistency audit on purpose. The
    rest of the fleet already puts its tests under a tests/ directory,
    so a fleet-wide criterion would measure one repository -- this one
    -- at the price of a check, a specification page and a compliance
    column, and would have to learn to ignore .tox, node_modules and
    vendored checkouts to do it.
    """

    SUITES = os.path.join('scripts', 'tests')

    def _modules(self, tracked):
        return {path for path in tracked
                if path.split(os.sep)[0] == 'scripts'
                and os.path.basename(path).startswith('test_')
                and path.endswith('.py')}

    def test_every_suite_is_under_scripts_tests(self):
        stray = sorted(path for path in self._modules(tracked_files())
                       if os.path.dirname(path) != self.SUITES)
        self.assertEqual(
            stray, [],
            'these are test suites outside ' + self.SUITES + ':\n  '
            + '\n  '.join(stray))

    def test_a_hook_runs_every_suite(self):
        covered = set()
        for hook in hooks().values():
            covered.update(suite_sources(hook['entry']))
        missed = sorted(self._modules(tracked_files()) - covered)
        self.assertEqual(
            missed, [],
            'no pre-commit hook runs these:\n  ' + '\n  '.join(missed))


if __name__ == '__main__':
    unittest.main()
