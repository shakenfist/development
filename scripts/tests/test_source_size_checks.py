#!/usr/bin/env python3

"""The source size criteria.

Named test_source_size_checks.py rather than after its module, because
test_source_size_ratchet.py already exists and tests the shipped tool.
Two suites a letter apart, one testing a tool and one the criteria that
require it, is a trap for the next reader.

Run with: python3 -m unittest tests.test_source_size_checks
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.check import ToolFailure  # noqa: E402
from audit.checks import source_size  # noqa: E402
from tests.base import CheckTestCase, repo_file  # noqa: E402


#: The template's exact bytes, which an adopter's copy is compared
#: with. Read from this repository's own tree rather than invented, so
#: that a case meaning "byte-identical" cannot drift from what the
#: criterion compares against.
SCRIPT = repo_file('templates', 'source-size-ratchet',
                   'source-size-ratchet.py')

#: The hook stanza as the template ships it, reduced to the keys the
#: criterion reads.
CONFIG = """\
repos:
  - repo: local
    hooks:
      - id: source-size-ratchet
        name: source size ratchet
        entry: python3 -I tools/source-size-ratchet.py
        language: system
        pass_filenames: false
        always_run: true
"""

#: The tool named in a comment and nowhere else, which is a repository
#: describing the hook it does not run.
CONFIG_COMMENT_ONLY = """\
repos:
  - repo: local
    hooks:
      # Nothing here runs tools/source-size-ratchet.py yet.
      - id: flake8wrap
        name: flake8
        entry: tools/flake8wrap.sh
        language: system
"""

#: No mention of the tool at all.
CONFIG_WITHOUT_HOOK = """\
repos:
  - repo: local
    hooks:
      - id: flake8wrap
        name: flake8
        entry: tools/flake8wrap.sh
        language: system
"""

#: This repository's own shape: one lane running every hook, through a
#: quoted path into a venv, so the closing quote sits between the
#: command and its subcommand.
PRE_COMMIT_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: '"${RUNNER_TEMP}/venv-lint/bin/pre-commit" run --all-files'
"""

#: ryll's shape: a job per hook, reaching this one by invoking the
#: script itself.
DIRECT_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  source-size-ratchet:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Check source file sizes against the baseline
        run: python3 -I tools/source-size-ratchet.py
"""

#: The whole config, invoked plainly.
ALL_FILES_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: pre-commit run --all-files
"""

#: The same lane with its flags on a continuation line, which is how a
#: long invocation through a venv path is written.
CONTINUATION_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: |
          "${RUNNER_TEMP}/venv-lint/bin/pre-commit" run \\
            --all-files --show-diff-on-failure
"""

#: One named hook, which runs that hook and no others.
SINGLE_HOOK_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  skillsaw:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: pre-commit run skillsaw --all-files
"""

#: ryll's shape with its ratchet job deleted: each hook that CI runs is
#: a job of its own, and the one that would run this hook is gone.
RYLL_WITHOUT_RATCHET_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  skillsaw:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Lint the agent context
        run: pre-commit run skillsaw --all-files
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: cargo test --workspace
"""

#: Installing pre-commit, and installing its git hooks, runs no hook
#: over the tree.
INSTALL_ONLY_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: pip install pre-commit==4.0.1
      - run: pre-commit install
"""

#: ryll's real job: the script invoked from a job of its own, behind a
#: fork guard.
RYLL_WORKFLOW = """\
name: ci
on: [pull_request]
jobs:
  source-size-ratchet:
    name: source size ratchet
    runs-on: [self-hosted, vm, debian-13, s]
    if: >-
      github.event_name != 'merge_group'
      && (github.event_name != 'pull_request'
          || github.event.pull_request.head.repo.full_name == github.repository)
    steps:
      - uses: actions/checkout@v7
      - name: Check source files against the baseline
        run: python3 -I tools/source-size-ratchet.py
"""

