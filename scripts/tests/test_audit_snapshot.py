#!/usr/bin/env python3

"""Tests for audit_snapshot.py.

Run with: python3 scripts/tests/test_audit_snapshot.py
"""

import json
import io
import os
import re
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPT_DIR)

import audit_snapshot  # noqa: E402
from audit.registry import CHECKS  # noqa: E402


# Fixture plan names for the details strings below, standing in for
# whatever plan-retirement found rather than being pointers a reader
# follows -- so plan-source-references is told to skip these two lines
# and goes on auditing the rest of this suite. The line marker rather
# than the file-scope one on purpose: this file is about the snapshot
# comparison, and a plan pointer in it that really had rotted should
# still be found.
PLAN_A = 'PLAN-a.md'  # audit-ok: plan-reference
PLAN_B = 'PLAN-b.md'  # audit-ok: plan-reference


def result(repo, checks, timestamp='2026-09-01T06:00:00+00:00'):
    """Build a result document of the shape audit-check.py emits."""
    return {
        'repo': repo,
        'org': 'shakenfist',
        'timestamp': timestamp,
        'checks': checks,
        'summary': {'total': len(checks)},
    }


def check(check_id, status='pass', details='fine'):
    return {'id': check_id, 'status': status, 'details': details}


class NormaliseTest(unittest.TestCase):
    def test_strips_the_timestamp(self):
        stripped = audit_snapshot.normalise(result('ryll', []))
        self.assertNotIn('timestamp', stripped)
        self.assertEqual(stripped['repo'], 'ryll')

    def test_does_not_modify_its_input(self):
        original = result('ryll', [])
        audit_snapshot.normalise(original)
        self.assertIn('timestamp', original)


class CompareRepoTest(unittest.TestCase):
    def test_identical_results_have_no_differences(self):
        checks = [check('llm-tooling'), check('renovate')]
        self.assertEqual(
            audit_snapshot.compare_repo(
                result('ryll', checks), result('ryll', list(checks))),
            [],
        )

    def test_a_timestamp_alone_is_not_a_difference(self):
        checks = [check('llm-tooling')]
        before = result('ryll', checks, timestamp='2026-09-01T06:00:00+00:00')
        after = result('ryll', checks, timestamp='2026-09-02T06:00:00+00:00')
        self.assertEqual(audit_snapshot.compare_repo(before, after), [])

    def test_a_changed_status_is_reported(self):
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('renovate', 'pass')]),
            result('ryll', [check('renovate', 'fail')]),
        )
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0][0], 'renovate')
        self.assertIn('pass -> fail', differences[0][1])

    def test_a_changed_detail_is_reported_even_when_the_status_holds(self):
        """The reason this tool exists.

        A reworded detail string is invisible in a status-only
        comparison and lands on the compliance page and in issue
        bodies fleet-wide.
        """
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('renovate', 'pass', 'renovate.json found')]),
            result('ryll', [check('renovate', 'pass', 'renovate.json ok')]),
        )
        self.assertEqual(len(differences), 1)
        self.assertIn('details changed', differences[0][1])
        self.assertIn('renovate.json found', differences[0][1])
        self.assertIn('renovate.json ok', differences[0][1])

    def test_an_age_that_moves_with_the_clock_is_not_a_difference(self):
        """plan-retirement's ages, which a second day's run recomputes.

        The details string says how long each plan's row has been
        terminal, because that is what a person reading the issue
        needs in order to decide whether the finding is right. It is
        also a different number tomorrow for the same tree, and
        counting that as a difference would leave this tool reporting
        one for every repository with an eligible plan.
        """
        before = (f'2 plan(s) terminal for more than 90 days: {PLAN_A} '
                  f'(Complete, at least 97 days), {PLAN_B} (Complete, '
                  f'at least 91 days)')
        after = (f'2 plan(s) terminal for more than 90 days: {PLAN_A} '
                 f'(Complete, at least 98 days), {PLAN_B} (Complete, '
                 f'at least 92 days)')
        self.assertEqual(
            audit_snapshot.compare_repo(
                result('ryll', [check('plan-retirement', 'fail', before)]),
                result('ryll', [check('plan-retirement', 'fail', after)])),
            [],
        )

    def test_the_age_phrase_is_only_normalised_for_plan_retirement(self):
        # The same words in another check's details are text the
        # comparison is meant to see, so a future check that happens
        # to use them does not lose them from the byte-for-byte net.
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('other-check', 'fail',
                                  'stale for at least 97 days')]),
            result('ryll', [check('other-check', 'fail',
                                  'stale for at least 98 days')]))
        self.assertEqual(len(differences), 1)
        self.assertIn('details changed', differences[0][1])

    def test_a_different_plan_list_is_still_a_difference(self):
        # The normalisation is of the count alone. Which plans the
        # check names is what the snapshot is kept for, and an age
        # written over cannot be allowed to take a name with it.
        before = (f'1 plan(s) terminal for more than 90 days: {PLAN_A} '
                  f'(Complete, at least 97 days)')
        after = (f'1 plan(s) terminal for more than 90 days: {PLAN_B} '
                 f'(Complete, at least 97 days)')
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('plan-retirement', 'fail', before)]),
            result('ryll', [check('plan-retirement', 'fail', after)]),
        )
        self.assertEqual(len(differences), 1)
        self.assertIn('details changed', differences[0][1])

    def test_a_dropped_check_is_a_difference(self):
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('renovate'), check('llm-tooling')]),
            result('ryll', [check('renovate')]),
        )
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0][0], 'llm-tooling')
        self.assertIn('no longer reported', differences[0][1])

    def test_an_added_check_is_a_difference(self):
        differences = audit_snapshot.compare_repo(
            result('ryll', [check('renovate')]),
            result('ryll', [check('renovate'), check('plan-index')]),
        )
        self.assertEqual(len(differences), 1)
        self.assertEqual(differences[0][0], 'plan-index')
        self.assertIn('not present before', differences[0][1])


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.path.join(self.tmp.name, 'old')
        self.new = os.path.join(self.tmp.name, 'new')
        os.mkdir(self.old)
        os.mkdir(self.new)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, directory, repo, checks):
        path = os.path.join(directory, f'audit-result-{repo}.json')
        with open(path, 'w') as f:
            json.dump(result(repo, checks), f)

    def report(self):
        stream = io.StringIO()
        count = audit_snapshot.report(self.old, self.new, stream=stream)
        return count, stream.getvalue()

    def test_matching_snapshots_report_nothing(self):
        self.write(self.old, 'ryll', [check('renovate')])
        self.write(self.new, 'ryll', [check('renovate')])
        count, output = self.report()
        self.assertEqual(count, 0)
        self.assertIn('No differences', output)

    def test_a_filesystem_check_difference_counts(self):
        self.write(self.old, 'ryll', [check('renovate', 'pass')])
        self.write(self.new, 'ryll', [check('renovate', 'fail')])
        count, output = self.report()
        self.assertEqual(count, 1)
        self.assertIn('=== ryll ===', output)

    def test_a_network_check_difference_is_advisory_and_does_not_count(self):
        self.write(self.old, 'ryll', [check('github-security', 'pass')])
        self.write(self.new, 'ryll', [check('github-security', 'fail')])
        count, output = self.report()
        self.assertEqual(count, 0)
        self.assertIn('advisory', output)
        self.assertIn('github-security', output)

    def test_a_repository_present_in_only_one_snapshot_counts(self):
        self.write(self.old, 'ryll', [check('renovate')])
        self.write(self.new, 'ryll', [check('renovate')])
        self.write(self.new, 'instar', [check('renovate')])
        count, output = self.report()
        self.assertEqual(count, 1)
        self.assertIn('instar', output)

    def test_both_kinds_are_reported_together(self):
        self.write(self.old, 'ryll',
                   [check('renovate', 'pass'), check('sfui-vendor', 'pass')])
        self.write(self.new, 'ryll',
                   [check('renovate', 'fail'), check('sfui-vendor', 'fail')])
        count, output = self.report()
        self.assertEqual(count, 1)
        self.assertIn('=== ryll ===', output)
        self.assertIn('advisory', output)


