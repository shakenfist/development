#!/usr/bin/env python3

"""Tests for audit_snapshot.py.

Run with: python3 scripts/test_audit_snapshot.py
"""

import json
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_snapshot  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.join(SCRIPT_DIR, 'audit-check.py')


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
    spurious diffs. Re-derive it from the source: find every check
    function whose body reaches the network -- through the `_github()`
    client accessor, or by cloning -- directly or through a helper it
    calls, and compare.

    Matching on `_github(` rather than on a literal `gh` is what makes
    this survive the client seam: after phase 2 no check spawns `gh`
    itself, they all go through audit/github.py.
    """

    def _source(self):
        with open(CHECKER, 'r') as f:
            return f.read()

    def _functions(self, source):
        """Split the module into {function name: body}."""
        functions = {}
        name = None
        body = []
        for line in source.splitlines():
            match = re.match(r'^def (\w+)', line)
            if match:
                if name:
                    functions[name] = '\n'.join(body)
                name = match.group(1)
                body = []
            elif name:
                body.append(line)
        if name:
            functions[name] = '\n'.join(body)
        return functions

    def _reaches_network(self, functions, name, seen=None):
        seen = seen if seen is not None else set()
        if name in seen:
            return False
        seen.add(name)
        body = functions.get(name, '')
        if re.search(r"_github\(|'clone'", body):
            return True
        for other in functions:
            if other != name and re.search(rf'\b{other}\s*\(', body):
                if self._reaches_network(functions, other, seen):
                    return True
        return False

    def test_network_checks_matches_the_checker(self):
        source = self._source()
        functions = self._functions(source)

        # Map check id to the function check_calls() schedules for it.
        scheduled = dict(re.findall(
            r"\('([a-z0-9-]+)',\s*\n\s*lambda: (\w+)\(", source))
        self.assertTrue(scheduled, 'could not read check_calls()')

        derived = {
            check_id for check_id, function in scheduled.items()
            if self._reaches_network(functions, function)
        }
        self.assertEqual(
            derived, set(audit_snapshot.NETWORK_CHECKS),
            'NETWORK_CHECKS in audit_snapshot.py disagrees with the '
            'checks that actually reach the network',
        )


if __name__ == '__main__':
    unittest.main()