#: instar's real shape: a step in a larger job, with a paths-filter
#: step after it that has nothing to do with this script.
INSTAR_WORKFLOW = """\
name: Functional Tests
on:
  pull_request:
jobs:
  ci-tooling:
    runs-on: ubuntu-latest
    steps:
      - name: Check the source size ratchet
        if: github.event_name == 'pull_request' || github.event_name == 'merge_group'
        run: python3 -I tools/source-size-ratchet.py

      - uses: dorny/paths-filter@v4
        id: filter
        with:
          filters: |
            code:
              - 'src/**'
"""

#: The script named where a workflow filters on it, and invoked
#: nowhere. Each is a shape that mentions the path and runs nothing.
MENTION_ONLY_WORKFLOWS = {
    'a trigger paths filter': """\
name: ci
on:
  pull_request:
    paths:
      - 'tools/source-size-ratchet.py'
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: make build
""",
    'a paths-filter action list': """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: dorny/paths-filter@v4
        id: filter
        with:
          filters: |
            tooling:
              - tools/source-size-ratchet.py
      - run: make build
""",
    'a diff grep': """\
name: ci
on: [pull_request]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - run: git diff --name-only origin/main | grep tools/source-size-ratchet.py
""",
}

#: The script's path in a hook's `files:` pattern and nowhere else: a
#: hook that runs on the script, and a hook that does not run it.
CONFIG_FILES_ONLY = """\
repos:
  - repo: local
    hooks:
      - id: flake8wrap
        name: flake8
        entry: tools/flake8wrap.sh
        language: system
        files: ^(tools/source-size-ratchet.py|tools/other.py)$
        exclude: tools/source-size-ratchet.py
"""

#: A long entry written as a block scalar, which is how one that does
#: not fit on a line is spelled. The `files:` after it is not part of it.
CONFIG_BLOCK_ENTRY = """\
repos:
  - repo: local
    hooks:
      - id: source-size-ratchet
        name: source size ratchet
        entry: >-
          python3 -I
          tools/source-size-ratchet.py
        language: system
        always_run: true
"""

CONFIG_BLOCK_ENTRY_THEN_FILES = """\
repos:
  - repo: local
    hooks:
      - id: other
        name: other
        entry: >-
          python3 -I
          tools/other.py
        language: system
        files: tools/source-size-ratchet.py
"""


class SourceExtensionsTest(unittest.TestCase):
    """The list the criterion reads out of the template's text."""

    def test_the_template_tuple_is_read(self):
        extensions = source_size.source_extensions()
        self.assertIsInstance(extensions, tuple)
        self.assertIn('.py', extensions)
        self.assertIn('.rs', extensions)
        self.assertTrue(
            all(extension.startswith('.') for extension in extensions))

    def test_the_tuple_is_the_shipped_one(self):
        """Not a copy of the list: the file's own line is the authority.

        Spelled as a substring of the template rather than as a
        literal here, so that widening the template's list cannot
        leave this suite asserting the old one.
        """
        text = repo_file('templates', 'source-size-ratchet',
                         'source-size-ratchet.py').decode('utf-8')
        for extension in source_size.source_extensions():
            self.assertIn(f"'{extension}'", text)


