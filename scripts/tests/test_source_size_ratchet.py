#!/usr/bin/env python3

"""Tests for templates/source-size-ratchet/source-size-ratchet.py, run rather than read.

The script is copied verbatim into every adopting repository's tools/,
and its characteristic failure is the quiet one: a file that grew
past its baseline and was not counted -- because it was untracked, or
excluded by a pattern broader than meant, or miscounted by one line
-- passes exactly like a file that did not grow. None of that is
visible from reading the script. So these run the template copy, the
one the fleet gets, against throwaway git repositories built here,
the way MermaidLintScriptTest runs mermaid-lint.sh.

The fixtures are small generated files either side of the threshold.
Nothing here reaches the network.

Run with: python3 scripts/tests/test_source_size_ratchet.py
"""

import ast
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import REPO_ROOT, repo_file  # noqa: E402

TEMPLATE_DIR = os.path.join(REPO_ROOT, 'templates', 'source-size-ratchet')
SCRIPT = os.path.join(TEMPLATE_DIR, 'source-size-ratchet.py')
HOOK = os.path.join(TEMPLATE_DIR, 'pre-commit-hook.yaml')

#: Written here rather than read from the script, so that a change to
#: the threshold has to be made in two places and cannot pass quietly.
THRESHOLD = 1500

BASELINE = 'tools/source-size-baseline'
EXCLUDE = 'tools/source-size-exclude'


def lines(n):
    """A Python source body exactly n lines long, newline-terminated."""
    return 'x = 1\n' * n


class RatchetTestCase(unittest.TestCase):
    """A throwaway repository per test, and the script run inside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self.env = dict(os.environ)
        # A stray global excludesFile or template would change what
        # git ls-files reports, and the script trusts that list.
        self.env['GIT_CONFIG_GLOBAL'] = os.devnull
        self.env['GIT_CONFIG_SYSTEM'] = os.devnull
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'user.name', 'Test')

    def git(self, *args):
        subprocess.run(['git', *args], cwd=self.repo, env=self.env, check=True,
                       stdout=subprocess.DEVNULL)

    def write(self, files):
        """Write {path: str} into the work tree, without adding it."""
        for name, content in files.items():
            target = os.path.join(self.repo, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'w', newline='') as f:
                f.write(content)

    def commit(self, files):
        """Write {path: str}, add everything and commit."""
        self.write(files)
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', 'fixture')

    def baseline(self, entries):
        """Commit a hand-written baseline of {path: lines}."""
        body = ''.join(f'{n} {p}\n' for p, n in sorted(entries.items()))
        self.commit({BASELINE: '# hand written\n\n' + body})

    def read_baseline(self):
        """The baseline's entries as {path: lines}, header ignored."""
        with open(os.path.join(self.repo, BASELINE)) as f:
            text = f.read()
        entries = {}
        for line in text.splitlines():
            if line and not line.startswith('#'):
                count, path = line.split(' ', 1)
                entries[path] = int(count)
        return entries

    def run_script(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, SCRIPT, *args], cwd=cwd or self.repo, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def assertExit(self, result, code):
        self.assertEqual(result.returncode, code,
                         f'stdout:\n{result.stdout}\nstderr:\n{result.stderr}')


