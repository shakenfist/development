#!/usr/bin/env python3

"""Tests for check-audit-smoke.py.

Run with: python3 scripts/test_check_audit_smoke.py
"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout


SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'check-audit-smoke.py'
)

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
        # development is exempt from the audits and fails several checks
        # by design. Asserting on verdicts would make this job a
        # compliance gate on a repository that is deliberately
        # non-compliant.
        code, output = self.run_on(report([
            MEASURED,
            {'id': 'renovate', 'status': 'fail', 'details': 'Missing'},
            {'id': 'readme-structure', 'status': 'fail', 'details': 'Long'},
        ]))
        self.assertEqual(code, 0, output)

    def test_llm_context_lint_going_not_applicable_fails(self):
        # This is the failure the job exists for: skillsaw missing means
        # skillsaw_errors() returns None and the check renders as
        # not_applicable, which is indistinguishable from a decision.
        code, output = self.run_on(report([
            {'id': 'llm-context-lint', 'status': 'not_applicable',
             'details': 'skillsaw is not available in the audit environment'},
        ]))
        self.assertEqual(code, 1)
        self.assertIn('llm-context-lint', output)
        self.assertIn('skillsaw is not available', output)

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