class SourceExtensionsFailureTest(unittest.TestCase):
    """The branches that make a broken audit checkout an `error`.

    A template the audit cannot read or understand is the audit's own
    checkout being wrong, and has to be reported as a failure to
    measure rather than as a verdict about somebody's repository.
    """

    def setUp(self):
        # The result is cached, so every case starts from a cache that
        # is empty and leaves one behind, rather than a verdict one case
        # got from a doctored template or one a doctored template hides.
        source_size.source_extensions.cache_clear()
        self.addCleanup(source_size.source_extensions.cache_clear)

    def read_as(self, text):
        return mock.patch.object(source_size, 'read_within',
                                 return_value=text)

    def test_an_unreadable_template_raises(self):
        with self.read_as(None):
            with self.assertRaisesRegex(ToolFailure, 'Could not read'):
                source_size.source_extensions()

    def test_an_unparseable_template_raises(self):
        with self.read_as('SOURCE_EXTENSIONS = ('):
            with self.assertRaisesRegex(ToolFailure, 'Could not parse'):
                source_size.source_extensions()

    def test_a_template_without_the_assignment_raises(self):
        with self.read_as("OTHER = ('.py',)\n"):
            with self.assertRaisesRegex(ToolFailure, 'declares no SOURCE'):
                source_size.source_extensions()

    def test_an_assignment_that_is_not_a_literal_raises(self):
        for value in ('tuple(compute())', '5'):
            with self.subTest(value=value):
                source_size.source_extensions.cache_clear()
                with self.read_as(f'SOURCE_EXTENSIONS = {value}\n'):
                    with self.assertRaisesRegex(ToolFailure, 'not a literal'):
                        source_size.source_extensions()

    def test_a_failure_is_not_remembered(self):
        with self.read_as(None):
            with self.assertRaises(ToolFailure):
                source_size.source_extensions()
        with self.read_as("SOURCE_EXTENSIONS = ('.py',)\n"):
            self.assertEqual(source_size.source_extensions(), ('.py',))

    def test_the_template_is_parsed_once(self):
        with self.read_as("SOURCE_EXTENSIONS = ('.py', '.rs')\n") as read:
            first = source_size.source_extensions()
            second = source_size.source_extensions()
        self.assertEqual(first, ('.py', '.rs'))
        self.assertEqual(second, first)
        self.assertEqual(read.call_count, 1)


