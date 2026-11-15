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
import tempfile
import unittest

CONFIG = '.pre-commit-config.yaml'

#: Module-level anchors the suites build repository paths from, as
#: repository-relative directories. Both are spelled as
#: `os.path.dirname(__file__)` walks, which mean nothing to a parser.
ANCHORS = {'REPO_ROOT': '', 'SCRIPT_DIR': 'scripts'}

#: What hooks() reports for a value it could not read: an `args:` or
#: `exclude:` written as a YAML list across lines rather than inline.
#: Deliberately distinguishable from the empty string, which means the
#: key is not there at all. A test that treats the two alike holds
#: vacuously the day somebody reformats one -- which is how an
#: assertion that a flag is *not* in a hook's arguments can pass with
#: the flag sitting right there in the file.
UNPARSED = '<written across lines>'


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def tracked_files():
    out = subprocess.run(
        ['git', 'ls-files'], cwd=repo_root(),
        capture_output=True, text=True, check=True).stdout
    return set(out.split('\n')) - {''}


def hooks(path=None):
    """Read the hooks as {alias or id: {...}}.

    Keyed by alias where there is one, because a repository may run the
    same hook id twice with different arguments. Two hooks that would
    key the same way raise rather than being silently collapsed: the
    first spelling of this keyed each hook by id as it read it and then
    deleted that key when an alias arrived, so the un-aliased half of a
    duplicated id was absent from the map entirely and any assertion
    about it would have been made against nothing at all.

    Line-based, like the rest of this tree's YAML reading: the audit
    ships without PyYAML on purpose and the shape here is flat. A value
    the line-based read cannot see -- a list written across lines -- is
    reported as UNPARSED rather than as missing, so a caller can tell
    the two apart.
    """
    collected = []
    current = None
    with open(path or os.path.join(repo_root(), CONFIG), 'r') as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('- id: '):
                current = {'id': stripped[len('- id: '):].strip(),
                           'files': None, 'exclude': None, 'args': '',
                           'entry': '', 'always_run': False}
                collected.append(current)
            elif current is None:
                continue
            elif stripped.startswith('files: '):
                current['files'] = stripped[len('files: '):].strip()
            elif stripped.startswith('alias: '):
                current['alias'] = stripped[len('alias: '):].strip()
            elif stripped.startswith('exclude: '):
                current['exclude'] = stripped[len('exclude: '):].strip()
            elif stripped.startswith('args: '):
                current['args'] = stripped[len('args: '):].strip()
            elif stripped in ('args:', 'exclude:'):
                current[stripped[:-1]] = UNPARSED
            elif stripped.startswith('entry: '):
                current['entry'] = stripped[len('entry: '):].strip()
            elif stripped.startswith('always_run: '):
                current['always_run'] = 'true' in stripped

    found = {}
    for hook in collected:
        key = hook.get('alias') or hook['id']
        if key in found:
            raise ValueError(
                f'two hooks are keyed {key!r}: give one of them an alias, '
                'because a map holds only one and the other would be absent '
                'from every test that reads this')
        found[key] = hook
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


