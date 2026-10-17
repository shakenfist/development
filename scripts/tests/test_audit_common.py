"""Tests for audit_common.py, the shared half of the audit scripts.

defuse() lives here rather than beside either caller because both
publish the same harvested string: audit-update-docs.py into the
generated compliance page, and audit-manage-issues.py into the body
of an issue filed on the audited repository. Kept beside either
publisher, it is easy to apply to one and forget the other, so these
tests sit with the function rather than with either publisher.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from audit_common import (  # noqa: E402
    BEGIN_MARKER, END_MARKER, ISSUE_TITLES, defuse, issue_title_for,
)


class DefuseTest(unittest.TestCase):
    """What a detail string loses before it is published.

    A detail string is whatever a check found in another repository.
    Published as-is it could carry a newline and emit a heading or a
    table row -- in the page, or in an issue body -- or carry the end
    marker and truncate the next run's splice.
    """

    def test_newlines_are_collapsed(self):
        self.assertEqual('a b c', defuse('a\nb\n\n  c  '))

    def test_a_traceback_becomes_one_line(self):
        # The accidental route: a check that puts subprocess stderr
        # into its details.
        self.assertEqual(
            'Traceback (most recent call last): File "x" ValueError',
            defuse('Traceback (most recent call last):\n'
                   '  File "x"\nValueError'))

    def test_the_markers_are_defused(self):
        for marker in (BEGIN_MARKER, END_MARKER):
            defused = defuse('found %s here' % marker)
            self.assertNotIn(marker, defused)
            self.assertIn('&lt;!--', defused)

    def test_a_heading_cannot_be_injected_on_its_own_line(self):
        # The issue-body route, which is what this function was not
        # being applied to: a plan filename or heading read from
        # another repository's markdown, quoted into an issue.
        self.assertNotIn(
            '\n## ', defuse('a plan named "x\n## Not a heading"'))


class IssueTitleForTest(unittest.TestCase):
    """The one spelling of the title an issue is filed and found by."""

    def test_the_title_is_the_check_s_issue_title_prefixed(self):
        self.assertEqual('Consistency: End-of-life distributions',
                         issue_title_for('eol-distro'))

    def test_every_check_has_a_distinct_title(self):
        # Titles are the idempotency key, so two checks sharing one
        # would close each other's issues.
        titles = [issue_title_for(check_id) for check_id in ISSUE_TITLES]
        self.assertEqual(len(titles), len(set(titles)))

    def test_an_unregistered_id_falls_back_to_the_id(self):
        self.assertEqual('Consistency: no-such-check',
                         issue_title_for('no-such-check'))


if __name__ == '__main__':
    unittest.main()
