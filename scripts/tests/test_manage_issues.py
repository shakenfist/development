#!/usr/bin/env python3

"""Tests for the issue body audit-manage-issues.py files.

The body is the one thing a fleet-wide consumer actually reads: it is
what somebody picking up a `consistency` issue in another repository
sees, and neither of its per-item lists had any coverage at all. The
script's hyphenated name makes it unimportable, so it is loaded the way
test_metadata.py loads audit-update-docs.py.

Run with: python3 scripts/tests/test_manage_issues.py
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audit_common  # noqa: E402
from audit_common import ISSUE_TITLES  # noqa: E402
from tests.base import REPO_ROOT  # noqa: E402
from tests.fake_issues import FakeIssueTracker  # noqa: E402


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

        It must be defused here as well as on the compliance page. A
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


REPO = 'shakenfist/testrepo'


def title_for(check_id):
    return f'Consistency: {ISSUE_TITLES[check_id]}'


def check_for(title):
    """The check whose issue carries `title`; titles are unique."""
    return next(check_id for check_id in ISSUE_TITLES
                if title_for(check_id) == title)


def title_families():
    """Issue titles that are a prefix of another, with all they prefix.

    Matching an issue to its check by anything looser than the exact
    title closes or rewrites a sibling's issue in every one of these.
    Derived rather than listed, so a criterion joining a family is
    covered without anybody remembering to add it here.
    """
    titles = sorted(title_for(check_id) for check_id in ISSUE_TITLES)
    families = {}
    for title in titles:
        members = [other for other in titles if other.startswith(title)]
        if len(members) > 1:
            families[title] = members
    return families


class IssueLifecycleCase(unittest.TestCase):
    """Run process_results against a fake tracker and see what it did."""

    def setUp(self):
        self.module = _manage_issues()
        self.module.time = types.SimpleNamespace(sleep=lambda seconds: None)
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.err = self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.tracker = self.fresh_tracker()

    def fresh_tracker(self, fail=()):
        audit_common._CANONICAL_CACHE.clear()
        self.addCleanup(audit_common._CANONICAL_CACHE.clear)
        return FakeIssueTracker(fail=fail)

    def results(self, *checks, repo='testrepo'):
        """One repository's results, from (check id, status) pairs."""
        return {
            'org': 'shakenfist', 'repo': repo,
            'summary': {'pass': 0, 'fail': 0, 'not_applicable': 0},
            'checks': [{'id': check_id, 'status': status, 'details': 'found'}
                       for check_id, status in checks],
        }

    def process(self, *checks, dry_run=False):
        """Process (check id, status) pairs as one repository's results."""
        return self.module.process_results(
            self.results(*checks), dry_run=dry_run, github=self.tracker)

    def run_main(self, *repos):
        """Run main() over one results file per repository."""
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        for repo in repos:
            with open(os.path.join(tmp, f'{repo}.json'), 'w') as f:
                json.dump(self.results(('eol-distro', 'fail'), repo=repo), f)
        self.module.GhCli = lambda: self.tracker
        argv = ['audit-manage-issues.py', '--results-dir', tmp]
        with mock.patch.object(sys, 'argv', argv):
            with self.assertRaises(SystemExit) as raised:
                self.module.main()
        return raised.exception.code

    def body(self, check_id):
        return self.module.build_issue_body(
            check_id, {'id': check_id, 'status': 'fail', 'details': 'found'})

    def written(self, operation):
        return [write[2] for write in self.tracker.writes
                if write[0] == operation]


