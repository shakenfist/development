#!/usr/bin/env python3

"""Tests for audit/text/markdown.py.

Run with: python3 scripts/tests/test_markdown.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.text.markdown import (  # noqa: E402
    iter_lines_outside_fences, iter_markdown_headings,
    iter_markdown_table_rows, markdown_heading, strip_markdown_code,
)


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


class IterLinesOutsideFencesTest(unittest.TestCase):
    """The one fence loop the structure readers share."""

    def test_offsets_survive_a_fence(self):
        # Blanked rather than dropped, so an offset is still an index
        # into the caller's own list of lines.
        lines = ['a', '```', 'b', '```', 'c']
        self.assertEqual(
            list(iter_lines_outside_fences(lines)),
            [(0, 'a'), (1, ''), (2, ''), (3, ''), (4, 'c')],
        )

    def test_a_fence_is_closed_only_by_its_own_marker(self):
        lines = ['~~~', '```', '~~~', 'after']
        self.assertEqual(
            [line for _, line in iter_lines_outside_fences(lines)],
            ['', '', '', 'after'],
        )

    def test_an_unterminated_fence_blanks_the_rest(self):
        # A malformed file reads as having no structure after the
        # stray marker, rather than structure invented from its code.
        lines = ['a', '```', '## Not a heading']
        self.assertEqual(
            [line for _, line in iter_lines_outside_fences(lines)],
            ['a', '', ''],
        )


class MarkdownHeadingTest(unittest.TestCase):
    def test_reads_level_and_text(self):
        self.assertEqual(markdown_heading('### Phase 5'), (3, 'Phase 5'))

    def test_requires_whitespace_after_the_hashes(self):
        self.assertIsNone(markdown_heading('###nope'))
        self.assertIsNone(markdown_heading('###'))

    def test_a_fenced_heading_is_not_a_heading(self):
        content = '## Real\n\n```\n## Sample\n```\n'
        self.assertEqual(
            [text for _, text, _ in iter_markdown_headings(content)],
            ['Real'],
        )


class IterMarkdownTableRowsTest(unittest.TestCase):
    """Header detection, and the fence handling underneath it."""

    def _rows(self, text):
        return list(iter_markdown_table_rows(text.splitlines()))

    def test_header_is_the_row_the_separator_underlines(self):
        rows = self._rows(
            '| Date | Plan |\n'
            '|------|------|\n'
            '| 2026-01-01 | One |\n'
        )
        # The separator is consumed, so two records come back.
        self.assertEqual([r[2] for r in rows], [True, False])
        self.assertEqual(rows[0][3], ['date', 'plan'])
        self.assertEqual(rows[1][3], ['date', 'plan'])
        self.assertEqual(rows[1][4], ['2026-01-01', 'One'])

    def test_prose_ends_the_run_of_rows(self):
        rows = self._rows(
            '| Date | Plan |\n'
            '|------|------|\n'
            '| 2026-01-01 | One |\n'
            '\n'
            '| 2026-01-02 | Two |\n'
        )
        self.assertIsNone(rows[2][4])
        self.assertIsNone(rows[3][3])

    def test_a_fenced_table_is_not_a_table(self):
        rows = self._rows(
            'before\n'
            '```markdown\n'
            '| Phase | Status |\n'
            '|-------|--------|\n'
            '| 1 | Complete |\n'
            '```\n'
        )
        self.assertTrue(all(r[4] is None for r in rows))
        self.assertTrue(all(r[1] == '' for r in rows[1:]))

    def test_a_fence_breaks_a_run_of_rows(self):
        # A table either side of an example is two tables, so the
        # second is never read against the first one's header.
        rows = self._rows(
            '| Date | Plan |\n'
            '|------|------|\n'
            '| 2026-01-01 | One |\n'
            '```\n'
            'x\n'
            '```\n'
            '| 2026-01-02 | Two |\n'
        )
        self.assertEqual(rows[1][3], ['date', 'plan'])
        self.assertIsNone(rows[-1][3])

    def test_columns_callable_normalises_the_header(self):
        rows = self._rows_with_columns(
            '| **Date** | `Plan` |\n'
            '|------|------|\n'
            '| 2026-01-01 | One |\n'
        )
        self.assertEqual(rows[0][3], ['date', 'plan'])

    def _rows_with_columns(self, text):
        return list(iter_markdown_table_rows(
            text.splitlines(),
            columns=lambda cells: [
                c.strip().strip('*`').lower() for c in cells
            ],
        ))


if __name__ == '__main__':
    unittest.main()