class CheckModeTest(RatchetTestCase):

    def test_under_the_threshold_passes(self):
        self.commit({'a.py': lines(THRESHOLD)})
        self.assertExit(self.run_script(), 0)

    def test_a_new_oversize_file_fails(self):
        self.commit({'pkg/big.py': lines(THRESHOLD + 1)})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertIn('pkg/big.py: 1,501 lines, not in the baseline', result.stdout)
        self.assertIn(BASELINE, result.stdout)
        self.assertIn('pull request description', result.stdout)

    def test_a_baselined_file_at_its_entry_passes(self):
        self.commit({'big.rs': lines(2000)})
        self.baseline({'big.rs': 2000})
        result = self.run_script()
        self.assertExit(result, 0)
        self.assertEqual(result.stdout, '')

    def test_growth_fails_with_both_numbers(self):
        self.commit({'big.rs': lines(2001)})
        self.baseline({'big.rs': 2000})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertIn('big.rs: 2,001 lines, baseline 2,000', result.stdout)

    def test_a_single_large_jump_fails(self):
        """instar's shape: one landing of hundreds of lines, not many small ones."""
        self.commit({'main.rs': lines(2500)})
        self.baseline({'main.rs': 2000})
        self.assertExit(self.run_script(), 1)

    def test_shrinking_passes_and_suggests_update(self):
        self.commit({'big.rs': lines(1900)})
        self.baseline({'big.rs': 2000})
        result = self.run_script()
        self.assertExit(result, 0)
        self.assertIn('big.rs: baseline 2,000, now 1,900', result.stdout)
        self.assertIn('--update', result.stdout)

    def test_an_entry_for_a_removed_file_is_not_a_failure(self):
        self.commit({'small.py': lines(3)})
        self.baseline({'gone.py': 2000})
        result = self.run_script()
        self.assertExit(result, 0)
        self.assertIn('gone.py', result.stdout)

    def test_an_entry_for_a_file_now_under_the_threshold_is_not_a_failure(self):
        self.commit({'was_big.py': lines(THRESHOLD)})
        self.baseline({'was_big.py': 2000})
        self.assertExit(self.run_script(), 0)

    def test_deleted_but_tracked_file_is_skipped(self):
        self.commit({'big.py': lines(THRESHOLD + 1)})
        os.unlink(os.path.join(self.repo, 'big.py'))
        self.assertExit(self.run_script(), 0)

    def test_a_tracked_symlink_is_not_counted(self):
        self.commit({'data/big.txt': lines(THRESHOLD + 1)})
        os.symlink('data/big.txt', os.path.join(self.repo, 'link.py'))
        self.git('add', 'link.py')
        self.git('commit', '-q', '-m', 'link')
        self.assertExit(self.run_script(), 0)

    def test_an_untracked_oversize_file_is_ignored(self):
        self.commit({'a.py': lines(3)})
        self.write({'target/generated.rs': lines(THRESHOLD + 1),
                    'venv/lib/thing.py': lines(THRESHOLD + 1)})
        self.assertExit(self.run_script(), 0)

    def test_a_staged_but_uncommitted_file_is_counted(self):
        """The index is what is listed, which is what a pre-commit hook sees."""
        self.commit({'a.py': lines(3)})
        self.write({'new.py': lines(THRESHOLD + 1)})
        self.git('add', 'new.py')
        self.assertExit(self.run_script(), 1)

    def test_a_non_source_extension_is_ignored(self):
        self.commit({'docs/huge.md': lines(THRESHOLD + 1),
                     'Cargo.lock': lines(THRESHOLD + 1),
                     'data.json': lines(THRESHOLD + 1)})
        self.assertExit(self.run_script(), 0)

    def test_every_source_extension_is_counted(self):
        for ext in ('.c', '.css', '.go', '.h', '.js', '.py', '.rs', '.sh', '.ts', '.tsx'):
            with self.subTest(ext=ext):
                name = f'f{ext}'
                self.commit({name: lines(THRESHOLD + 1)})
                result = self.run_script()
                self.assertExit(result, 1)
                self.assertIn(name, result.stdout)
                self.git('rm', '-q', name)
                self.git('commit', '-q', '-m', 'remove')

    def test_builtin_exclusions_are_ignored(self):
        self.commit({'shakenfist/protos/db_pb2.py': lines(THRESHOLD + 1),
                     'shakenfist/protos/db_pb2_grpc.py': lines(THRESHOLD + 1),
                     'api/thing.pb.go': lines(THRESHOLD + 1),
                     'static/app.min.js': lines(THRESHOLD + 1),
                     'static/app.min.css': lines(THRESHOLD + 1)})
        self.assertExit(self.run_script(), 0)

    def test_exclude_file_glob_is_ignored(self):
        self.commit({'gen/a_generated.rs': lines(THRESHOLD + 1),
                     'src/real.rs': lines(THRESHOLD + 1),
                     EXCLUDE: '# Written by build.rs.\n\n*_generated.rs\n'})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertNotIn('a_generated.rs', result.stdout)
        self.assertIn('src/real.rs', result.stdout)

    def test_exclude_file_directory_pattern_excludes_the_tree(self):
        for pattern in ('sfui', 'sfui/', './sfui'):
            with self.subTest(pattern=pattern):
                self.commit({'sfui/deep/er/x.ts': lines(THRESHOLD + 1),
                             'sfuix/y.ts': lines(3),
                             EXCLUDE: f'# Vendored from sfui; reviewed there.\n{pattern}\n'})
                result = self.run_script()
                self.assertExit(result, 0)
                self.assertNotIn('match no tracked', result.stdout)

    def test_a_directory_pattern_does_not_match_a_sibling_prefix(self):
        """`sfui` names a directory component, not a string prefix."""
        self.commit({'sfuix/y.ts': lines(THRESHOLD + 1),
                     EXCLUDE: '# Vendored.\nsfui\n'})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertIn('sfuix/y.ts', result.stdout)

    def test_a_trailing_slash_only_matches_a_directory(self):
        self.commit({'odd.py': lines(THRESHOLD + 1),
                     EXCLUDE: '# Not a directory.\nodd.py/\n'})
        self.assertExit(self.run_script(), 1)

    def test_an_exclude_pattern_matching_nothing_is_noted(self):
        self.commit({'a.py': lines(3), EXCLUDE: '# Anticipatory.\nvendor\n'})
        result = self.run_script()
        self.assertExit(result, 0)
        self.assertIn('vendor', result.stdout)
        self.assertIn('match no tracked source file', result.stdout)

    def test_a_missing_trailing_newline_counts_as_a_line(self):
        self.commit({'edge.py': lines(THRESHOLD) + 'y = 2'})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertIn('edge.py: 1,501 lines', result.stdout)

    def test_crlf_endings_count_once_per_line(self):
        self.commit({'win.py': 'x = 1\r\n' * (THRESHOLD + 1)})
        result = self.run_script()
        self.assertExit(result, 1)
        self.assertIn('win.py: 1,501 lines', result.stdout)

    def test_runs_from_a_subdirectory(self):
        self.commit({'pkg/sub/big.py': lines(THRESHOLD + 1)})
        result = self.run_script(cwd=os.path.join(self.repo, 'pkg', 'sub'))
        self.assertExit(result, 1)
        self.assertIn('pkg/sub/big.py', result.stdout)

    def test_a_path_with_a_space_round_trips(self):
        self.commit({'my dir/big file.py': lines(THRESHOLD + 1)})
        self.assertExit(self.run_script('--init'), 0)
        self.assertEqual(self.read_baseline(), {'my dir/big file.py': THRESHOLD + 1})
        self.assertExit(self.run_script(), 0)


