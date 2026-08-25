#!/usr/bin/env python3

"""Tests for the marker extraction in the issue-fix template.

The template asks the model for two marker delimited blocks on stdout --
a commit message and a pull request description -- and publishes both.
The commit message is pushed and its first line becomes the pull request
title, so a parsing mistake here is not cosmetic: it lands in git history
and in a description a human is meant to read before the diff.

Neither actionlint nor shellcheck sees anything wrong with an extraction
which silently mangles its input, so the marker semantics are pinned
here instead. The extraction lives in
templates/issue-fix/extract-model-block.sh, which these tests run
directly; an earlier version was awk embedded in the workflow YAML and
had to be lifted back out with a regular expression to be testable at
all, which is most of the reason it now has a file of its own.

The second class covers templates/issue-fix/neutralise-pr-body.sh,
which defuses the constructs GitHub acts on in a published body. Two
tests guard the seams between all of this and the workflow: a script
nothing calls is not an extraction, so the workflow must still invoke
the extractor for both block names and the neutraliser for the
description alone.

Run with: python3 scripts/test_issue_fix_extraction.py
"""

import os
import subprocess
import tempfile
import unittest


TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'templates', 'issue-fix'
)
SCRIPT = os.path.join(TEMPLATE_DIR, 'extract-model-block.sh')
NEUTRALISE = os.path.join(TEMPLATE_DIR, 'neutralise-pr-body.sh')
WORKFLOW = os.path.join(TEMPLATE_DIR, 'issue-fix.yml')


