#!/usr/bin/env python3

"""Tests for the PR description extraction in the issue-fix template.

The extraction is a few lines of awk embedded in
templates/issue-fix/issue-fix.yml. actionlint and shellcheck both pass
over an extraction which silently mangles its input, so the marker
semantics are pinned here instead: the program is lifted out of the
template and run against the shapes a model transcript can actually
take.

Run with: python3 scripts/test_issue_fix_extraction.py
"""

import os
import re
import subprocess
import unittest


TEMPLATE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'templates', 'issue-fix', 'issue-fix.yml'
)

# The awk invocation in the extract step, as a single-quoted program
# spanning several lines. Anchored on the marker name so an unrelated
# awk elsewhere in the template cannot be picked up by accident.
AWK_RE = re.compile(
    r"awk '\n(.*?PR_DESCRIPTION_START.*?)\n\s*' \$\{\{ runner\.temp \}\}",
    re.DOTALL
)


def awk_program():
    """Lift the extraction program out of the workflow template."""
    with open(TEMPLATE) as f:
        template = f.read()

    match = AWK_RE.search(template)
    if not match:
        raise AssertionError(
            'No PR description awk program found in %s. If the '
            'extraction was rewritten in another language, rewrite '
            'these tests against it rather than deleting them.'
            % TEMPLATE
        )
    return match.group(1)


class ExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = awk_program()

    def extract(self, transcript):
        """Run the extraction over a transcript, as the workflow does.

        Returns (description, description_found), where the second
        value mirrors the workflow's whitespace-only test.
        """
        result = subprocess.run(
            ['awk', self.program], input=transcript,
            capture_output=True, text=True, check=True
        )
        return result.stdout, bool(result.stdout.strip())

    def test_a_block_is_extracted_without_its_markers(self):
        body, found = self.extract(
            'Narration.\n'
            'PR_DESCRIPTION_START\n'
            '## Summary\n\nFixed it.\n'
            'PR_DESCRIPTION_END\n'
            'More narration.\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, '## Summary\n\nFixed it.\n')

    def test_fenced_code_in_the_description_survives(self):
        # Unlike the commit summary path, which strips fences, a PR
        # body may legitimately contain them.
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            '```python\nx = 1\n```\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, '```python\nx = 1\n```\n')

    def test_only_the_first_of_two_blocks_is_taken(self):
        # A sed address range re-matches, concatenating both blocks
        # and leaving the interior markers in the published body.
        body, found = self.extract(
            'PR_DESCRIPTION_START\nfirst\nPR_DESCRIPTION_END\n'
            'On reflection, let me redo that.\n'
            'PR_DESCRIPTION_START\nsecond\nPR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, 'first\n')
        self.assertNotIn('PR_DESCRIPTION_', body)

    def test_a_repeated_start_restarts_rather_than_embedding(self):
        # A START inside an open block is a marker, not prose. Taking
        # the last one loses the earlier text, but the alternative is
        # a literal PR_DESCRIPTION_START line in the published body.
        body, found = self.extract(
            'PR_DESCRIPTION_START\nfalse start\n'
            'PR_DESCRIPTION_START\nthe real body\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, 'the real body\n')
        self.assertNotIn('PR_DESCRIPTION_', body)

    def test_an_unclosed_block_yields_nothing(self):
        # A truncated run must not publish the tail of the transcript.
        body, found = self.extract(
            'PR_DESCRIPTION_START\nhalf a description and then\n'
        )
        self.assertFalse(found)
        self.assertEqual(body, '')

    def test_an_end_marker_before_any_start_does_not_close(self):
        body, found = self.extract(
            'PR_DESCRIPTION_END\n'
            'PR_DESCRIPTION_START\n'
            'transcript which must not become a PR body\n'
        )
        self.assertFalse(found)
        self.assertEqual(body, '')

    def test_a_stray_end_marker_does_not_lose_a_later_block(self):
        body, found = self.extract(
            'PR_DESCRIPTION_END\n'
            'PR_DESCRIPTION_START\nthe real one\nPR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, 'the real one\n')

    def test_a_start_marker_named_mid_line_does_not_restart(self):
        # A description of this template is the obvious case: it has
        # to be able to name its own markers in a sentence.
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            'The prompt asks for a PR_DESCRIPTION_START block.\n'
            'That sentence is prose, not a marker.\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(
            body,
            'The prompt asks for a PR_DESCRIPTION_START block.\n'
            'That sentence is prose, not a marker.\n'
        )

    def test_an_end_marker_named_mid_line_does_not_close(self):
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            'The block is terminated by PR_DESCRIPTION_END.\n'
            'This line must survive.\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(
            body,
            'The block is terminated by PR_DESCRIPTION_END.\n'
            'This line must survive.\n'
        )

    def test_an_empty_block_is_not_a_description(self):
        body, found = self.extract(
            'PR_DESCRIPTION_START\nPR_DESCRIPTION_END\n'
        )
        self.assertFalse(found)
        self.assertEqual(body, '')

    def test_a_whitespace_only_block_is_not_a_description(self):
        _, found = self.extract(
            'PR_DESCRIPTION_START\n\n   \n\t\nPR_DESCRIPTION_END\n'
        )
        self.assertFalse(found)

    def test_no_markers_at_all_yields_nothing(self):
        body, found = self.extract('The model just wandered off.\n')
        self.assertFalse(found)
        self.assertEqual(body, '')

    def test_shell_metacharacters_are_carried_through_verbatim(self):
        # They are inert because the body reaches gh via --body-file,
        # but the extraction must not mangle them either.
        payload = 'Body with $(touch /tmp/PWNED) and `id` in it.\n'
        body, found = self.extract(
            'PR_DESCRIPTION_START\n' + payload + 'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, payload)


if __name__ == '__main__':
    unittest.main()
