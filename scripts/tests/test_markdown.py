#!/usr/bin/env python3

"""Tests for audit/text/markdown.py.

Run with: python3 scripts/tests/test_markdown.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.text.markdown import strip_markdown_code  # noqa: E402


class StripMarkdownCodeTest(unittest.TestCase):
    def test_strips_fenced_blocks(self):
        stripped = strip_markdown_code(
            'before\n```\n[x](y)\n```\nafter\n'
        )
        self.assertNotIn('[x](y)', stripped)
        self.assertIn('before', stripped)
        self.assertIn('after', stripped)

    def test_strips_inline_span(self):
        self.assertNotIn(
            '[x](y)', strip_markdown_code('see `[x](y)` here')
        )

    def test_strips_span_wrapped_across_lines(self):
        # Prose wrapped at 65 columns splits code spans all the time.
        stripped = strip_markdown_code(
            'the guard read `if a.shared and requestor not in\n'
            "[a.namespace, 'system']: 404`, which is inverted\n"
        )
        self.assertNotIn('[a.namespace', stripped)
        self.assertIn('which is inverted', stripped)

    def test_unpaired_backtick_does_not_swallow_later_paragraphs(self):
        stripped = strip_markdown_code(
            'a stray ` backtick\n\n[real](../README.md)\n'
        )
        self.assertIn('[real](../README.md)', stripped)


if __name__ == '__main__':
    unittest.main()