class ToolFailureTest(RatchetTestCase):
    """A tool failure exits 2, never 1: it is not a size violation."""

    def test_a_malformed_baseline_exits_2_naming_the_line(self):
        self.commit({'a.py': lines(3), BASELINE: '# header\n\n2000 ok.py\nnot a number.py\n'})
        result = self.run_script()
        self.assertExit(result, 2)
        self.assertIn(f'{BASELINE}:4', result.stderr)

    def test_a_duplicate_baseline_entry_exits_2(self):
        self.commit({'a.py': lines(3), BASELINE: '2000 a.py\n2001 a.py\n'})
        self.assertExit(self.run_script(), 2)

    def test_outside_a_git_repository_exits_2(self):
        with tempfile.TemporaryDirectory() as bare:
            env = dict(self.env)
            # Stop git walking up into whatever repository holds the
            # temporary directory.
            env['GIT_CEILING_DIRECTORIES'] = os.path.dirname(bare)
            result = subprocess.run(
                [sys.executable, SCRIPT], cwd=bare, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('error', result.stderr)

    def test_a_missing_git_exits_2(self):
        self.commit({'a.py': lines(3)})
        self.env['PATH'] = os.path.join(self.repo, 'no-such-bin')
        self.assertExit(self.run_script(), 2)


class InitTest(RatchetTestCase):

    def test_init_writes_the_sorted_baseline(self):
        self.commit({'z/late.py': lines(1600),
                     'a/early.rs': lines(2400),
                     'm/small.py': lines(THRESHOLD),
                     'gen_pb2.py': lines(5000)})
        result = self.run_script('--init')
        self.assertExit(result, 0)
        with open(os.path.join(self.repo, BASELINE)) as f:
            text = f.read()
        body = [line for line in text.splitlines() if line and not line.startswith('#')]
        self.assertEqual(body, ['2400 a/early.rs', '1600 z/late.py'])
        self.assertTrue(text.startswith('#'), 'the baseline carries an explanatory header')
        self.assertIn('pull request description', text)
        self.assertExit(self.run_script(), 0)

    def test_init_replaces_an_existing_baseline(self):
        self.commit({'big.py': lines(1600)})
        self.baseline({'big.py': 9000, 'gone.py': 2000})
        self.assertExit(self.run_script('--init'), 0)
        self.assertEqual(self.read_baseline(), {'big.py': 1600})

    def test_init_and_update_are_exclusive(self):
        self.commit({'a.py': lines(3)})
        self.assertExit(self.run_script('--init', '--update'), 2)


class UpdateTest(RatchetTestCase):

    def test_update_lowers_and_drops_but_keeps_the_rest(self):
        self.commit({'shrank.py': lines(1700),
                     'same.py': lines(1800),
                     'under.py': lines(THRESHOLD),
                     'excluded/x.py': lines(1900),
                     'other.py': lines(3)})
        self.baseline({'shrank.py': 2000, 'same.py': 1800, 'under.py': 1600,
                       'gone.py': 2000, 'excluded/x.py': 1900})
        self.commit({EXCLUDE: '# Vendored.\nexcluded\n'})
        result = self.run_script('--update')
        self.assertExit(result, 0)
        self.assertEqual(self.read_baseline(), {'shrank.py': 1700, 'same.py': 1800})

    def test_update_never_raises_an_entry_or_adds_a_file(self):
        self.commit({'grew.py': lines(2100), 'new.py': lines(1600), 'shrank.py': lines(1700)})
        self.baseline({'grew.py': 2000, 'shrank.py': 2000})
        result = self.run_script('--update')
        self.assertExit(result, 1)
        self.assertEqual(self.read_baseline(), {'grew.py': 2000, 'shrank.py': 1700})
        self.assertIn('grew.py: 2,100 lines, baseline 2,000', result.stdout)
        self.assertIn('new.py: 1,600 lines, not in the baseline', result.stdout)

    def test_update_without_a_baseline_creates_nothing(self):
        self.commit({'a.py': lines(3)})
        self.assertExit(self.run_script('--update'), 0)
        self.assertFalse(os.path.exists(os.path.join(self.repo, BASELINE)))


class TemplateFilesTest(unittest.TestCase):
    """The files shipped beside the script, which nothing else reads."""

    def test_the_script_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK), f'{SCRIPT} must be executable')

    def test_the_script_imports_only_the_standard_library(self):
        """It runs from a bare pre-commit `language: system` hook in every adopter."""
        with open(SCRIPT) as f:
            tree = ast.parse(f.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module.split('.')[0])
        self.assertEqual(imported - set(sys.stdlib_module_names), set())

    def test_the_hook_stanza_runs_the_tools_copy_on_every_commit(self):
        with open(HOOK) as f:
            hook = f.read()
        self.assertIn('repo: local', hook)
        self.assertIn('entry: python3 tools/source-size-ratchet.py', hook)
        self.assertIn('language: system', hook)
        self.assertIn('pass_filenames: false', hook)
        self.assertIn('always_run: true', hook)


class SourceSizeRatchetDeploymentTest(unittest.TestCase):
    """This repository's copy must match the template exactly.

    templates/source-size-ratchet/README.md promises that the script
    copies byte-for-byte into every adopting repository, this one
    included because it is in its own audit matrix. As with
    MermaidLintDeploymentTest, a drifted copy means the script
    shipped to the fleet is not the one this repository runs. It
    lives here rather than in test_docs_content.py beside that one
    because test_docs_content.py is itself a baselined file.
    """

    def test_script_matches_the_template(self):
        self.assertEqual(
            repo_file('tools', 'source-size-ratchet.py'),
            repo_file('templates', 'source-size-ratchet', 'source-size-ratchet.py'),
        )


if __name__ == '__main__':
    unittest.main()