class ExtractionTest(unittest.TestCase):
    def extract(self, transcript, block='PR_DESCRIPTION'):
        """Run the extraction over a transcript, as the workflow does.

        Returns (body, found), where found is the script's exit status
        as the workflow reads it.
        """
        with tempfile.TemporaryDirectory() as tempdir:
            source = os.path.join(tempdir, 'claude-output.txt')
            extracted = os.path.join(tempdir, 'block.txt')
            with open(source, 'w') as f:
                f.write(transcript)

            proc = subprocess.run(
                [SCRIPT, block, source, extracted],
                capture_output=True, text=True)

            # The output file is always created, so the workflow can
            # upload it as a build artifact without special casing the
            # failure.
            self.assertTrue(os.path.exists(extracted),
                            'output file was not created')
            with open(extracted) as f:
                return f.read(), proc.returncode == 0

    def test_the_script_is_executable(self):
        # The workflow copies it and runs it directly rather than via
        # bash, and git records the mode.
        self.assertTrue(os.access(SCRIPT, os.X_OK))

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

    def test_both_blocks_come_out_of_one_transcript(self):
        transcript = (
            'COMMIT_SUMMARY_START\n'
            'Fix the thing.\n\nBody line.\n'
            'COMMIT_SUMMARY_END\n'
            'PR_DESCRIPTION_START\n'
            '## Summary\n'
            'PR_DESCRIPTION_END\n'
        )
        body, found = self.extract(transcript, block='COMMIT_SUMMARY')
        self.assertTrue(found)
        self.assertEqual(body, 'Fix the thing.\n\nBody line.\n')
        body, found = self.extract(transcript)
        self.assertTrue(found)
        self.assertEqual(body, '## Summary\n')

    def test_fenced_code_in_the_description_survives(self):
        # A PR body may legitimately contain fenced code, which is why
        # fences are never stripped globally.
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            '## What changed\n\n'
            '```python\nx = 1\n```\n\n'
            'And that is all.\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(
            body,
            '## What changed\n\n```python\nx = 1\n```\n\nAnd that is all.\n')

    def test_a_fence_wrapping_the_whole_block_is_stripped(self):
        # The prompt illustrates both blocks inside fences while telling
        # the model not to use them. A model which copies the
        # illustration would otherwise have its entire description
        # rendered as one preformatted lump. A block which is nothing
        # but a fenced slab is not a description worth preserving, so
        # the cost of being wrong here is negligible.
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            '```\n## Summary\n\nFixed it.\n```\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, '## Summary\n\nFixed it.\n')

    def test_a_fence_with_a_language_is_stripped_too(self):
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            '```markdown\n## Summary\n```\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, '## Summary\n')

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

    def test_an_unclosed_commit_summary_does_not_swallow_the_description(self):
        # Before both paths required a closing marker, this published
        # the description as the commit message, and its first line as
        # the pull request title.
        body, found = self.extract(
            'COMMIT_SUMMARY_START\n'
            'Fix the thing.\n\nBody line.\n'
            'PR_DESCRIPTION_START\n'
            '## Summary\n'
            'PR_DESCRIPTION_END\n',
            block='COMMIT_SUMMARY'
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

    def test_a_marker_beginning_a_line_of_prose_does_not_close(self):
        # Stricter than an anchored match: only a line which is nothing
        # but the marker terminates the block. A fix to this very
        # template would plausibly start a sentence with the token.
        body, found = self.extract(
            'PR_DESCRIPTION_START\n'
            'PR_DESCRIPTION_END is what terminates the block.\n'
            'This line must survive.\n'
            'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(
            body,
            'PR_DESCRIPTION_END is what terminates the block.\n'
            'This line must survive.\n'
        )

    def test_indented_markers_are_still_markers(self):
        # Models indent things, and trailing whitespace on a marker line
        # is the same hazard handled by the same trim.
        body, found = self.extract(
            '  PR_DESCRIPTION_START  \n'
            'The real body.\n'
            '\tPR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, 'The real body.\n')

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

    def test_blank_lines_around_the_body_are_trimmed(self):
        body, found = self.extract(
            'PR_DESCRIPTION_START\n\n\n## Summary\n\n\nPR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, '## Summary\n')

    def test_no_markers_at_all_yields_nothing(self):
        body, found = self.extract('The model just wandered off.\n')
        self.assertFalse(found)
        self.assertEqual(body, '')

    def test_shell_metacharacters_are_carried_through_verbatim(self):
        # They are inert because the body reaches gh via --body-file,
        # but the extraction must not evaluate or mangle them either.
        payload = 'Body with $(touch /tmp/PWNED) and `id` in it.\n'
        body, found = self.extract(
            'PR_DESCRIPTION_START\n' + payload + 'PR_DESCRIPTION_END\n'
        )
        self.assertTrue(found)
        self.assertEqual(body, payload)
        self.assertFalse(os.path.exists('/tmp/PWNED'))

    def test_a_missing_input_file_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tempdir:
            extracted = os.path.join(tempdir, 'block.txt')
            proc = subprocess.run(
                [SCRIPT, 'PR_DESCRIPTION',
                 os.path.join(tempdir, 'nope.txt'), extracted],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(os.path.exists(extracted))

    def test_the_workflow_neutralises_the_description(self):
        # The neutraliser must run on the description and only on the
        # description: a commit message is not published anywhere
        # GitHub acts on a mention, and the workflow appends its own
        # "Fixes #NNNN" after this pass.
        with open(WORKFLOW) as f:
            workflow = f.read()
        self.assertIn('cp tools/neutralise-pr-body.sh', workflow)
        self.assertIn(
            'neutralise-pr-body.sh \\\n              '
            '${{ runner.temp }}/pr-description.txt', workflow)
        self.assertNotIn('neutralise-pr-body.sh ${{ runner.temp }}/'
                         'commit-summary.txt', workflow)

    def test_the_workflow_calls_the_script_for_both_blocks(self):
        # A tested script the workflow does not call is not an
        # extraction. This is the seam the regex-lifting version of
        # these tests used to cover implicitly.
        with open(WORKFLOW) as f:
            workflow = f.read()

        self.assertIn('cp tools/extract-model-block.sh', workflow)
        for block in ('COMMIT_SUMMARY', 'PR_DESCRIPTION'):
            self.assertIn(
                'extract-model-block.sh %s' % block, workflow,
                'the workflow no longer extracts %s with the tested '
                'script. If the extraction was rewritten, rewrite these '
                'tests against it rather than deleting them.' % block
            )


class NeutralisationTest(unittest.TestCase):
    """Tests for templates/issue-fix/neutralise-pr-body.sh.

    Two things GitHub finds in a published PR body it acts on rather
    than renders: an @mention notifies a real person the instant
    `gh pr create` runs -- before any human has looked at the draft,
    and a notification cannot be taken back -- and an issue-closing
    keyword closes an unrelated issue on merge. The prompt forbids
    both, and the prompt is not enough for a side effect which fires
    automatically and is irreversible.

    The tests worth reading are the ones pinning what it must NOT
    touch. A description quoting a decorator, an email address or a
    path is a normal description, and mangling it to defuse a hazard
    which is not there is its own defect.
    """

    def _neutralise(self, body):
        with tempfile.TemporaryDirectory() as tempdir:
            path = os.path.join(tempdir, 'pr-description.txt')
            with open(path, 'w') as f:
                f.write(body)

            proc = subprocess.run(
                [NEUTRALISE, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)

            with open(path) as f:
                return f.read()

    def test_the_script_is_executable(self):
        self.assertTrue(os.access(NEUTRALISE, os.X_OK))

    def test_a_mention_loses_its_at_sign(self):
        # Which is what the prompt asks the model to do itself.
        self.assertEqual(
            'Thanks to someone for the diagnosis.\n',
            self._neutralise('Thanks to @someone for the diagnosis.\n'))

    def test_a_team_mention_loses_its_at_sign(self):
        self.assertEqual(
            'Raised by an-org/a-team.\n',
            self._neutralise('Raised by @an-org/a-team.\n'))

    def test_a_mention_at_the_start_of_a_line_is_caught(self):
        self.assertEqual(
            'someone asked for this.\n',
            self._neutralise('@someone asked for this.\n'))

    def test_an_email_address_is_untouched(self):
        # The character before the @ is what distinguishes the two.
        self.assertEqual(
            'Reported by foo@example.com in passing.\n',
            self._neutralise('Reported by foo@example.com in passing.\n'))

    def test_a_closing_keyword_is_separated_from_its_reference(self):
        # "Fixes issue #12" is a citation; GitHub only closes when the
        # reference immediately follows the keyword.
        self.assertEqual(
            'Fixes issue #12 as a side effect.\n',
            self._neutralise('Fixes #12 as a side effect.\n'))

    def test_every_closing_keyword_inflection_is_caught(self):
        for keyword in ('Fix', 'Fixes', 'Fixed', 'Close', 'Closes',
                        'Closed', 'Resolve', 'Resolves', 'Resolved',
                        'fix', 'fixes', 'closes', 'resolved'):
            self.assertEqual(
                '%s issue #12\n' % keyword,
                self._neutralise('%s #12\n' % keyword),
                'keyword %s was not defused' % keyword)

    def test_a_cross_repository_reference_keeps_its_repository(self):
        self.assertEqual(
            'Closes issue shakenfist/other#34.\n',
            self._neutralise('Closes shakenfist/other#34.\n'))

    def test_a_url_reference_is_defused(self):
        self.assertEqual(
            'This resolves issue https://github.com/o/r/issues/9 too.\n',
            self._neutralise(
                'This resolves https://github.com/o/r/issues/9 too.\n'))

    def test_a_keyword_not_followed_by_a_reference_is_prose(self):
        # Rewriting this would mangle ordinary English.
        self.assertEqual(
            'It fixes a typo, and closes the gap in coverage.\n',
            self._neutralise(
                'It fixes a typo, and closes the gap in coverage.\n'))

    def test_a_bare_issue_reference_still_links(self):
        # A citation without a keyword does not close anything, and the
        # link is useful.
        self.assertEqual(
            'See #77 for the original report.\n',
            self._neutralise('See #77 for the original report.\n'))

    def test_fenced_code_is_untouched(self):
        # GitHub does not linkify inside a fence, so there is nothing to
        # defuse, and a quoted decorator must survive intact.
        body = (
            'Before.\n'
            '\n'
            '```python\n'
            '@property\n'
            'def x(self):  # Fixes #99\n'
            '    pass\n'
            '```\n'
            '\n'
            'After.\n')
        self.assertEqual(body, self._neutralise(body))

    def test_text_after_a_fence_closes_is_defused_again(self):
        # The toggle has to survive the block, or everything after the
        # first fenced example goes unprotected.
        self.assertEqual(
            '```\n@property\n```\nThanks someone.\n',
            self._neutralise('```\n@property\n```\nThanks @someone.\n'))

    def test_several_hazards_on_one_line(self):
        self.assertEqual(
            'Thanks someone and someoneelse; fixes issue #1 and '
            'closes issue #2.\n',
            self._neutralise(
                'Thanks @someone and @someoneelse; fixes #1 and '
                'closes #2.\n'))

    def test_an_ordinary_description_is_returned_unchanged(self):
        body = (
            '## What was wrong\n'
            '\n'
            'The extraction used a sed address range, which re-matches.\n'
            '\n'
            '## What I did not do\n'
            '\n'
            'The publish step is still untested shell.\n')
        self.assertEqual(body, self._neutralise(body))

    def test_a_missing_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tempdir:
            proc = subprocess.run(
                [NEUTRALISE, os.path.join(tempdir, 'nope.txt')],
                capture_output=True, text=True)
            self.assertEqual(1, proc.returncode)

    def test_wrong_argument_count_is_a_usage_error(self):
        proc = subprocess.run([NEUTRALISE], capture_output=True, text=True)
        self.assertEqual(2, proc.returncode)
        self.assertIn('usage:', proc.stderr)


if __name__ == '__main__':
    unittest.main()
