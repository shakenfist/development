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
    iter_markdown_table_rows, markdown_heading, markdown_table_cells,
    strip_markdown_code,
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

    def test_strips_a_closing_hash_sequence(self):
        """Closing hashes are syntax, so every reader drops them.

        They used to be left on, with the two readers in this module
        disagreeing: iter_markdown_headings stripped them and
        markdown_heading did not, so a plan headed "## Push audit ##"
        was reported for lacking a heading it plainly has.
        """
        self.assertEqual(markdown_heading('## Push audit ##'),
                         (2, 'Push audit'))
        self.assertEqual(markdown_heading('## Push audit   ###  '),
                         (2, 'Push audit'))
        self.assertEqual(
            [text for _, text, _ in iter_markdown_headings('## Real ##\n')],
            ['Real'],
        )

    def test_hashes_that_are_not_a_closing_sequence_are_kept(self):
        """CommonMark requires whitespace before a closing sequence.

        Without that rule a heading naming a channel or an anchor
        would be silently truncated, which is the mirror image of the
        bug the stripping fixes.
        """
        self.assertEqual(markdown_heading('## C#'), (2, 'C#'))
        self.assertEqual(markdown_heading('## See #4 ##'), (2, 'See #4'))

    def test_a_fenced_heading_is_not_a_heading(self):
        content = '## Real\n\n```\n## Sample\n```\n'
        self.assertEqual(
            [text for _, text, _ in iter_markdown_headings(content)],
            ['Real'],
        )


class MarkdownTableCellsTest(unittest.TestCase):
    """Direct cover for the split every table row goes through.

    It had only the indirect cover of IterMarkdownTableRowsTest, which
    exercises it exclusively through well-formed rows -- so nothing
    pinned what it does to the ragged ones a repository can actually
    write.
    """

    def test_trims_the_outer_pipes_and_the_whitespace(self):
        self.assertEqual(
            markdown_table_cells('| Phase |  Status  |'),
            ['Phase', 'Status'])

    def test_a_row_without_outer_pipes_still_splits(self):
        self.assertEqual(
            markdown_table_cells('Phase | Status'), ['Phase', 'Status'])

    def test_an_empty_cell_is_kept(self):
        self.assertEqual(
            markdown_table_cells('| 5 | Complete | |'),
            ['5', 'Complete', ''])

    def test_leading_and_trailing_space_outside_the_pipes(self):
        self.assertEqual(
            markdown_table_cells('   | a | b |   '), ['a', 'b'])

    def test_a_bare_pipe_pair_is_one_empty_cell(self):
        # Both pipes are outer, so stripping them leaves nothing to
        # split: one empty cell rather than two.
        self.assertEqual(markdown_table_cells('||'), [''])


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

    def test_a_row_after_a_table_belongs_to_that_table(self):
        """Only a non-row ends a table, which is what the renderer does.

        A `|` line directly under a table, with no blank line between,
        is another data row of it -- there is no second table until
        something that is not a row intervenes. The push audit of
        PLAN-push-audit-phase.md raised the carried-over header as a
        possible misattribution; it is not one, because GitHub renders
        the same two lines as one table. Pinned here so the next
        reader does not "fix" the parser away from the renderer.
        """
        doc = [
            '| Phase | Status |',
            '|---|---|',
            '| 1 | Complete |',
            '| stray | row |',
            '',
            '| Other | Table |',
            '|---|---|',
            '| x | y |',
        ]
        rows = [(h, c) for _o, _l, _i, h, c
                in iter_markdown_table_rows(doc) if c]
        self.assertEqual(rows[1][0], ['phase', 'status'])
        self.assertEqual(rows[2][0], ['phase', 'status'])
        self.assertEqual(rows[2][1], ['stray', 'row'])
        # The blank line is the boundary, so the second table is read
        # against its own header rather than the first one's.
        self.assertEqual(rows[3][0], ['other', 'table'])
        self.assertEqual(rows[4][0], ['other', 'table'])

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
