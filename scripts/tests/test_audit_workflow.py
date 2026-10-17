#!/usr/bin/env python3

"""Tests for the guards in .github/workflows/consistency-audit.yml.

The audit workflow holds a token that files and closes issues across
the organisation and pushes to this repository, and runs checks over
files every audited repository controls. What keeps those two apart
is a handful of lines: which secret each step names, which jobs run
only from main, which checkout keeps its credential. Each is the kind
of line a tidy-up removes without anything else noticing -- actionlint
is happy either way, and so is every run until the one that matters --
so these read the workflow and say which guard went.

Run with: python3 scripts/tests/test_audit_workflow.py
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.text.workflows import (  # noqa: E402
    job_level_keys, step_action, step_with_inputs, strip_yaml_comments,
    workflow_job_blocks, workflow_step_blocks,
)
from tests.base import repo_text  # noqa: E402

WORKFLOW = '.github/workflows/consistency-audit.yml'

# The jobs that write: issues across the fleet, the compliance page on
# main, and the audit-failure issue here.
WRITING_JOBS = ('manage-issues', 'update-docs', 'report-failure')

# The only jobs that may hold the writing token.
AUDIT_TOKEN_JOBS = ('manage-issues', 'update-docs')

MAIN_ONLY = "github.ref == 'refs/heads/main'"


class AuditWorkflowGuardsTest(unittest.TestCase):

    def setUp(self):
        self.text = repo_text(WORKFLOW)
        self.jobs = dict(workflow_job_blocks(self.text))

    def code(self, text):
        return strip_yaml_comments(text, trailing=True)

    def test_the_writing_token_is_only_in_the_writing_jobs(self):
        holders = sorted(
            name for name, body in self.jobs.items()
            if re.search(r'secrets\.AUDIT_TOKEN\b', self.code(body))
        )
        self.assertEqual(holders, sorted(AUDIT_TOKEN_JOBS))

    def test_the_audit_legs_hold_the_read_token(self):
        audit = self.code(self.jobs['audit'])
        self.assertIn('secrets.AUDIT_READ_TOKEN', audit)
        self.assertNotRegex(audit, r'secrets\.AUDIT_TOKEN\b')
        # An empty GH_TOKEN makes gh fall back to the runner's own login.
        self.assertIn('test -n "${GH_TOKEN}"', audit)

    def test_the_writing_jobs_run_only_from_main(self):
        for name in WRITING_JOBS:
            with self.subTest(job=name):
                condition = job_level_keys(self.jobs[name]).get('if', '')
                self.assertIn(MAIN_ONLY, condition)

    def test_the_audit_checkout_drops_its_credential(self):
        checkouts = [
            step for step in workflow_step_blocks(self.jobs['audit'])
            if step_action(step) == 'actions/checkout'
        ]
        self.assertTrue(checkouts)
        for step in checkouts:
            self.assertEqual(
                step_with_inputs(step).get('persist-credentials'), 'false')

    def test_skillsaw_stays_off_path(self):
        # On PATH, skillsaw's venv supplies the python3 the audit runs
        # under. The audit step names the binary in AUDIT_SKILLSAW.
        audit = self.code(self.jobs['audit'])
        self.assertNotIn('GITHUB_PATH', audit)
        self.assertIn('AUDIT_SKILLSAW:', audit)

    def test_no_fixed_tmp_paths(self):
        # The static runners share hosts; runner.temp is per runner.
        for workflow in (WORKFLOW, '.github/workflows/ci.yml'):
            with self.subTest(workflow=workflow):
                self.assertNotRegex(
                    self.code(repo_text(workflow)), r'(?<![\w}])/tmp\b')

    def test_runs_never_overlap(self):
        header = self.code(self.text.split('\njobs:', 1)[0])
        self.assertRegex(
            header,
            r'(?m)^concurrency:\n  group: consistency-audit\n'
            r'  cancel-in-progress: false$')


if __name__ == '__main__':
    unittest.main()