class IssueLifecycleTest(IssueLifecycleCase):
    """What a verdict does to the issues of the repository it is about.

    The rest of this file tests the body. These test the lifecycle --
    which verdict files an issue, which closes one, and which issue it
    is -- which is the part that touches every repository in the fleet
    on every scheduled run.
    """

    def test_a_failure_with_no_open_issue_files_one(self):
        self.process(('eol-distro', 'fail'))
        self.assertEqual(self.written('create'), [title_for('eol-distro')])
        filed = self.tracker.issues[0]
        self.assertEqual(filed['labels'], ['consistency'])
        self.assertEqual(filed['body'], self.body('eol-distro'))

    def test_a_failure_with_its_issue_open_files_no_second(self):
        self.tracker.add(REPO, title_for('eol-distro'), self.body('eol-distro'))
        self.process(('eol-distro', 'fail'))
        self.assertEqual(self.tracker.writes, [])

    def test_a_closed_issue_does_not_count_as_open(self):
        self.tracker.add(REPO, title_for('eol-distro'), state='closed')
        self.process(('eol-distro', 'fail'))
        self.assertEqual(self.written('create'), [title_for('eol-distro')])

    def test_a_pass_closes_every_open_issue_for_its_title(self):
        first = self.tracker.add(REPO, title_for('eol-distro'))
        second = self.tracker.add(REPO, title_for('eol-distro'))
        self.process(('eol-distro', 'pass'))
        self.assertEqual(sorted(self.written('close')), [first, second])
        self.assertEqual(self.tracker.open_titles(REPO), [])
        self.assertEqual(sorted(self.written('comment')), [first, second])

    def test_not_applicable_closes_them_too(self):
        first = self.tracker.add(REPO, title_for('eol-distro'))
        second = self.tracker.add(REPO, title_for('eol-distro'))
        self.process(('eol-distro', 'not_applicable'))
        self.assertEqual(sorted(self.written('close')), [first, second])
        self.assertEqual(self.tracker.open_titles(REPO), [])

    def test_a_pass_with_nothing_open_writes_nothing(self):
        self.process(('eol-distro', 'pass'), ('renovate', 'not_applicable'))
        self.assertEqual(self.tracker.writes, [])

    def test_another_repository_s_issue_is_not_touched(self):
        self.tracker.add('shakenfist/other', title_for('eol-distro'))
        self.process(('eol-distro', 'pass'))
        self.assertEqual(self.tracker.writes, [])

    def test_a_dry_run_creates_and_closes_nothing(self):
        self.tracker.add(REPO, title_for('eol-distro'))
        self.tracker.add(REPO, title_for('renovate'), 'stale body')
        self.process(('eol-distro', 'pass'), ('renovate', 'fail'),
                     ('sfui-vendor', 'fail'), dry_run=True)
        self.assertEqual(self.tracker.writes, [])
        output = self.out.getvalue()
        self.assertIn('[eol-distro] PASS -- closing issue #1', output)
        self.assertIn('[renovate] FAIL -- issue #2 already open, updating body',
                      output)
        self.assertIn('[sfui-vendor] FAIL -- creating issue', output)


class ExactTitleTest(IssueLifecycleCase):
    """An issue belongs to the check whose title it carries exactly.

    Several titles are prefixes of others: "Workflow standards" of
    every "Workflow standards (...)" criterion, and so on. GitHub's
    search matches a quoted phrase anywhere in a title, so a prefix
    match would let one criterion's verdict close, or rewrite the body
    of, a sibling criterion's issue in every repository at once.
    """

    def test_there_are_families_to_test(self):
        # Guards the other cases in this class against passing by
        # vacuum, should the titles ever stop overlapping.
        self.assertTrue(title_families())

    def seed_family(self, members, skip=None):
        """Open one issue per family member, returning {title: number}."""
        return {title: self.tracker.add(REPO, title,
                                        self.body(check_for(title)))
                for title in members if title != skip}

    def test_a_verdict_closes_only_its_own_title_in_a_family(self):
        for family in title_families().values():
            for title in family:
                for status in ('pass', 'not_applicable'):
                    with self.subTest(title=title, status=status):
                        self.tracker = self.fresh_tracker()
                        numbers = self.seed_family(family)
                        self.process((check_for(title), status))
                        self.assertEqual(self.written('close'),
                                         [numbers[title]])
                        self.assertEqual(
                            self.tracker.open_titles(REPO),
                            sorted(t for t in family if t != title))

    def test_a_failure_does_not_adopt_a_sibling_s_issue(self):
        for family in title_families().values():
            for title in family:
                with self.subTest(title=title):
                    self.tracker = self.fresh_tracker()
                    self.seed_family(family, skip=title)
                    self.process((check_for(title), 'fail'))
                    self.assertEqual(self.tracker.writes,
                                     [('create', REPO, title)])


