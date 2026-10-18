#!/usr/bin/env python3

"""Tests for check-audit-smoke.py.

Run with: python3 scripts/tests/test_check_audit_smoke.py
"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout


SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(SCRIPT_DIR, 'check-audit-smoke.py')

_spec = importlib.util.spec_from_file_location('check_audit_smoke', SCRIPT)
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


def report(checks):
    return {'repo': 'development', 'org': 'shakenfist', 'checks': checks}


MEASURED = {'id': 'llm-context-lint', 'status': 'pass',
            'details': 'Agent context lints clean at error severity'}


class CheckAuditSmokeTest(unittest.TestCase):
    def run_on(self, payload):
        """Run main() over a payload, returning (exit code, output)."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'results.json')
            with open(path, 'w') as f:
                if isinstance(payload, str):
                    f.write(payload)
                else:
                    json.dump(payload, f)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = smoke.main(['check-audit-smoke.py', path])
        return code, out.getvalue() + err.getvalue()

    def test_a_run_that_measured_everything_passes(self):
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'renovate', 'status': 'fail', 'details': 'Missing'},
            {'id': 'sfui-vendor', 'status': 'not_applicable',
             'details': 'Does not vendor sfui'},
        ]))
        self.assertEqual(code, 0, output)

    def test_failing_verdicts_are_not_this_script_s_business(self):
        # This job exists to prove the audit measured, not that it
        # approved. Asserting on verdicts would duplicate the scheduled
        # run, which is what turns a failing check into an issue, and
        # would block every pull request in a repository that has
        # open findings -- which this one does.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'renovate', 'status': 'fail', 'details': 'Missing'},
            {'id': 'readme-structure', 'status': 'fail', 'details': 'Long'},
        ]))
        self.assertEqual(code, 0, output)

    def test_llm_context_lint_going_not_applicable_fails(self):
        # This repository has agent context, so a not_applicable here is
        # the check not running, whatever it says.
        code, output = self.run_on(report([
            {'id': 'llm-context-lint', 'status': 'not_applicable',
             'details': 'No agent context files to lint'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('llm-context-lint', output)
        self.assertIn('No agent context files to lint', output)

    def test_llm_context_lint_missing_its_tool_fails(self):
        # This is the failure the job exists for: a skillsaw that is
        # not installed. It is a tool failure, and it is not excused.
        code, output = self.run_on(report([
            {'id': 'llm-context-lint', 'status': 'error', 'tool_failure': True,
             'details': 'skillsaw could not run'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('skillsaw could not run', output)

    def test_a_network_check_that_could_not_ask_is_listed_not_failed(self):
        # This job has no GitHub token, so the checks that ask GitHub
        # cannot, and say so.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'github-security', 'status': 'error', 'tool_failure': True,
             'details': 'Could not query GitHub for the security settings'},
        ]))
        self.assertEqual(code, 0, output)
        self.assertIn('Not measured without a GitHub token, as expected: '
                      'github-security.', output)
        self.assertIn('measured 1 checks', output)

    def test_a_network_check_that_broke_still_fails(self):
        # Unmarked, the error is a bug in the check, not GitHub.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'github-security', 'status': 'error',
             'details': 'The check raised KeyError'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('github-security raised', output)

    def test_a_tool_failure_outside_the_network_checks_still_fails(self):
        # git or skillsaw not answering on this runner would not answer
        # on the scheduled run either.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'pyproject-usage', 'status': 'error', 'tool_failure': True,
             'details': 'Could not list the tracked files'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('pyproject-usage raised', output)

    def test_llm_context_lint_absent_entirely_fails(self):
        code, output = self.run_on(report([
            {'id': 'renovate', 'status': 'pass', 'details': 'ok'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('did not run at all', output)

    def test_a_check_with_no_status_fails(self):
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'renovate', 'details': 'ok'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('renovate', output)
        self.assertIn('neither passed nor failed', output)

    def test_a_check_that_raised_fails(self):
        # registry.run_check() turns a raise into an `error` result so
        # the other checks still run. The scheduled workflow fails the
        # leg on it; so must this job, or the first sign of a raising
        # check is the next scheduled run across the whole fleet.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'renovate', 'status': 'error',
             'details': 'ValueError: boom'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('renovate raised', output)
        self.assertIn('ValueError: boom', output)

    def test_the_measured_check_raising_fails(self):
        code, output = self.run_on(report([
            {'id': 'llm-context-lint', 'status': 'error',
             'details': 'Traceback'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('llm-context-lint raised', output)

    def test_a_check_with_no_id_fails(self):
        code, output = self.run_on(report([MEASURED, {'status': 'pass'}]))
        self.assertEqual(code, 1)
        self.assertIn('no id', output)

    def test_an_empty_check_list_fails(self):
        code, output = self.run_on(report([]))
        self.assertEqual(code, 1)
        self.assertIn('no checks at all', output)

    def test_truncated_json_fails_rather_than_raising(self):
        # A killed audit leaves a partial file. That must read as a
        # failure, not a traceback the job reports as an error nobody
        # can act on.
        code, output = self.run_on('{"checks": [')
        self.assertEqual(code, 1)
        self.assertIn('parseable JSON', output)

    def test_a_missing_file_fails_rather_than_raising(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = smoke.main(['check-audit-smoke.py', '/nonexistent.json'])
        self.assertEqual(code, 1)
        self.assertIn('parseable JSON', out.getvalue() + err.getvalue())

    def test_wrong_argument_count_is_a_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = smoke.main(['check-audit-smoke.py'])
        self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
