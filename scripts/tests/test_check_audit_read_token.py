#!/usr/bin/env python3

"""Tests for tools/check-audit-read-token.sh, run against a stub gh.

The script is the first step of every audit leg, and its job is to stop
a leg whose token cannot see what the checks read. A probe that passes
a blind token is worse than none: the checks then report `error` for
every repository, or quietly measure less. So each answer GitHub can
give is scripted here and the script's verdict on it asserted, rather
than the script's text read.

Run with: python3 scripts/tests/test_check_audit_read_token.py
"""

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks.github_config import DELETE_BRANCH_ON_MERGE_QUERY  # noqa: E402
from tests.base import REPO_ROOT  # noqa: E402

SCRIPT = os.path.join(REPO_ROOT, 'tools', 'check-audit-read-token.sh')

# Answers the two calls the script makes from FAKE_REST and FAKE_GRAPHQL,
# each either a value to print or 'refused' to fail as gh does, and
# records each call's arguments so a test can see what was asked.
FAKE_GH = textwrap.dedent("""\
    #!/bin/bash
    printf '%s\\n' "$*" >> "${FAKE_GH_LOG}"
    if [ "$1 $2" = 'api graphql' ]; then
        answer="${FAKE_GRAPHQL}"
    else
        answer="${FAKE_REST}"
    fi
    if [ "${answer}" = refused ]; then
        echo 'gh: Bad credentials (HTTP 401)' >&2
        exit 1
    fi
    echo "${answer}"
    """)


class CheckAuditReadTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        bin_dir = os.path.join(self.tmp.name, 'bin')
        os.mkdir(bin_dir)
        gh = os.path.join(bin_dir, 'gh')
        with open(gh, 'w') as f:
            f.write(FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
        self.log = os.path.join(self.tmp.name, 'gh.log')
        self.path = bin_dir + os.pathsep + os.environ.get('PATH', '')

    def probe(self, rest='true', graphql='true', token='a-token'):
        env = {
            'PATH': self.path,
            'GH_TOKEN': token,
            'GITHUB_REPOSITORY': 'shakenfist/example',
            'FAKE_GH_LOG': self.log,
            'FAKE_REST': rest,
            'FAKE_GRAPHQL': graphql,
        }
        return subprocess.run(['bash', SCRIPT], env=env, capture_output=True,
                              text=True, timeout=30)

    def calls(self):
        try:
            with open(self.log) as f:
                return f.read().splitlines()
        except FileNotFoundError:
            return []

    def assert_refused(self, result, containing):
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn(containing, result.stdout)

    def test_a_token_that_sees_both_settings_passes(self):
        result = self.probe()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_an_unset_token_fails_before_asking_github(self):
        # gh given an empty GH_TOKEN uses the runner's own login, so the
        # answer would describe the wrong token.
        self.assert_refused(self.probe(token=''), 'AUDIT_READ_TOKEN is not set')
        self.assertEqual([], self.calls())

    def test_hidden_security_settings_fail(self):
        self.assert_refused(self.probe(rest='false'), 'security_and_analysis: false')

    def test_a_hidden_delete_branch_setting_fails(self):
        self.assert_refused(self.probe(graphql='false'), 'deleteBranchOnMerge: false')

    def test_a_refused_rest_call_fails_with_its_reason(self):
        result = self.probe(rest='refused')
        self.assert_refused(result, 'refused by the API')
        self.assertIn('Bad credentials', result.stderr)

    def test_a_refused_graphql_call_fails_with_its_reason(self):
        result = self.probe(graphql='refused')
        self.assert_refused(result, 'refused by the GraphQL API')
        self.assertIn('Bad credentials', result.stderr)

    def test_the_delete_branch_setting_is_read_through_graphql(self):
        # The REST answer omits it for a read-only fine-grained token.
        self.probe()
        graphql = [call for call in self.calls() if call.startswith('api graphql')]
        self.assertEqual(1, len(graphql), self.calls())
        # The same query the check sends, so the probe vouches for the
        # call the check makes rather than a neighbour of it.
        self.assertIn('query=' + DELETE_BRANCH_ON_MERGE_QUERY, graphql[0])
        self.assertIn('owner=shakenfist', graphql[0])
        self.assertIn('name=example', graphql[0])
        for call in self.calls():
            self.assertNotIn('delete_branch_on_merge', call)


if __name__ == '__main__':
    unittest.main()
