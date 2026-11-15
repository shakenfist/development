#!/usr/bin/env python3

"""Tests for tests/base.py, the shared machinery of the check suites.

Run with: python3 scripts/tests/test_base.py

Only `current_ref` and `uses_clause` are covered here, because they
are the helpers whose failure is silent: a suite that substitutes a
reference it could not find still runs, still passes, and measures
nothing. The rest of base.py fails loudly in whatever suite uses it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import RefNotFound, current_ref, uses_clause  # noqa: E402

UNPINNED = (
    '      - name: Checkout\n'
    '        uses: actions/checkout@v7\n'
    '      - uses: dtolnay/rust-toolchain@stable\n'
)

PINNED = (
    '      - name: Checkout\n'
    '        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7\n'
    '      - uses: dtolnay/rust-toolchain@b725eda525362e7cf34af9a7243cac1e3f01a7ec # stable\n'
)


class CurrentRefTest(unittest.TestCase):
    """The reference an action carries, whichever form the file is in."""

    def test_a_tag_is_returned_whole(self):
        self.assertEqual(current_ref(UNPINNED, 'actions/checkout'),
                         'actions/checkout@v7')

    def test_a_branch_ref_is_returned_whole(self):
        self.assertEqual(current_ref(UNPINNED, 'dtolnay/rust-toolchain'),
                         'dtolnay/rust-toolchain@stable')

    def test_a_pinned_ref_keeps_the_sha_and_drops_the_comment(self):
        # The comment is Renovate's note of the ref it replaced. The
        # caller substitutes the reference, so the comment is not part
        # of it -- but the sha is, and a helper that returned the
        # action alone would make every caller's replacement a rewrite
        # of the name with the old ref left behind.
        self.assertEqual(
            current_ref(PINNED, 'actions/checkout'),
            'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1')
        self.assertEqual(
            current_ref(PINNED, 'dtolnay/rust-toolchain'),
            'dtolnay/rust-toolchain@b725eda525362e7cf34af9a7243cac1e3f01a7ec')

    def test_the_ref_is_always_present(self):
        for text in (UNPINNED, PINNED):
            self.assertIn('@', current_ref(text, 'actions/checkout'))

    def test_the_error_is_an_assertion_error(self):
        # Documented in base.py and relied on by any suite that catches
        # AssertionError without having heard of RefNotFound.
        self.assertTrue(issubclass(RefNotFound, AssertionError))

    def test_a_missing_action_raises(self):
        # The point of the helper: returning None or the bare action
        # would let a caller substitute nothing and still pass.
        for text in (UNPINNED, PINNED):
            with self.assertRaises(RefNotFound):
                current_ref(text, 'actions/upload-artifact')

    def test_the_first_occurrence_wins(self):
        two = UNPINNED + '        uses: actions/checkout@v6\n'
        self.assertEqual(current_ref(two, 'actions/checkout'),
                         'actions/checkout@v7')

    def test_a_sub_path_action_is_found(self):
        text = '        uses: github/codeql-action/init@v4\n'
        self.assertEqual(current_ref(text, 'github/codeql-action/init'),
                         'github/codeql-action/init@v4')

    def test_the_action_is_escaped_before_it_becomes_a_pattern(self):
        # Unescaped, the dot matches the hyphen and the helper would
        # hand back setup-node's reference for an action that is not in
        # the file at all. Delete the re.escape() in base.py and this
        # is the case that fails.
        text = '        uses: actions/setup-node@v7\n'
        self.assertEqual(current_ref(text, 'actions/setup-node'),
                         'actions/setup-node@v7')
        with self.assertRaises(RefNotFound):
            current_ref(text, 'actions/setup.node')

    def test_a_commented_out_step_is_not_a_uses_line(self):
        # templates/release-automation-rust/release.yml carries one of
        # these. Matching it would mean a test mutating a comment and
        # passing, which is what the helper exists to prevent.
        text = ('      # - name: Check the wheel contents\n'
                '      #   uses: shakenfist/actions/check-dist@main\n')
        with self.assertRaises(RefNotFound):
            current_ref(text, 'shakenfist/actions/check-dist')

    def test_a_tab_or_extra_space_after_uses_is_found(self):
        for text in ('        uses:\tactions/checkout@v7\n',
                     '        uses:   actions/checkout@v7\n',
                     '      - uses: actions/checkout@v7\n'):
            self.assertEqual(current_ref(text, 'actions/checkout'),
                             'actions/checkout@v7')

    def test_a_quoted_ref_is_found_without_its_quotes(self):
        for quote in ("'", '"'):
            text = f'        uses: {quote}actions/checkout@v7{quote}\n'
            self.assertEqual(current_ref(text, 'actions/checkout'),
                             'actions/checkout@v7')

    def test_the_clause_carries_the_trailing_comment(self):
        self.assertEqual(
            uses_clause(PINNED, 'actions/checkout'),
            'uses: actions/checkout'
            '@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7')

    def test_the_clause_of_an_unpinned_ref_has_no_comment(self):
        self.assertEqual(uses_clause(UNPINNED, 'actions/checkout'),
                         'uses: actions/checkout@v7')

    def test_the_clause_drops_the_list_dash_and_indentation(self):
        text = '      - uses: actions/checkout@v7 # v7\n'
        self.assertEqual(uses_clause(text, 'actions/checkout'),
                         'uses: actions/checkout@v7 # v7')

    def test_a_missing_action_has_no_clause(self):
        with self.assertRaises(RefNotFound):
            uses_clause(UNPINNED, 'actions/upload-artifact')


if __name__ == '__main__':
    unittest.main()
