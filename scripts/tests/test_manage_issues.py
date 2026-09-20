#!/usr/bin/env python3

"""Tests for the issue body audit-manage-issues.py files.

The body is the one thing a fleet-wide consumer actually reads: it is
what somebody picking up a `consistency` issue in another repository
sees, and neither of its per-item lists had any coverage at all. The
script's hyphenated name makes it unimportable, so it is loaded the way
test_metadata.py loads audit-update-docs.py.

Run with: python3 scripts/tests/test_manage_issues.py
"""

import importlib.util
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.base import REPO_ROOT  # noqa: E402


def _manage_issues():
    """Load audit-manage-issues.py, whose hyphen makes it unimportable."""
    path = os.path.join(REPO_ROOT, 'scripts', 'audit-manage-issues.py')
    spec = importlib.util.spec_from_file_location('audit_manage_issues', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IssueBodyTest(unittest.TestCase):
    def setUp(self):
        self.build = _manage_issues().build_issue_body

    def result(self, **extra):
        built = {'id': 'eol-distro', 'status': 'fail',
                 'details': 'two references'}
        built.update(extra)
        return built

    def test_the_body_names_the_check_and_links_its_spec(self):
        body = self.build('eol-distro', self.result())
        self.assertIn('End-of-life distributions', body)
        self.assertIn('docs/audits/eol-distro.md', body)
        self.assertIn('two references', body)

    def test_the_details_are_defused_before_they_reach_the_body(self):
        """A detail string is another repository's text, published.

        The push audit of PLAN-push-audit-phase.md found this path
        splicing it in raw while the compliance page defused it. A
        newline in a detail renders a heading in an issue body, and
        the comment opener terminates the next compliance splice
        early; both are reachable by committing a plan file with the
        right name to any audited repository.
        """
        body = self.build('eol-distro', self.result(
            details='a plan named "x\n## Not a real heading" <!-- end -->'))
        self.assertNotIn('\n## Not a real heading', body)
        self.assertIn('&lt;!-- end -->', body)
        self.assertNotIn('<!-- end -->', body)

    def test_a_result_with_neither_list_renders_neither_heading(self):
        body = self.build('eol-distro', self.result())
        self.assertNotIn('**Findings:**', body)
        self.assertNotIn('**Missing items:**', body)

    def test_findings_render_one_bullet_each(self):
        body = self.build('eol-distro', self.result(
            findings=['ci.yml:3 (debian-12)', 'Dockerfile:1 (debian:12)']))
        self.assertIn('**Findings:**', body)
        self.assertIn('- `ci.yml:3 (debian-12)`', body)
        self.assertIn('- `Dockerfile:1 (debian:12)`', body)

    def test_missing_renders_under_its_own_heading(self):
        """The pre-existing sibling, which also had no coverage."""
        body = self.build('review-scope-completeness', self.result(
            missing=['scripts/thing.py']))
        self.assertIn('**Missing items:**', body)
        self.assertIn('- `scripts/thing.py`', body)
        self.assertNotIn('**Findings:**', body)

    def test_the_two_lists_are_independent(self):
        """Nothing stops a check carrying both; they must not merge."""
        body = self.build('eol-distro', self.result(
            missing=['a.py'], findings=['b.yml:1 (debian-12)']))
        self.assertIn('**Missing items:**', body)
        self.assertIn('**Findings:**', body)
        self.assertIn('- `a.py`', body)
        self.assertIn('- `b.yml:1 (debian-12)`', body)
        self.assertLess(body.index('**Missing items:**'),
                        body.index('**Findings:**'))


class IssueBodyLimitTest(unittest.TestCase):
    """The lists are unbounded, and GitHub's issue body is not."""

    #: GitHub rejects a create with a body longer than this.
    GITHUB_LIMIT = 65536

    def setUp(self):
        self.module = _manage_issues()

    def body(self, count):
        return self.module.build_issue_body('eol-distro', {
            'id': 'eol-distro', 'status': 'fail',
            'details': 'many references',
            'findings': ['.github/workflows/ci.yml:%d (debian-12)' % n
                         for n in range(count)],
        })

    def test_a_realistic_list_is_rendered_whole(self):
        """The fleet's worst repository has twenty-one findings."""
        body = self.body(21)
        self.assertNotIn('omitted', body)
        self.assertIn('.github/workflows/ci.yml:20 (debian-12)', body)

    def test_a_pathological_list_still_files_an_issue(self):
        body = self.body(5000)
        self.assertLess(len(body), self.GITHUB_LIMIT)
        self.assertIn('more, omitted', body)

    def test_every_item_is_either_rendered_or_counted(self):
        """A short list that does not say so is the failure to avoid."""
        body = self.body(5000)
        rendered = body.count('(debian-12)')
        omitted = re.search(r'\.\.\.and (\d+) more', body)
        self.assertIsNotNone(omitted, body[-500:])
        self.assertEqual(5000, rendered + int(omitted.group(1)))


class DetailsLimitTest(unittest.TestCase):
    """`details` is written by the criterion and is not bounded.

    Several criteria route a per-item list through it and the npm ones
    quote a workflow's `run:` line verbatim, so it can carry the body
    past GitHub's limit on its own. When it does, the create call
    returns nothing and the criterion silently stops filing while the
    audit still reports success.
    """

    #: GitHub rejects a create with a body longer than this.
    GITHUB_LIMIT = 65536

    def setUp(self):
        self.module = _manage_issues()

    def body(self, details, **extra):
        built = {'id': 'eol-distro', 'status': 'fail', 'details': details}
        built.update(extra)
        return self.module.build_issue_body('eol-distro', built)

    def test_a_realistic_details_string_is_rendered_whole(self):
        body = self.body('two references to debian-12')
        self.assertIn('two references to debian-12', body)
        self.assertNotIn('truncated', body)

    def test_a_pathological_details_string_still_files_an_issue(self):
        body = self.body('debian-12 ' * 20000)
        self.assertLess(len(body), self.GITHUB_LIMIT)
        self.assertIn('truncated', body)

    def test_a_long_details_string_leaves_room_for_the_lists(self):
        """The two budgets are one budget, or neither of them holds.

        Asserting on the heading would not say this: `render_issue_items`
        emits it unconditionally, so the test would pass with every item
        dropped -- which is exactly what an uncapped `details` did.
        """
        body = self.body(
            'debian-12 ' * 20000,
            findings=['.github/workflows/ci.yml:%d (debian-12)' % n
                      for n in range(500)])
        self.assertLess(len(body), self.GITHUB_LIMIT)
        self.assertIn('.github/workflows/ci.yml:0 (debian-12)', body)
        self.assertIn('.github/workflows/ci.yml:499 (debian-12)', body)
        self.assertNotIn('more, omitted', body)

    # `x` rather than `d` as the filler: neither the heading nor the
    # trailer contains one, so counting it counts only what survived
    # of `details`.
    def test_details_one_character_under_the_room_renders_whole(self):
        """The boundary is asked for, not approached by bisection."""
        room = self.module.details_room(0)
        rendered = self.module.render_details('x' * (room - 1), 0)
        self.assertNotIn('truncated', rendered)
        self.assertEqual(room - 1, rendered.count('x'))

    def test_details_one_character_over_the_room_truncates(self):
        """`room` characters is one too many: the whole form adds a newline."""
        room = self.module.details_room(0)
        rendered = self.module.render_details('x' * room, 0)
        self.assertIn('truncated', rendered)
        self.assertEqual(room, rendered.count('x'))

    def test_details_well_over_the_room_loses_the_tail(self):
        room = self.module.details_room(0)
        rendered = self.module.render_details('x' * (room + 100), 0)
        self.assertIn('truncated', rendered)
        self.assertEqual(room, rendered.count('x'))

    def test_a_truncated_render_fits_inside_the_details_budget(self):
        """The bound that does not come from `details_room`.

        Every other assertion here asks `details_room` what to expect,
        so a miscalculation inside it moves the expectation with it and
        stays invisible. This one is arithmetic the function does not
        supply: heading plus kept text plus trailer, all of it, under
        the budget.
        """
        rendered = self.module.render_details('x' * 10 ** 6, 0)
        self.assertLessEqual(len(rendered), self.module.DETAILS_BUDGET)

    def test_details_is_capped_by_its_own_budget_not_the_body_one(self):
        """An empty body does not entitle `details` to all of it."""
        self.assertLess(
            self.module.details_room(0), self.module.DETAILS_BUDGET)
        self.assertLess(
            self.module.details_room(0), self.module.ISSUE_BODY_BUDGET // 2)

    def test_details_with_the_budget_already_spent_renders_none_of_it(self):
        """Negative room is clamped, not sliced from the end."""
        # Longer than the overrun on purpose: with a shorter string an
        # unclamped `details[:room]` returns the empty string too, and
        # the assertion cannot tell the two apart.
        rendered = self.module.render_details(
            'debian-12 ' * 1000, self.module.ISSUE_BODY_BUDGET + 1000)
        self.assertIn('truncated', rendered)
        self.assertNotIn('debian-12', rendered)


class ItemDefusingTest(unittest.TestCase):
    """A filename cannot forge content in a bot-authored issue body.

    `missing` and `findings` are built from `git ls-files`, so an
    audited repository chooses these bytes. The body is filed by
    shakenfist-bot, so anything that escapes the code span arrives
    carrying the bot's authority.
    """

    def setUp(self):
        module = _manage_issues()
        self.defuse = module.defuse_item
        self.build = module.build_issue_body

    def result(self, **extra):
        built = {'id': 'eol-distro', 'status': 'fail',
                 'details': 'two references'}
        built.update(extra)
        return built

    def test_an_ordinary_path_is_unchanged(self):
        self.assertEqual('`src/a.py`', self.defuse('src/a.py'))

    def test_a_newline_cannot_start_a_new_line(self):
        defused = self.defuse('src/a.py\n\n## Fake heading')
        self.assertNotIn('\n', defused)
        self.assertIn('## Fake heading', defused)

    def test_a_backtick_cannot_close_the_span(self):
        # CommonMark ends a span at the first backtick run matching the
        # opener, so the opener has to be longer than anything inside.
        defused = self.defuse('src/we`ird.py')
        self.assertTrue(defused.startswith('`` '))
        self.assertTrue(defused.endswith(' ``'))

    def test_the_fence_outgrows_the_longest_run(self):
        defused = self.defuse('a```b`c')
        self.assertTrue(defused.startswith('```` '))
        self.assertTrue(defused.endswith(' ````'))

    def test_a_value_may_begin_and_end_with_a_backtick(self):
        defused = self.defuse('`both`')
        self.assertEqual('`` `both` ``', defused)

    def test_nothing_is_dropped(self):
        """A path needing defusing is still a path somebody must review.

        The expectation is the flattened value verbatim, not a
        stripped one. Stripping the backticks would make this vacuous
        for `` `x` `` -- the one input where they are all at the ends
        -- so the assertion would rest entirely on `we`ird.py`, whose
        interior backtick survives a strip. Asking for the whole value
        makes every input here pin the property.
        """
        for raw in ['plain.py', 'we`ird.py', 'two\nlines.py', '`x`']:
            flat = ' '.join(raw.split())
            self.assertIn(flat, self.defuse(raw))

    def test_an_injected_heading_stays_inside_the_bullet(self):
        body = self.build('eol-distro', self.result(
            missing=['ok.py', 'evil.py\n\n## Not a real heading']))
        self.assertNotIn('\n## Not a real heading', body)
        self.assertIn('## Not a real heading', body)
        for line in body.splitlines():
            if 'Not a real heading' in line:
                self.assertTrue(line.startswith('- '), line)


if __name__ == '__main__':
    unittest.main()