class OpenIssueListingTest(IssueLifecycleCase):
    """A repository's open issues are listed once, and a failure is loud.

    One search per check, with failures answered as an empty list, made
    a transient error look like "no issue": a failing check then filed
    a duplicate of the issue it already had, and the compliance page
    published the issue as missing.
    """

    def test_the_issues_are_listed_once_per_repository(self):
        self.process(('eol-distro', 'fail'), ('renovate', 'pass'),
                     ('sfui-vendor', 'not_applicable'))
        listings = [call for call in self.tracker.calls
                    if call[:2] == ['issue', 'list']]
        self.assertEqual(len(listings), 1)
        self.assertNotIn('--search', listings[0])

    def test_a_failed_listing_writes_nothing(self):
        self.tracker = self.fresh_tracker(fail={'list'})
        self.tracker.add(REPO, title_for('renovate'))
        tally = self.process(('eol-distro', 'fail'), ('renovate', 'pass'))
        self.assertEqual(self.tracker.writes, [])
        self.assertEqual(tally.unlisted, [REPO])
        self.assertTrue(tally.failed())
        self.assertIn('could not list open issues', self.err.getvalue())

    def test_a_failed_listing_does_not_stop_the_next_repository(self):
        self.tracker = self.fresh_tracker(fail={'list:shakenfist/broken'})
        tally = self.module.Tally()
        for repo in ('broken', 'testrepo'):
            self.module.process_results(
                self.results(('eol-distro', 'fail'), repo=repo),
                github=self.tracker, tally=tally)
        self.assertEqual(self.tracker.writes,
                         [('create', REPO, title_for('eol-distro'))])
        self.assertEqual(tally.unlisted, ['shakenfist/broken'])

    def test_a_clean_run_is_not_a_failure(self):
        self.assertFalse(self.process(('eol-distro', 'fail')).failed())

    def test_more_than_ten_issues_with_one_title_are_all_found(self):
        """The search this replaced stopped at ten, family included."""
        root, family = max(title_families().items(),
                           key=lambda item: len(item[1]))
        for title in family[1:]:
            self.tracker.add(REPO, title)
        duplicates = [self.tracker.add(REPO, root) for _ in range(12)]
        self.process((check_for(root), 'pass'))
        self.assertEqual(sorted(self.written('close')), duplicates)

    def test_main_fails_after_managing_the_repositories_it_could(self):
        self.tracker = self.fresh_tracker(fail={'list:shakenfist/broken'})
        self.assertEqual(self.run_main('broken', 'testrepo'), 1)
        self.assertEqual(self.tracker.writes,
                         [('create', REPO, title_for('eol-distro'))])
        self.assertIn('shakenfist/broken', self.err.getvalue())


