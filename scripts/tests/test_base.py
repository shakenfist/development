#!/usr/bin/env python3

"""Tests for tests/base.py, the shared machinery of the check suites.

Run with: python3 scripts/tests/test_base.py

Only `current_ref` is covered here, because it is the one helper whose
failure is silent: a suite that substitutes a reference it could not
find still runs, still passes, and measures nothing. The rest of
base.py fails loudly in whatever suite uses it.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import current_ref  # noqa: E402

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

    def test_a_missing_action_raises(self):
        # The point of the helper: returning None or the bare action
        # would let a caller substitute nothing and still pass.
        for text in (UNPINNED, PINNED):
            with self.assertRaises(AssertionError):
                current_ref(text, 'actions/upload-artifact')

    def test_the_first_occurrence_wins(self):
        two = UNPINNED + '        uses: actions/checkout@v6\n'
        self.assertEqual(current_ref(two, 'actions/checkout'),
                         'actions/checkout@v7')

    def test_a_dotted_action_name_is_not_a_wildcard(self):
        # The action is escaped before it becomes a pattern, so the dot
        # in a name matches a dot rather than any character.
        text = '        uses: github/codeql-action/init@v4\n'
        self.assertEqual(current_ref(text, 'github/codeql-action/init'),
                         'github/codeql-action/init@v4')
        with self.assertRaises(AssertionError):
            current_ref(text, 'github/codeql-action/inXt')


if __name__ == '__main__':
    unittest.main()
