"""The audit's command lines, run the way the workflows run them.

audit-raised.py is what fails a scheduled leg on a check that raised,
and audit-check.py is what every leg runs first. Both are tested
through their functions elsewhere, but neither main() had been run as
a process, so an exit code the workflow depends on could change with
every test still passing.

Run with: python3 -m unittest tests.test_audit_clis
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_script(name, *args):
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, name)] + list(args),
        capture_output=True, text=True, timeout=60,
    )


class AuditRaisedTest(unittest.TestCase):
    def run_on(self, checks):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'results.json')
            with open(path, 'w') as f:
                json.dump({'repo': 'testrepo', 'org': 'shakenfist',
                           'checks': checks}, f)
            return run_script('audit-raised.py', path)

    def test_one_error_result_exits_one(self):
        result = self.run_on([
            {'id': 'eol-distro', 'status': 'pass', 'details': 'ok'},
            {'id': 'renovate', 'status': 'error',
             'details': 'ValueError: boom'},
        ])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('testrepo: renovate: ValueError: boom', result.stderr)
        self.assertIn('1 check(s) raised', result.stderr)

    def test_all_verdicts_exit_zero(self):
        result = self.run_on([
            {'id': 'eol-distro', 'status': 'pass', 'details': 'ok'},
            {'id': 'renovate', 'status': 'fail', 'details': 'missing'},
            {'id': 'sfui-vendor', 'status': 'not_applicable', 'details': 'no'},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')

    def test_github_not_answering_is_a_warning_not_a_failed_leg(self):
        # A failed leg holds back the compliance page for the whole
        # fleet, which one transient 502 in one repository is not worth.
        # The annotation is escaped: a literal newline would end it.
        result = self.run_on([
            {'id': 'eol-distro', 'status': 'pass', 'details': 'ok'},
            {'id': 'merge-queue-config', 'status': 'error', 'tool_failure': True,
             'details': 'Could not query GitHub: HTTP 502\n50% of calls failed.'},
        ])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            '::warning::testrepo: merge-queue-config: Could not query GitHub: '
            'HTTP 502%0A50%25 of calls failed.\n')
        self.assertEqual(result.stderr, '')

    def test_a_tool_failure_outside_the_network_checks_still_fails(self):
        # The runner's own git, skillsaw or review-tracking.py not
        # answering is not an outage, and the next run would meet it too.
        result = self.run_on([
            {'id': 'llm-context-lint', 'status': 'error', 'tool_failure': True,
             'details': 'skillsaw could not run'},
        ])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('testrepo: llm-context-lint: skillsaw could not run', result.stderr)

    def test_a_network_check_that_raised_still_fails(self):
        result = self.run_on([
            {'id': 'merge-queue-config', 'status': 'error',
             'details': 'The check raised KeyError'},
        ])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('1 check(s) raised', result.stderr)

    def test_an_outage_does_not_hide_a_raise_beside_it(self):
        result = self.run_on([
            {'id': 'merge-queue-config', 'status': 'error', 'tool_failure': True,
             'details': 'HTTP 502'},
            {'id': 'renovate', 'status': 'error', 'details': 'ValueError: boom'},
        ])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('::warning::testrepo: merge-queue-config: HTTP 502', result.stdout)
        self.assertIn('testrepo: renovate: ValueError: boom', result.stderr)
        self.assertNotIn('merge-queue-config', result.stderr)
        self.assertIn('1 check(s) raised', result.stderr)


class AuditCheckTest(unittest.TestCase):
    def test_a_missing_repo_path_exits_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'not-a-clone')
            result = run_script('audit-check.py', '--repo-path', missing,
                                '--repo-name', 'testrepo')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f'{missing} is not a directory', result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
