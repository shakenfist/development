#!/usr/bin/env python3

"""Tests for audit/text/pre_commit.py.

The criteria that read pre-commit's YAML are tested against it in
test_ci_workflows.py and test_review.py; these pin down the shapes the
scanner itself must handle, since a regression here would move both.

Run with: python3 scripts/tests/test_pre_commit.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.text.pre_commit import item_value, scan_pre_commit_config, scan_pre_commit_yaml  # noqa: E402


def hook_ids(scanned):
    return [item_value(hook, 'id') for hook in scanned.hooks()]


class ScanPreCommitYamlTest(unittest.TestCase):

    def test_indented_hooks(self):
        scanned = scan_pre_commit_yaml('repos:\n'
                                       '  - repo: local\n'
                                       '    hooks:\n'
                                       '      - id: one\n'
                                       '        language: node\n'
                                       '      - id: two\n')
        self.assertEqual(hook_ids(scanned), ['one', 'two'])
        self.assertEqual(item_value(scanned.hooks()[0], 'language'), 'node')

    def test_unindented_hooks(self):
        """pre-commit's own sample-config style: each list at its parent's column."""
        scanned = scan_pre_commit_yaml('repos:\n'
                                       '- repo: https://github.com/example/hooks\n'
                                       '  rev: v1\n'
                                       '  hooks:\n'
                                       '  - id: one\n'
                                       '    language: node\n'
                                       '- repo: local\n'
                                       '  hooks:\n'
                                       '  - id: two\n')
        one, two = scanned.hooks()
        self.assertEqual((item_value(one, 'id'), item_value(one, 'language')), ('one', 'node'))
        self.assertEqual(item_value(one['parent'], 'rev'), 'v1')
        self.assertEqual(item_value(two['parent'], 'repo'), 'local')
        self.assertNotIn('language', two['keys'])

    def test_parent_links(self):
        scanned = scan_pre_commit_yaml('repos:\n'
                                       '  - repo: local\n'
                                       '    hooks:\n'
                                       '      - id: one\n')
        hook = scanned.hooks()[0]
        self.assertEqual(item_value(hook['parent'], 'repo'), 'local')
        self.assertIsNone(hook['parent']['parent'])

    def test_a_bare_list_manifest(self):
        scanned = scan_pre_commit_yaml('- id: one\n'
                                       '  language: node\n'
                                       '  language_version: lts\n'
                                       '- id: two\n'
                                       '  language: python\n')
        self.assertEqual(hook_ids(scanned), ['one', 'two'])
        self.assertEqual(item_value(scanned.hooks()[0], 'language_version'), 'lts')
        self.assertIsNone(scanned.hooks()[0]['parent'])

    def test_args_items_are_their_own_and_not_the_hooks(self):
        scanned = scan_pre_commit_yaml('repos:\n'
                                       '  - repo: local\n'
                                       '    hooks:\n'
                                       '      - id: one\n'
                                       '        args:\n'
                                       '          - language: python\n'
                                       '        language: node\n')
        hook = scanned.hooks()[0]
        self.assertEqual(hook['keys']['language'], ['node'])
        nested = [item for item in scanned.items if item['parent'] is hook]
        self.assertEqual([item_value(item, 'language') for item in nested], ['python'])

    def test_a_section_before_repos(self):
        scanned = scan_pre_commit_yaml('default_language_version:\n'
                                       '  node: lts\n'
                                       'repos:\n'
                                       '  - repo: local\n'
                                       '    hooks:\n'
                                       '      - id: one\n')
        self.assertEqual(scanned.sections['default_language_version'], {'node': 'lts'})
        self.assertNotIn('repos', scanned.sections)

    def test_a_section_after_repos(self):
        scanned = scan_pre_commit_yaml('repos:\n'
                                       '  - repo: local\n'
                                       '    hooks:\n'
                                       '      - id: one\n'
                                       'default_language_version:\n'
                                       '  node: lts\n')
        self.assertEqual(scanned.sections['default_language_version'], {'node': 'lts'})
        self.assertEqual(hook_ids(scanned), ['one'])

    def test_a_comment_after_a_section_key(self):
        scanned = scan_pre_commit_yaml('default_language_version:  # pinned\n'
                                       '  node: lts  # and this\n')
        self.assertEqual(scanned.top['default_language_version'], [''])
        self.assertEqual(scanned.sections['default_language_version'], {'node': 'lts'})

    def test_a_comment_as_a_hook_value(self):
        scanned = scan_pre_commit_yaml('- id: one\n'
                                       '  language_version:   # todo\n'
                                       '  entry: run#this\n')
        hook = scanned.hooks()[0]
        self.assertEqual(item_value(hook, 'language_version'), '')
        self.assertEqual(item_value(hook, 'entry'), 'run#this')

    def test_a_flow_mapping_section(self):
        scanned = scan_pre_commit_yaml('default_language_version: {python: "python3", node: lts}  # one line\n')
        self.assertEqual(scanned.sections['default_language_version'], {'python': 'python3', 'node': 'lts'})

    def test_flow_mappings_that_are_not_read(self):
        for value in ('{node: [lts]}', '{node: {a: b}}', '{node}', "{node: 'lts}", "{node: lts', python: x}", 'lts'):
            with self.subTest(value=value):
                scanned = scan_pre_commit_yaml(f'default_language_version: {value}\n')
                self.assertNotIn('default_language_version', scanned.sections)

    def test_a_flow_mapping_quotes_its_commas_and_brackets(self):
        scanned = scan_pre_commit_yaml("default_language_version: {node: 'a, [b]', python: x,}\n")
        self.assertEqual(scanned.sections['default_language_version'], {'node': 'a, [b]', 'python': 'x'})

    def test_quoted_scalars(self):
        scanned = scan_pre_commit_yaml('- id: one\n'
                                       "  language_version: 'it''s'  # c\n"
                                       '  entry: "say \\"hi\\""\n')
        hook = scanned.hooks()[0]
        self.assertEqual(item_value(hook, 'language_version'), "it's")
        self.assertEqual(item_value(hook, 'entry'), 'say "hi"')

    def test_a_block_scalar_holds_text_not_keys(self):
        scanned = scan_pre_commit_yaml('- id: one\n'
                                       '  entry: |\n'
                                       '    language: python\n'
                                       '    second line\n'
                                       '  language: node\n')
        hook = scanned.hooks()[0]
        self.assertEqual(item_value(hook, 'entry'), 'language: python\nsecond line\n')
        self.assertEqual(hook['keys']['language'], ['node'])


class ScanPreCommitConfigTest(unittest.TestCase):

    def test_top_level_and_hook_excludes_are_apart(self):
        top, hooks = scan_pre_commit_config('exclude: ^vendor/\n'
                                            'repos:\n'
                                            '  - repo: local\n'
                                            '    hooks:\n'
                                            '      - id: one\n'
                                            '        exclude: ^docs/\n'
                                            '      - id: two\n')
        self.assertEqual(top, ['^vendor/'])
        self.assertEqual(hooks, [('one', ['^docs/']), ('two', [])])


if __name__ == '__main__':
    unittest.main()