class WriteFailureTest(IssueLifecycleCase):
    """A write GitHub refuses is counted, and fails the run at the end.

    Each failure used to be printed and forgotten, so a run in which
    every write failed -- an expired token, say -- was green.
    """

    def test_a_failed_create_is_counted(self):
        self.tracker = self.fresh_tracker(fail={'create'})
        tally = self.process(('eol-distro', 'fail'))
        self.assertEqual(len(tally.write_failures), 1)
        self.assertIn(title_for('eol-distro'), tally.write_failures[0])
        self.assertTrue(tally.failed())

    def test_a_failed_edit_is_counted(self):
        self.tracker = self.fresh_tracker(fail={'edit'})
        number = self.tracker.add(REPO, title_for('eol-distro'), 'stale')
        tally = self.process(('eol-distro', 'fail'))
        self.assertEqual(tally.write_failures, [f'{REPO}: edit #{number}'])

    def test_a_failed_close_is_counted_and_not_commented_on(self):
        """Otherwise every run repeats the comment on an issue left open."""
        self.tracker = self.fresh_tracker(fail={'close'})
        number = self.tracker.add(REPO, title_for('eol-distro'))
        tally = self.process(('eol-distro', 'pass'))
        self.assertEqual(tally.write_failures, [f'{REPO}: close #{number}'])
        self.assertEqual(self.tracker.writes, [])
        self.assertEqual(self.tracker.issue(number)['state'], 'open')

    def test_a_failed_duplicate_close_is_counted(self):
        self.tracker = self.fresh_tracker(fail={'close'})
        body = self.body('eol-distro')
        self.tracker.add(REPO, title_for('eol-distro'), body)
        duplicate = self.tracker.add(REPO, title_for('eol-distro'), body)
        tally = self.process(('eol-distro', 'fail'))
        self.assertEqual(tally.write_failures, [f'{REPO}: close #{duplicate}'])

    def test_the_close_comes_before_its_comment(self):
        number = self.tracker.add(REPO, title_for('eol-distro'))
        self.process(('eol-distro', 'pass'))
        self.assertEqual(self.tracker.writes,
                         [('close', REPO, number), ('comment', REPO, number)])

    def test_a_failed_comment_after_a_close_is_not_counted(self):
        """The issue is closed, and no later run could retry it."""
        self.tracker = self.fresh_tracker(fail={'comment'})
        number = self.tracker.add(REPO, title_for('eol-distro'))
        tally = self.process(('eol-distro', 'pass'))
        self.assertEqual(self.tracker.issue(number)['state'], 'closed')
        self.assertFalse(tally.failed())
        self.assertIn('commenting on closed issue', self.err.getvalue())

    def test_a_write_that_times_out_is_counted_and_the_run_goes_on(self):
        tracker = self.tracker
        original = tracker.run

        def run(args, timeout=30):
            if args[:2] == ['issue', 'create']:
                raise subprocess.TimeoutExpired('gh', timeout)
            return original(args, timeout)

        tracker.run = run
        renovate = tracker.add(REPO, title_for('renovate'))
        tally = self.process(('eol-distro', 'fail'), ('renovate', 'pass'))
        self.assertEqual(len(tally.write_failures), 1)
        self.assertEqual(self.written('close'), [renovate])

    def test_a_dry_run_counts_nothing(self):
        self.tracker = self.fresh_tracker(fail={'create', 'close'})
        self.tracker.add(REPO, title_for('renovate'))
        tally = self.process(('eol-distro', 'fail'), ('renovate', 'pass'),
                             dry_run=True)
        self.assertFalse(tally.failed())

    def test_main_exits_non_zero_after_a_failed_write(self):
        self.tracker = self.fresh_tracker(fail={'create:shakenfist/broken'})
        self.assertEqual(self.run_main('broken', 'testrepo'), 1)
        self.assertEqual(self.tracker.writes,
                         [('create', REPO, title_for('eol-distro'))])
        self.assertIn('1 issue write(s) failed', self.err.getvalue())


class RetiredTitleTest(IssueLifecycleCase):
    """A retired criterion's open issues are closed, not orphaned."""

    RETIRED = 'Retired criterion'

    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(
            audit_common, 'RETIRED_ISSUE_TITLES', (self.RETIRED,)))

    def test_a_retired_title_s_open_issues_are_closed_with_a_reason(self):
        first = self.tracker.add(REPO, f'Consistency: {self.RETIRED}')
        second = self.tracker.add(REPO, f'Consistency: {self.RETIRED}')
        live = self.tracker.add(REPO, title_for('eol-distro'),
                                self.body('eol-distro'))
        self.process(('eol-distro', 'fail'))
        self.assertEqual(sorted(self.written('close')), [first, second])
        self.assertEqual(self.tracker.issue(live)['state'], 'open')
        self.assertIn('[retired] closing issue', self.out.getvalue())

    def test_the_comment_says_the_criterion_was_retired(self):
        number = self.tracker.add(REPO, f'Consistency: {self.RETIRED}')
        self.process(('eol-distro', 'pass'))
        comments = [call for call in self.tracker.calls
                    if call[:2] == ['issue', 'comment']]
        self.assertEqual(len(comments), 1)
        self.assertIn(str(number), comments[0])
        self.assertIn('retired', comments[0][-1])

    def test_only_the_exact_retired_title_is_closed(self):
        self.tracker.add(REPO, f'Consistency: {self.RETIRED} (kept)')
        self.process(('eol-distro', 'pass'))
        self.assertEqual(self.tracker.writes, [])

    def test_a_dry_run_closes_nothing(self):
        self.tracker.add(REPO, f'Consistency: {self.RETIRED}')
        self.process(('eol-distro', 'pass'), dry_run=True)
        self.assertEqual(self.tracker.writes, [])
        self.assertIn('[retired] closing issue #1', self.out.getvalue())

    def test_a_failed_close_is_counted(self):
        self.tracker = self.fresh_tracker(fail={'close'})
        number = self.tracker.add(REPO, f'Consistency: {self.RETIRED}')
        tally = self.process(('eol-distro', 'pass'))
        self.assertEqual(tally.write_failures, [f'{REPO}: close #{number}'])