class NetworkCheckListTest(unittest.TestCase):
    """The advisory list must match what the checker actually does.

    NETWORK_CHECKS is written by hand, and a check that grows a
    GitHub call later would otherwise silently become a source of
    spurious diffs. Take the schedule from `registry.CHECKS`, then
    re-derive from the source which of those checks reaches the
    network -- through the `repo.github` client, or by cloning --
    directly, through a helper it calls, or through a base class it
    inherits, and compare.

    Matching on the client rather than on a literal `gh` is what makes
    this survive the client seam: no check spawns `gh` itself, they
    all go through audit/github.py.
    """

    def _sources(self):
        """Every module a scheduled check can be implemented in."""
        sources = []
        package = os.path.join(SCRIPT_DIR, 'audit')
        for root, _, names in os.walk(package):
            for name in sorted(names):
                if name.endswith('.py'):
                    with open(os.path.join(root, name), 'r') as handle:
                        sources.append(handle.read())
        return sources

    def _symbols(self, sources):
        """Split every module into {symbol: body} and {class: bases}.

        Classes and functions together: a check is a class whose body
        makes the call, and the module-level helpers it reaches it
        through are functions.

        Base classes are recorded separately because a subclass body
        never names them. An intermediate base such as
        `NpmPackageCheck` holds an `applies()` that every subclass
        runs, so a scan that does not follow the inheritance edge
        cannot see what that method does.

        The tables are keyed by bare name across every module, so two
        same-named top-level symbols would silently overwrite each
        other and `_reaches_network` would examine the wrong body --
        a wrong answer rather than a missing one, which the `unread`
        assertion cannot see. Returns the collisions so the caller can
        fail on them; there are none today.
        """
        bodies = {}
        bases = {}
        duplicates = set()
        for source in sources:
            name = None
            body = []
            for line in source.splitlines():
                match = re.match(r'^(?:def|class) (\w+)', line)
                if match:
                    if name:
                        if name in bodies:
                            duplicates.add(name)
                        bodies[name] = '\n'.join(body)
                    name = match.group(1)
                    body = []
                    inherits = re.match(r'^class \w+\(([^)]*)\)', line)
                    bases[name] = [
                        base.strip()
                        for base in (inherits.group(1).split(',')
                                     if inherits else [])
                        if base.strip()
                    ]
                elif name:
                    body.append(line)
            if name:
                if name in bodies:
                    duplicates.add(name)
                bodies[name] = '\n'.join(body)
        return bodies, bases, sorted(duplicates)

    def _reaches_network(self, bodies, bases, name, seen=None):
        seen = seen if seen is not None else set()
        if name in seen:
            return False
        seen.add(name)
        body = bodies.get(name, '')
        if re.search(r"_github\(|repo\.github|GhCli\(|'clone'", body):
            return True
        for other in bases.get(name, []):
            if self._reaches_network(bodies, bases, other, seen):
                return True
        for other in bodies:
            if other != name and re.search(rf'\b{other}\s*\(', body):
                if self._reaches_network(bodies, bases, other, seen):
                    return True
        return False

    def test_network_checks_matches_the_checker(self):
        bodies, bases, duplicates = self._symbols(self._sources())

        # A bare-name table cannot tell two same-named top-level
        # symbols apart, and the survivor is whichever module was
        # walked last. Fail on the collision rather than on whatever
        # it makes the network scan conclude.
        self.assertEqual(
            duplicates, [],
            'these top-level symbol names are defined in more than one '
            'module under scripts/audit/, so the body examined for a '
            'network call may belong to the wrong one',
        )

        # Map check id to the class that implements it, taken from the
        # registry rather than matched out of the source. An earlier
        # version of this test read `class (\w+)\(Check\):`, which
        # matches nothing whose base class is an intermediate one --
        # npm_dependencies.py's three all inherit NpmPackageCheck --
        # so it covered 52 of the 55 registered criteria, and passed
        # green while doing it.
        scheduled = {check.id: type(check).__name__ for check in CHECKS}

        # Two registered criteria sharing an id would collapse the
        # map silently, so check the size before using it. This is
        # cheap rather than load-bearing: `scheduled` is built from
        # CHECKS, so nothing else can make it disagree.
        self.assertEqual(
            len(scheduled), len(CHECKS),
            'two registered checks share an id')

        # This is the assertion that would have caught the regex: a
        # criterion whose class body cannot be found is one whose
        # network behaviour was never examined, and a family dropping
        # out that way now fails loudly instead of quietly narrowing
        # what the test looks at.
        unread = sorted(
            check_id for check_id, symbol in scheduled.items()
            if symbol not in bodies)
        self.assertEqual(
            unread, [],
            'no class body was found under scripts/audit/ for these '
            'checks, so whether they reach the network was never looked '
            'at',
        )

        derived = {
            check_id for check_id, symbol in scheduled.items()
            if self._reaches_network(bodies, bases, symbol)
        }
        self.assertEqual(
            derived, set(audit_snapshot.NETWORK_CHECKS),
            'NETWORK_CHECKS in audit_snapshot.py disagrees with the '
            'checks that actually reach the network',
        )

    def test_a_marked_tool_failure_is_excused_and_others_are_not(self):
        # The amnesty audit-raised.py applies. A criterion whose
        # verdict comes from the tree says that one of its reads went
        # to a remote by marking the failure, because nothing in the
        # result could tell afterwards which git call raised. Keying
        # it off the check id instead excused every failure path the
        # criterion has, so a clone step regressing to --depth 1 would
        # have warned and passed on every leg while plan-retirement
        # errored fleet-wide.
        def failure(**extra):
            return dict({'status': 'error', 'tool_failure': True,
                         'id': 'plan-retirement'}, **extra)

        self.assertTrue(
            audit_snapshot.transient_tool_failure(failure(transient=True)))
        self.assertFalse(
            audit_snapshot.transient_tool_failure(failure()))

        # A network check needs no mark: asking GitHub is how it
        # reaches a verdict at all, so there is no other tooling in it
        # to fail.
        self.assertTrue(audit_snapshot.transient_tool_failure(
            {'status': 'error', 'tool_failure': True,
             'id': 'github-security'}))

        # Neither excuses a check that broke, or a verdict.
        self.assertFalse(audit_snapshot.transient_tool_failure(
            {'status': 'error', 'id': 'plan-retirement', 'transient': True}))
        self.assertFalse(audit_snapshot.transient_tool_failure(
            {'status': 'fail', 'tool_failure': True,
             'id': 'plan-retirement', 'transient': True}))

    def test_the_two_questions_are_asked_of_different_results(self):
        # github_did_not_answer() is the smoke run's question and
        # transient_tool_failure() is the scheduled run's. The smoke
        # run's clone is unfiltered and it has no token, so a marked
        # failure is still a check that broke as far as it is
        # concerned, and collapsing the two is what failed the leg
        # before.
        marked = {'status': 'error', 'tool_failure': True,
                  'id': 'plan-retirement', 'transient': True}
        self.assertTrue(audit_snapshot.transient_tool_failure(marked))
        self.assertFalse(audit_snapshot.github_did_not_answer(marked))


if __name__ == '__main__':
    unittest.main()