class SourceSizeRatchetTest(CheckTestCase):
    check_class = source_size.SourceSizeRatchet

    def build(self, script=SCRIPT, config=CONFIG, workflows=None,
              source=True, dangling=False):
        """Write a fixture and make it a checkout the index can be read from.

        `script` is bytes so that a case can differ from the template
        by one of them; None leaves the copy out entirely, and
        `dangling` writes the name as a link to nothing instead.
        """
        fixture = self.fixture
        fixture.write('README.md', '# fixture\n')
        if source:
            fixture.write('src/app.py', 'print("hello")\n')
        if config is not None:
            fixture.write('.pre-commit-config.yaml', config)
        for name, content in (workflows or {}).items():
            fixture.workflow(name, content)
        copy = os.path.join(fixture.path, 'tools', 'source-size-ratchet.py')
        if dangling:
            os.makedirs(os.path.dirname(copy), exist_ok=True)
            os.symlink(os.path.join(fixture.path, 'tools', 'gone.py'), copy)
        elif script is not None:
            os.makedirs(os.path.dirname(copy), exist_ok=True)
            with open(copy, 'wb') as f:
                f.write(script)
        fixture.init_git()
        fixture.commit()
        return fixture

    def check_with(self, **kwargs):
        """build() and check() in one, for the single-case methods."""
        self.build(**kwargs)
        return self.check()

    def test_a_compliant_repository_passes(self):
        """With this repository's own lane: a quoted venv path to pre-commit.

        PRE_COMMIT_WORKFLOW is that shape, which is what a separate
        test of the quoted form would have asserted.
        """
        self.build(workflows={'ci.yml': PRE_COMMIT_WORKFLOW})
        self.assert_pass(self.check())

    def test_no_tracked_source_is_not_applicable(self):
        # Markdown is not source the ratchet counts, and the script
        # itself is left out so that its own extension does not make
        # the repository look like it has some.
        self.build(script=None, config=None, source=False)
        self.assert_skip(self.check(), 'No tracked source files')

    def test_a_missing_copy_fails(self):
        self.build(script=None, workflows={'ci.yml': PRE_COMMIT_WORKFLOW})
        self.assert_fail(self.check(),
                         'tools/source-size-ratchet.py is missing')

    def test_a_copy_differing_by_one_byte_fails(self):
        self.build(script=SCRIPT + b'\n',
                   workflows={'ci.yml': PRE_COMMIT_WORKFLOW})
        self.assert_fail(
            self.check(),
            'tools/source-size-ratchet.py differs from '
            'templates/source-size-ratchet/source-size-ratchet.py')

    def test_a_dangling_link_in_place_of_the_copy_fails(self):
        # Distinguished from a missing copy: the name is there, so
        # "copy it" is not the fix.
        self.build(dangling=True, workflows={'ci.yml': PRE_COMMIT_WORKFLOW})
        result = self.assert_fail(
            self.check(),
            'tools/source-size-ratchet.py is not a regular file the audit '
            'will read')
        self.assertNotIn('is missing', result['details'])

    def findings(self, result):
        """The semicolon-separated findings, without the fix advice."""
        return result['details'].split('. Adopt the ratchet')[0].split('; ')

    def test_a_config_without_the_hook_but_with_a_lane_is_one_finding(self):
        """A lane that runs every hook is a lane, hook declared or not.

        `pre-commit run --all-files` is run by this repository's CI,
        so saying that no workflow runs it would be false. Only the
        hook is missing. The script is the template's, so a finding
        naming the copy would be the wrong one as well.
        """
        result = self.assert_fail(
            self.check_with(config=CONFIG_WITHOUT_HOOK,
                            workflows={'ci.yml': PRE_COMMIT_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke '
            'tools/source-size-ratchet.py')
        self.assertEqual(
            self.findings(result),
            ['.pre-commit-config.yaml does not invoke '
             'tools/source-size-ratchet.py'])
        self.assertNotIn('no workflow runs', result['details'])
        self.assertNotIn('differs from', result['details'])

    def test_a_config_without_the_hook_but_with_a_direct_lane_is_one_finding(
            self):
        result = self.assert_fail(
            self.check_with(config=CONFIG_WITHOUT_HOOK,
                            workflows={'ci.yml': DIRECT_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke')
        self.assertEqual(len(self.findings(result)), 1)
        self.assertNotIn('no workflow runs', result['details'])

    def test_no_hook_and_no_lane_are_two_findings(self):
        result = self.assert_fail(
            self.check_with(config=CONFIG_WITHOUT_HOOK,
                            workflows={'ci.yml': SINGLE_HOOK_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke '
            'tools/source-size-ratchet.py')
        self.assertEqual(len(self.findings(result)), 2)
        self.assertIn('no workflow runs every pre-commit hook',
                      result['details'])

    def test_the_hook_named_only_in_a_comment_fails(self):
        result = self.assert_fail(
            self.check_with(config=CONFIG_COMMENT_ONLY,
                            workflows={'ci.yml': PRE_COMMIT_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke '
            'tools/source-size-ratchet.py')
        self.assertNotIn('differs from', result['details'])

    def test_the_hook_with_no_workflow_at_all_fails(self):
        result = self.assert_fail(
            self.check_with(workflows=None),
            'no workflow runs every pre-commit hook (as `pre-commit run '
            '--all-files` does) or invokes tools/source-size-ratchet.py')
        # Only the lane is missing: the copy and the hook are both as
        # they should be, so a failure naming either would be the
        # wrong finding.
        self.assertNotIn('differs from', result['details'])
        self.assertNotIn('does not invoke', result['details'])

    def test_a_workflow_that_only_installs_pre_commit_fails(self):
        result = self.assert_fail(
            self.check_with(workflows={'ci.yml': INSTALL_ONLY_WORKFLOW}),
            'no workflow runs every pre-commit hook (as `pre-commit run '
            '--all-files` does) or invokes tools/source-size-ratchet.py')
        self.assertNotIn('does not invoke', result['details'])

    def test_a_plain_all_files_lane_is_a_lane(self):
        self.build(workflows={'ci.yml': ALL_FILES_WORKFLOW})
        self.assert_pass(self.check())

    def test_a_lane_continued_onto_a_second_line_is_a_lane(self):
        # The flags are on the next line, and the patterns are matched
        # a line at a time, so the trailing backslash is what says the
        # invocation is not a bare `pre-commit run`.
        self.build(workflows={'ci.yml': CONTINUATION_WORKFLOW})
        self.assert_pass(self.check())

    def test_a_single_hook_lane_is_not_a_lane(self):
        result = self.assert_fail(
            self.check_with(workflows={'ci.yml': SINGLE_HOOK_WORKFLOW}),
            'no workflow runs every pre-commit hook (as `pre-commit run '
            '--all-files` does) or invokes tools/source-size-ratchet.py')
        self.assertNotIn('does not invoke tools/', result['details'])

    def test_ryll_shaped_ci_without_its_ratchet_job_fails(self):
        """The regression this criterion exists to catch.

        A repository that runs each hook as its own job reaches only
        the hooks its jobs name. Deleting the ratchet job leaves the
        hook declared and ungated, and a `pre-commit run <other hook>`
        job elsewhere in the same workflow must not stand in for it --
        which is what a looser reading of the invocation would have
        done, passing this repository's own pilot adopter with the job
        the pilot added removed again.
        """
        result = self.assert_fail(
            self.check_with(
                workflows={'ci.yml': RYLL_WITHOUT_RATCHET_WORKFLOW}),
            'no workflow runs every pre-commit hook (as `pre-commit run '
            '--all-files` does) or invokes tools/source-size-ratchet.py')
        self.assertNotIn('differs from', result['details'])
        self.assertNotIn('does not invoke tools/', result['details'])

    def test_a_direct_invocation_is_a_lane(self):
        self.build(workflows={'ci.yml': DIRECT_WORKFLOW})
        self.assert_pass(self.check())

    def test_rylls_job_is_a_lane(self):
        self.build(workflows={'ci.yml': RYLL_WORKFLOW})
        self.assert_pass(self.check())

    def test_instars_step_is_a_lane(self):
        self.build(workflows={'ci.yml': INSTAR_WORKFLOW})
        self.assert_pass(self.check())

    def test_a_workflow_that_only_mentions_the_script_is_not_a_lane(self):
        for shape, workflow in MENTION_ONLY_WORKFLOWS.items():
            with self.subTest(shape):
                self.fresh_fixture()
                result = self.assert_fail(
                    self.check_with(workflows={'ci.yml': workflow}),
                    'no workflow runs every pre-commit hook')
                self.assertNotIn('does not invoke tools/',
                                 result['details'])

    def test_a_dot_slash_invocation_is_a_lane(self):
        self.build(workflows={
            'ci.yml': DIRECT_WORKFLOW.replace(
                'python3 -I tools/source-size-ratchet.py',
                './tools/source-size-ratchet.py')})
        self.assert_pass(self.check())

    def test_a_script_named_in_a_files_pattern_is_not_the_hook(self):
        result = self.assert_fail(
            self.check_with(config=CONFIG_FILES_ONLY,
                            workflows={'ci.yml': PRE_COMMIT_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke '
            'tools/source-size-ratchet.py')
        self.assertEqual(len(self.findings(result)), 1)

    def test_an_entry_written_as_a_block_scalar_is_the_hook(self):
        self.build(config=CONFIG_BLOCK_ENTRY,
                   workflows={'ci.yml': PRE_COMMIT_WORKFLOW})
        self.assert_pass(self.check())

    def test_a_files_pattern_after_a_block_scalar_entry_is_not_the_hook(self):
        self.assert_fail(
            self.check_with(config=CONFIG_BLOCK_ENTRY_THEN_FILES,
                            workflows={'ci.yml': PRE_COMMIT_WORKFLOW}),
            '.pre-commit-config.yaml does not invoke')


class RustFunctionLengthLintTest(CheckTestCase):
    check_class = source_size.RustFunctionLengthLint

    ROOT = (
        '[workspace]\n'
        'members = ["crates/thing"]\n'
        '\n'
        '[workspace.lints.clippy]\n'
        'too_many_lines = "warn"\n'
    )
    CLIPPY = '# Measured, see the spec.\ntoo-many-lines-threshold = 200\n'
    MEMBER = '[package]\nname = "thing"\n\n[lints]\nworkspace = true\n'

    def build(self, root=ROOT, clippy=CLIPPY, extra=None):
        """A workspace with one inheriting member, plus `extra` files."""
        files = {'crates/thing/Cargo.toml': self.MEMBER}
        if root is not None:
            files['Cargo.toml'] = root
        if clippy is not None:
            files['clippy.toml'] = clippy
        files.update(extra or {})
        self.fixture.init_git()
        self.fixture.write_all(files)
        self.fixture.commit()
        return self.check(has_cargo_toml=True)

    def with_source(self, source):
        return self.build(extra={'crates/thing/src/lib.rs': source})

    def test_without_cargo_toml_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No Cargo.toml')

    def test_a_configured_workspace_passes(self):
        self.assert_pass(self.build())

    def test_the_lint_missing_fails(self):
        self.assert_fail(
            self.build(root=self.ROOT.replace(
                'too_many_lines = "warn"', 'unwrap_used = "warn"')),
            'too_many_lines lint not set to warn or deny in Cargo.toml')

    def test_allow_with_a_comment_naming_deny_is_not_enabled(self):
        root = self.ROOT.replace(
            'too_many_lines = "warn"',
            'too_many_lines = "allow"  # was "deny"')
        self.assert_fail(self.build(root=root), 'not set to warn or deny')

    def test_the_table_form_passes(self):
        root = self.ROOT.replace(
            'too_many_lines = "warn"',
            'too_many_lines = { level = "warn", priority = -1 }')
        self.assert_pass(self.build(root=root))

    def test_a_single_crate_repository_passes(self):
        root = ('[package]\nname = "one"\n\n[lints.clippy]\n'
                'too_many_lines = "deny"\n')
        self.fixture.init_git()
        self.fixture.write_all(
            {'Cargo.toml': root, 'clippy.toml': self.CLIPPY})
        self.fixture.commit()
        self.assert_pass(self.check(has_cargo_toml=True))

    def test_a_missing_clippy_toml_fails(self):
        self.assert_fail(
            self.build(clippy=None), 'clippy.toml missing')

    def test_a_clippy_toml_without_the_threshold_fails(self):
        self.assert_fail(
            self.build(clippy='allow-unwrap-in-tests = true\n'),
            'too-many-lines-threshold')

    def test_a_commented_out_threshold_does_not_count(self):
        self.assert_fail(
            self.build(clippy='# too-many-lines-threshold = 200\n'),
            'too-many-lines-threshold')

    def test_a_member_that_neither_inherits_nor_defines_fails(self):
        result = self.build(extra={
            'crates/other/Cargo.toml': '[package]\nname = "other"\n'})
        self.assert_fail(
            result, 'crates/other/Cargo.toml neither inherits workspace')

    def test_a_member_defining_the_lint_itself_passes(self):
        self.assert_pass(self.build(extra={
            'crates/other/Cargo.toml':
                '[package]\nname = "other"\n\n[lints.clippy]\n'
                'too_many_lines = "warn"\n'}))

    def test_a_fuzz_crate_that_does_neither_passes(self):
        self.assert_pass(self.build(extra={
            'crates/thing/fuzz/Cargo.toml':
                '[package]\nname = "thing-fuzz"\n'}))

    def test_a_nested_workspace_crate_that_does_neither_passes(self):
        # Cargo excludes a nested workspace root from the parent, so
        # the root's [workspace.lints] cannot reach it.
        self.assert_pass(self.build(extra={
            'tools/gen/Cargo.toml':
                '[package]\nname = "gen"\n\n[workspace]\n'}))

    def test_a_member_with_only_workspace_tables_is_still_asked(self):
        # A [workspace.dependencies] or [workspace.lints] copied from a
        # root manifest declares no workspace.
        for table in ('[workspace.dependencies]\nserde = "1"\n',
                      '[workspace.lints.clippy]\nunwrap_used = "warn"\n'):
            with self.subTest(table=table):
                self.fresh_fixture()
                self.assert_fail(self.build(extra={
                    'crates/other/Cargo.toml':
                        f'[package]\nname = "other"\n\n{table}'}),
                    'crates/other/Cargo.toml neither inherits')

    def test_a_nested_workspace_root_with_a_dependencies_table_passes(self):
        self.assert_pass(self.build(extra={
            'tools/gen/Cargo.toml':
                '[package]\nname = "gen"\n\n[workspace]\n\n'
                '[workspace.dependencies]\nserde = "1"\n'}))

    def test_a_crate_that_merely_mentions_workspace_is_still_asked(self):
        self.assert_fail(self.build(extra={
            'tools/gen/Cargo.toml':
                '[package]\nname = "gen"\n'
                'description = "a [workspace] helper"\n'}),
            'tools/gen/Cargo.toml neither inherits')

    def test_a_bare_allow_fails(self):
        result = self.with_source(
            'fn a() {}\n\n#[allow(clippy::too_many_lines)]\nfn b() {}\n')
        self.assert_fail(result, 'crates/thing/src/lib.rs:3')

    def test_an_allow_with_a_reason_passes(self):
        self.assert_pass(self.with_source(
            '#[allow(clippy::too_many_lines, reason = "one big match")]\n'
            'fn b() {}\n'))

    def test_an_allow_with_a_comment_above_passes(self):
        self.assert_pass(self.with_source(
            '// A flat dispatch table; splitting it hides the symmetry.\n'
            '#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n'))

    def test_a_comment_two_lines_above_does_not_count(self):
        self.assert_fail(self.with_source(
            '// Explains something.\n\n'
            '#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n'), 'lib.rs:3')

    def test_an_empty_comment_or_a_doc_comment_above_does_not_count(self):
        result = self.with_source(
            '//\n#[allow(clippy::too_many_lines)]\nfn a() {}\n\n'
            '/// Does the thing.\n#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n')
        self.assert_fail(result, 'lib.rs:2, crates/thing/src/lib.rs:6')

    def test_a_single_line_block_comment_above_passes(self):
        self.assert_pass(self.with_source(
            '/* A flat dispatch table; splitting it hides the symmetry. */\n'
            '#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n'))

    def test_a_multi_line_block_comment_above_does_not_count(self):
        # Only the line immediately above is read, and for a block
        # comment over several lines that is the last one, which does
        # not open a comment. The limit is pinned rather than incidental.
        self.assert_fail(self.with_source(
            '/* A flat dispatch table;\n'
            '   splitting it hides the symmetry. */\n'
            '#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n'), 'lib.rs:3')

    def test_a_doc_block_comment_above_does_not_count(self):
        self.assert_fail(self.with_source(
            '/** Does the thing. */\n'
            '#[allow(clippy::too_many_lines)]\n'
            'fn b() {}\n'), 'lib.rs:2')

    def test_a_multi_line_allow_without_a_reason_fails(self):
        result = self.with_source(
            'fn a() {}\n\n'
            '#[allow(\n'
            '    clippy::too_many_arguments,\n'
            '    clippy::too_many_lines,\n'
            ')]\n'
            'fn b() {}\n')
        self.assert_fail(result, 'lib.rs:3')

    def test_a_multi_line_allow_with_a_reason_passes(self):
        self.assert_pass(self.with_source(
            '#[allow(\n'
            '    clippy::too_many_lines,\n'
            '    reason = "the wire format is one flat sequence; [x] and\n'
            '              (y) inside the reason must not end the attribute"\n'
            ')]\n'
            'fn b() {}\n'))

    def test_a_multi_line_allow_with_a_comment_above_passes(self):
        self.assert_pass(self.with_source(
            '// Mirrors the protocol table.\n'
            '#[allow(\n'
            '    clippy::too_many_lines,\n'
            ')]\n'
            'fn b() {}\n'))

    def test_an_empty_reason_does_not_count(self):
        self.assert_fail(self.with_source(
            '#[allow(clippy::too_many_lines, reason = "")]\nfn b() {}\n'),
            'lib.rs:1')

    def test_a_lint_name_inside_a_reason_is_not_an_allow(self):
        self.assert_pass(self.with_source(
            '#[allow(dead_code, reason = "allow(clippy::too_many_lines) '
            'is elsewhere")]\nfn b() {}\n'))

    def test_a_crate_level_allow_is_found(self):
        self.assert_fail(self.with_source(
            '#![allow(clippy::too_many_lines)]\nfn b() {}\n'), 'lib.rs:1')

    def test_an_allow_inside_cfg_attr_is_found(self):
        self.assert_fail(self.with_source(
            '#[cfg_attr(test, allow(clippy::too_many_lines))]\nfn b() {}\n'),
            'lib.rs:1')

    def test_an_expect_beside_an_allow_of_another_lint_is_not_flagged(self):
        # The lint is handled by `expect`; the `allow` is for dead_code.
        self.assert_pass(self.with_source(
            '#[cfg_attr(test, expect(clippy::too_many_lines), '
            'allow(dead_code))]\n'
            'fn b() {}\n'))
        self.assert_pass(self.with_source(
            '#[cfg_attr(test, allow(dead_code), '
            'expect(clippy::too_many_lines))]\n'
            'fn b() {}\n'))

    def test_an_allow_of_the_lint_beside_an_expect_is_still_flagged(self):
        self.assert_fail(self.with_source(
            '#[cfg_attr(test, expect(dead_code), '
            'allow(clippy::too_many_lines))]\n'
            'fn b() {}\n'), 'lib.rs:1')

    def test_an_expect_is_not_flagged(self):
        self.assert_pass(self.with_source(
            '#[expect(clippy::too_many_lines)]\nfn b() {}\n'))

    def test_text_in_comments_and_strings_is_not_an_attribute(self):
        self.assert_pass(self.with_source(
            '// #[allow(clippy::too_many_lines)]\n'
            '/* #[allow(clippy::too_many_lines)] */\n'
            'const S: &str = "#[allow(clippy::too_many_lines)]";\n'
            'const R: &str = r#"#[allow(clippy::too_many_lines)]"#;\n'
            "const Q: char = '\"';\n"
            'fn b<\'a>(x: &\'a str) -> &\'a str { x }\n'))

    def test_a_bare_allow_after_a_quote_char_is_still_found(self):
        self.assert_fail(self.with_source(
            "const Q: char = '\"';\n"
            '#[allow(clippy::too_many_lines)]\nfn b() {}\n'), 'lib.rs:2')

    def test_a_bare_allow_in_any_tracked_rust_file_is_found(self):
        result = self.build(extra={
            'src/deep/mod.rs': '#[allow(clippy::too_many_lines)]\nfn b() {}\n'})
        self.assert_fail(result, 'src/deep/mod.rs:1')

    def test_rylls_own_shape_passes(self):
        # ryll: a workspace root with the lint under [workspace.lints],
        # a clippy.toml with a measured threshold, members inheriting,
        # a fuzz crate declaring no lints, a nested-workspace tool
        # defining a different lint, and multi-line expect attributes.
        root = (
            '[workspace]\nresolver = "2"\n'
            'members = ["ryll", "shakenfist-spice-protocol"]\n'
            'exclude = ["tools/gen-swatches-jpeg"]\n\n'
            '[workspace.lints.clippy]\nunwrap_used = "warn"\n'
            'too_many_lines = "warn"\n')
        clippy = (
            '# 2026-11: about 4,145 functions, 40 over 100 lines, 13 over\n'
            '# 200. Set to 200.\n'
            'too-many-lines-threshold = 200\n'
            'allow-unwrap-in-tests = true\n')
        result = self.build(root=root, clippy=clippy, extra={
            'ryll/Cargo.toml': self.MEMBER,
            'shakenfist-spice-protocol/Cargo.toml': self.MEMBER,
            'shakenfist-spice-protocol/fuzz/Cargo.toml':
                '[package]\nname = "fuzz"\n',
            'tools/gen-swatches-jpeg/Cargo.toml':
                '[package]\nname = "gen"\n\n[lints.clippy]\n'
                'unwrap_used = "warn"\n\n[workspace]\n',
            'ryll/src/main.rs':
                '#[expect(\n'
                '    clippy::too_many_lines,\n'
                '    reason = "one arm per message type"\n'
                ')]\n'
                'fn run() {}\n'})
        self.assert_pass(result)


if __name__ == '__main__':
    unittest.main()