class RenovateConfigHookTest(unittest.TestCase):
    """Every Renovate configuration here is validated by exactly one hook.

    There are two validator hooks, because a repository configuration
    and the fleet runner's global configuration need different modes.
    One that holds a global option must fail as a repository
    configuration, and the other must accept them. A file matched by
    neither is never validated and reports "Passed" regardless, which
    is how the preset went unchecked when the hook's own default
    pattern matched renovate.json but not renovate-fleet.json. A file
    matched by both is validated in the wrong mode by one of them,
    which fails a correct global configuration or passes a bad
    repository one.

    The patterns are derived rather than listed, and the set they are
    measured against is derived too, from the names Renovate reads a
    configuration from plus this fleet's preset naming. So a second
    preset, a `.github/renovate.json`, or a `renovate.json5` added
    later is covered without anyone widening a pattern, and if one is
    not, this fails rather than the validators passing silently.
    """

    REPOSITORY = 'renovate-config-validator-repository'
    GLOBAL = 'renovate-config-validator-global'

    #: The fleet runner's global configuration, the one file the second
    #: hook exists for.
    RUNNER = 'renovate-runner.json'

    #: The names Renovate reads a repository configuration from, and
    #: the `renovate-<name>.json` spelling this fleet gives a preset.
    #: Names rather than a pattern, so this is independent of the
    #: patterns it checks.
    CONFIG_NAMES = frozenset({
        'renovate.json', 'renovate.json5', 'renovate.jsonc',
        '.renovaterc', '.renovaterc.json', '.renovaterc.json5',
    })
    PRESET_PREFIX = 'renovate-'
    PRESET_SUFFIXES = ('.json', '.json5', '.jsonc')

    def _configs(self, tracked):
        found = set()
        for path in tracked:
            name = os.path.basename(path)
            if name in self.CONFIG_NAMES:
                found.add(path)
            elif (name.startswith(self.PRESET_PREFIX)
                    and name.endswith(self.PRESET_SUFFIXES)):
                found.add(path)
        return found

    def _validators(self, path):
        """The validator hooks that would run on this path."""
        found = []
        for name in (self.REPOSITORY, self.GLOBAL):
            hook = hooks()[name]
            if not re.search(hook['files'], path):
                continue
            if hook['exclude'] and re.search(hook['exclude'], path):
                continue
            found.append(name)
        return found

    def test_both_hooks_are_configured(self):
        for name in (self.REPOSITORY, self.GLOBAL):
            self.assertIn(name, hooks())
            self.assertIsNotNone(hooks()[name]['files'])

    def _args(self, name):
        """The hook's arguments, or a loud failure if they cannot be read."""
        args = hooks()[name]['args']
        self.assertNotEqual(
            args, UNPARSED,
            name + ' writes its args across lines, which the line-based '
            'read cannot see: the assertions about what is in them would '
            'hold against any arguments at all')
        return args

    def test_the_modes_are_the_ones_intended(self):
        # The repository hook's arguments have to parse before
        # membership in them means anything, so the shape is asserted
        # first. The global hook is meant to carry no args at all,
        # which is why only the shape it must not have -- a list the
        # read cannot see -- is checked there.
        repository = self._args(self.REPOSITORY)
        self.assertTrue(
            repository.startswith('['),
            self.REPOSITORY + ' args are not a single-line YAML list, so '
            'the flag below cannot be looked for in them: ' + repository)
        self.assertIn('--no-global', repository)
        self.assertNotIn('--no-global', self._args(self.GLOBAL))

    def test_the_tree_still_has_configs_to_check(self):
        # Without this the tests below could pass on an empty set, which
        # is the failure mode they exist to catch.
        found = self._configs(tracked_files())
        for expected in ('renovate.json', 'renovate-fleet.json',
                         self.RUNNER,
                         os.path.join('templates', 'renovate',
                                      'renovate.json')):
            self.assertIn(expected, found)

    def test_every_renovate_config_is_validated_by_exactly_one_hook(self):
        configs = self._configs(tracked_files())
        self.assertTrue(configs, 'no Renovate configurations were found')
        unvalidated = sorted(p for p in configs if not self._validators(p))
        self.assertEqual(
            unvalidated, [],
            'neither validator hook matches these Renovate '
            'configurations, so they are never validated:\n  '
            + '\n  '.join(unvalidated))
        doubled = sorted(p for p in configs
                         if len(self._validators(p)) > 1)
        self.assertEqual(
            doubled, [],
            'both validator hooks match these Renovate configurations, '
            'so one validates them in the wrong mode:\n  '
            + '\n  '.join(doubled))

    def test_the_global_hook_validates_the_runner_config_alone(self):
        self.assertEqual(self._validators(self.RUNNER), [self.GLOBAL])
        self.assertEqual(self._validators('renovate.json'),
                         [self.REPOSITORY])


class HookParsingTest(unittest.TestCase):
    """hooks() must not lose a hook, nor read a value it cannot parse.

    Both are silent failures in a map every suite above reads: a hook
    absent from it fails nothing, and a value read as empty satisfies
    every assertion about what is not in it.
    """

    def _hooks(self, body):
        with tempfile.NamedTemporaryFile('w', suffix='.yaml',
                                         delete=False) as handle:
            handle.write(body)
        self.addCleanup(os.unlink, handle.name)
        return hooks(handle.name)

    def test_a_duplicated_id_is_kept_under_the_alias_and_the_id(self):
        parsed = self._hooks(
            'repos:\n'
            '  - repo: somewhere\n'
            '    hooks:\n'
            '      - id: actionlint\n'
            "        args: ['-config-file', '.github/actionlint.yaml']\n"
            '      - id: actionlint\n'
            '        alias: actionlint-templates\n'
            '        files: ^templates/\n')
        # Both, where keying by id as the file was read kept neither:
        # the second entry overwrote the first, and then its alias
        # deleted the key they shared.
        self.assertIn('actionlint', parsed)
        self.assertIn('actionlint-templates', parsed)
        self.assertIsNone(parsed['actionlint']['files'])
        self.assertEqual(parsed['actionlint-templates']['files'],
                         '^templates/')

    def test_a_duplicated_id_with_no_alias_raises(self):
        with self.assertRaises(ValueError) as raised:
            self._hooks(
                'repos:\n'
                '  - repo: somewhere\n'
                '    hooks:\n'
                '      - id: actionlint\n'
                '      - id: actionlint\n')
        self.assertIn('actionlint', str(raised.exception))

    def test_args_written_across_lines_are_not_read_as_absent(self):
        parsed = self._hooks(
            'repos:\n'
            '  - repo: somewhere\n'
            '    hooks:\n'
            '      - id: renovate-config-validator\n'
            '        args:\n'
            "          - '--no-global'\n")
        self.assertEqual(parsed['renovate-config-validator']['args'],
                         UNPARSED)

    def test_an_inline_list_is_read_as_itself(self):
        # The guard on the guard: if every args: read as UNPARSED the
        # test above would pass and the mode assertions would all fail.
        parsed = self._hooks(
            'repos:\n'
            '  - repo: somewhere\n'
            '    hooks:\n'
            '      - id: renovate-config-validator\n'
            "        args: ['--no-global']\n")
        self.assertEqual(parsed['renovate-config-validator']['args'],
                         "['--no-global']")


if __name__ == '__main__':
    unittest.main()