class OpenIssueRefreshTest(IssueLifecycleCase):
    """A failing check's open issue follows the latest result.

    It used to be left as the snapshot it was filed with: ryll#304 went
    on reporting 63 files needing review when the audit measured 120,
    and kerbside#227 went on reporting none reviewed after 114 had
    been, both still linking a spec path that had since moved.
    """

    def check(self, details='120 of 214 need review'):
        return {'id': 'eol-distro', 'status': 'fail', 'details': details}

    def process(self, check, dry_run=False):
        self.module.process_results({
            'org': 'shakenfist', 'repo': 'testrepo',
            'summary': {'pass': 0, 'fail': 1, 'not_applicable': 0},
            'checks': [check],
        }, dry_run=dry_run, github=self.tracker)

    def open_issue(self, body):
        return self.tracker.add(REPO, title_for('eol-distro'), body)

    def test_a_stale_body_is_rewritten_to_the_latest_result(self):
        number = self.open_issue(self.module.build_issue_body(
            'eol-distro', self.check('63 need review')))
        self.process(self.check())
        expected = self.module.build_issue_body('eol-distro', self.check())
        self.assertEqual(self.tracker.writes, [('edit', REPO, number)])
        self.assertEqual(self.tracker.issue(number)['body'], expected)

    def test_a_current_body_is_left_alone(self):
        self.open_issue(
            self.module.build_issue_body('eol-distro', self.check()))
        self.process(self.check())
        self.assertEqual(self.tracker.writes, [])
        self.assertIn('already open and current', self.out.getvalue())

    def test_line_endings_and_trailing_space_are_not_a_change(self):
        """Otherwise every issue is rewritten on every run."""
        body = self.module.build_issue_body('eol-distro', self.check())
        self.open_issue(body.replace('\n', '\r\n').rstrip())
        self.process(self.check())
        self.assertEqual(self.tracker.writes, [])

    def test_an_issue_with_no_body_is_filled_in(self):
        self.open_issue(None)
        self.process(self.check())
        self.assertEqual(self.written('edit'), [1])

    def test_a_dry_run_reports_the_update_without_making_it(self):
        number = self.open_issue('old')
        self.process(self.check(), dry_run=True)
        self.assertEqual(self.tracker.writes, [])
        self.assertIn(f'#{number} already open, updating body',
                      self.out.getvalue())

    def test_only_the_surviving_original_is_updated(self):
        original = self.open_issue('old')
        duplicate = self.open_issue('old')
        self.process(self.check())
        self.assertEqual(self.written('edit'), [original])
        self.assertEqual(self.written('close'), [duplicate])
        self.assertEqual(self.tracker.issue(duplicate)['state'], 'closed')

    def test_the_footer_says_the_body_is_maintained(self):
        """A reader should know the list is live, not a filing snapshot."""
        body = self.module.build_issue_body('eol-distro', self.check())
        self.assertIn('rewrites this description on every run', body)


if __name__ == '__main__':
    unittest.main()
