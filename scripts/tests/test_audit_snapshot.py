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
        # The plan names below are fixtures standing in for whatever
        # plan-retirement found, not pointers a reader follows, so
        # each line carrying one is marked for plan-source-references.
        # The line marker rather than the file-scope one: this suite
        # is about the snapshot comparison and would otherwise stop
        # being audited for a pointer that really had rotted.
        before = ('2 plan(s) terminal for more than 90 days: PLAN-a.md '  # audit-ok: plan-reference
                  '(Complete, at least 97 days), PLAN-b.md (Complete, at '  # audit-ok: plan-reference
                  'least 91 days)')
        after = ('2 plan(s) terminal for more than 90 days: PLAN-a.md '  # audit-ok: plan-reference
                 '(Complete, at least 98 days), PLAN-b.md (Complete, at '  # audit-ok: plan-reference
                 'least 92 days)')
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
        before = ('1 plan(s) terminal for more than 90 days: PLAN-a.md '  # audit-ok: plan-reference
                  '(Complete, at least 97 days)')
        after = ('1 plan(s) terminal for more than 90 days: PLAN-b.md '  # audit-ok: plan-reference
                 '(Complete, at least 97 days)')
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
    """Both hand-written lists must match what the checker does.

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

    TRANSIENT_FAILURE_CHECKS is derived the same way from a different
    question: which checks read a blob out of history, which in the
    `--filter=blob:none` clone the scheduled run makes is a fetch from
    the promisor remote. That set is hand-written too, because nothing
    in a result says which git call produced a tool failure, and the
    two lists go stale in the same way.
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
        other and the reachability walk would examine the wrong body
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

    # The GitHub client, or a clone from it. Matched on the client
    # rather than on a literal `gh` for the reason the class docstring
    # gives.
    NETWORK = r"_github\(|repo\.github|GhCli\(|'clone'"

    # A git subcommand that materialises a blob out of history, which
    # is the read a promisor remote has to answer. `ls-files`,
    # `check-ignore`, `rev-parse` and a `log` of commit metadata do
    # not: a clone filtered of blobs still holds every commit and
    # every tree. `cat-file` is matched whatever it is asked for,
    # because the one call of it today is in a check that already
    # reaches the network and a future one asked for a blob would not
    # be.
    HISTORY_BLOB = r"'(?:show|cat-file|archive)'"

    def _reaches(self, pattern, bodies, bases, name, seen=None):
        """Does name, or anything it reaches, match pattern?

        The call-graph walk both derivations share: the symbol's own
        body, then its base classes, then every other top-level symbol
        its body calls. Only the pattern differs between them.
        """
        seen = seen if seen is not None else set()
        if name in seen:
            return False
        seen.add(name)
        body = bodies.get(name, '')
        if re.search(pattern, body):
            return True
        for other in bases.get(name, []):
            if self._reaches(pattern, bodies, bases, other, seen):
                return True
        for other in bodies:
            if other != name and re.search(rf'\b{other}\s*\(', body):
                if self._reaches(pattern, bodies, bases, other, seen):
                    return True
        return False

    def _scheduled(self):
        """Check id to implementing class name, from the registry.

        Taken from CHECKS rather than matched out of the source, for
        the reason test_network_checks_matches_the_checker gives.
        That test asserts the map is complete and unambiguous, so a
        derivation here that went blind fails there first.
        """
        return {check.id: type(check).__name__ for check in CHECKS}

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
            if self._reaches(self.NETWORK, bodies, bases, symbol)
        }
        self.assertEqual(
            derived, set(audit_snapshot.NETWORK_CHECKS),
            'NETWORK_CHECKS in audit_snapshot.py disagrees with the '
            'checks that actually reach the network',
        )

    def test_transient_failure_checks_matches_the_checker(self):
        # The set audit-raised.py excuses is every check that asks
        # GitHub plus every check that reads a blob out of history.
        # Asserted as that union rather than as a literal list, so a
        # criterion added to either side is covered without a second
        # place to remember.
        bodies, bases, _ = self._symbols(self._sources())
        scheduled = self._scheduled()

        reads_history = {
            check_id for check_id, symbol in scheduled.items()
            if self._reaches(self.HISTORY_BLOB, bodies, bases, symbol)
        }
        self.assertEqual(
            set(audit_snapshot.TRANSIENT_FAILURE_CHECKS),
            set(audit_snapshot.NETWORK_CHECKS) | reads_history,
            'TRANSIENT_FAILURE_CHECKS in audit_snapshot.py disagrees '
            'with the checks whose tooling reaches a remote',
        )

        # The finding this list was split out for: plan-retirement is
        # excused as transient without being a network check, because
        # its verdict is held to the byte-for-byte comparison while
        # its `git show` of a historical index is not.
        self.assertIn('plan-retirement', reads_history)
        self.assertIn('plan-retirement',
                      audit_snapshot.TRANSIENT_FAILURE_CHECKS)
        self.assertNotIn('plan-retirement', audit_snapshot.NETWORK_CHECKS)

    def test_the_two_questions_are_asked_of_different_sets(self):
        # github_did_not_answer() is the smoke run's question and
        # transient_tool_failure() is the scheduled run's. A
        # plan-retirement tool failure is transient, but it is not
        # GitHub declining to answer a run that has no token, and
        # collapsing the two is what failed the leg before.
        failure = {'status': 'error', 'tool_failure': True,
                   'id': 'plan-retirement'}
        self.assertTrue(audit_snapshot.transient_tool_failure(failure))
        self.assertFalse(audit_snapshot.github_did_not_answer(failure))

        # Neither excuses a check that broke, or a verdict.
        broken = {'status': 'error', 'id': 'plan-retirement'}
        self.assertFalse(audit_snapshot.transient_tool_failure(broken))
        verdict = {'status': 'fail', 'tool_failure': True,
                   'id': 'plan-retirement'}
        self.assertFalse(audit_snapshot.transient_tool_failure(verdict))


if __name__ == '__main__':
    unittest.main()
