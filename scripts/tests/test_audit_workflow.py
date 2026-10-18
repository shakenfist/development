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
    job_level_keys, parse_runner_labels, step_action, step_with_inputs,
    strip_yaml_comments, workflow_job_blocks, workflow_step_blocks,
)
from tests.base import repo_text  # noqa: E402

WORKFLOW = '.github/workflows/consistency-audit.yml'

# The jobs that write: issues across the fleet, the compliance page on
# main, and the audit-failure issue here.
WRITING_JOBS = ('manage-issues', 'update-docs', 'report-failure')

# The only jobs that may hold the writing token.
AUDIT_TOKEN_JOBS = ('manage-issues', 'update-docs')

# The only job that pushes, so the only checkout that keeps its credential.
PUSHING_JOBS = ('update-docs',)

MAIN_ONLY = "github.ref == 'refs/heads/main'"

# The environment holding AUDIT_TOKEN. Its deployment branch policy
# allows only main, which is a repository setting and not visible here.
AUDIT_TOKEN_ENVIRONMENT = 'audit-write'

# The ephemeral runners the AUDIT_TOKEN jobs use, and the persistent
# ones they must not: those share a home directory between jobs.
EPHEMERAL_RUNNER = 'vm'
SHARED_RUNNER = 'static'


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

    def test_the_writing_token_jobs_run_in_its_environment(self):
        # AUDIT_TOKEN is an environment secret. Outside the environment
        # it is an empty string, and gh with an empty GH_TOKEN falls
        # back to the runner's own login. And only these jobs: every
        # other job in the environment could name the token too.
        holders = sorted(
            name for name, body in self.jobs.items()
            if job_level_keys(body).get('environment') == AUDIT_TOKEN_ENVIRONMENT
        )
        self.assertEqual(holders, sorted(AUDIT_TOKEN_JOBS))

    def test_the_writing_token_jobs_run_on_ephemeral_runners(self):
        # On a static runner, something an audit leg left in the shared
        # home directory runs with AUDIT_TOKEN.
        for name in AUDIT_TOKEN_JOBS:
            with self.subTest(job=name):
                runs_on = job_level_keys(self.jobs[name]).get('runs-on', '')
                # None is an expression this cannot resolve, which is a
                # runner nobody can check.
                labels = parse_runner_labels(runs_on)
                self.assertIsNotNone(labels, runs_on)
                self.assertIn(EPHEMERAL_RUNNER, labels)
                self.assertNotIn(SHARED_RUNNER, labels)

    def test_the_audit_legs_hold_the_read_token(self):
        audit = self.code(self.jobs['audit'])
        self.assertIn('secrets.AUDIT_READ_TOKEN', audit)
        self.assertNotRegex(audit, r'secrets\.AUDIT_TOKEN\b')
        # An empty GH_TOKEN makes gh fall back to the runner's own login.
        self.assertIn('test -n "${GH_TOKEN}"', audit)

    def test_the_writing_jobs_run_only_from_main(self):
        # The condition must be a conjunction with MAIN_ONLY as one of its
        # terms. Finding MAIN_ONLY anywhere in it is not enough:
        # `always() || github.ref == ...` contains it and runs everywhere.
        for name in WRITING_JOBS:
            with self.subTest(job=name):
                condition = job_level_keys(self.jobs[name]).get('if', '')
                condition = re.sub(r'^\$\{\{(.*)\}\}$', r'\1', condition.strip()).strip()
                self.assertNotIn('||', condition)
                terms = [term.strip() for term in condition.split('&&')]
                self.assertIn(MAIN_ONLY, terms)

    def test_only_the_pushing_job_keeps_its_checkout_credential(self):
        # Every other job runs code with a token in its environment and
        # pushes nothing, so it has no use for a credential left in
        # .git/config.
        keeping = set()
        for name, body in self.jobs.items():
            for step in workflow_step_blocks(body):
                if step_action(step) != 'actions/checkout':
                    continue
                if step_with_inputs(step).get('persist-credentials') != 'false':
                    keeping.add(name)
        self.assertEqual(sorted(keeping), sorted(PUSHING_JOBS))

    def test_the_smoke_job_makes_the_same_assumptions(self):
        # ci.yml's audit-smoke exists to exercise the scheduled run's
        # environment on a pull request, so it drops its checkout
        # credential and asserts the same Python floor.
        smoke = dict(workflow_job_blocks(repo_text('.github/workflows/ci.yml')))['audit-smoke']
        checkouts = [step for step in workflow_step_blocks(smoke)
                     if step_action(step) == 'actions/checkout']
        self.assertTrue(checkouts)
        for step in checkouts:
            self.assertEqual(step_with_inputs(step).get('persist-credentials'), 'false')
        floor = re.search(r"python3 -c 'import sys; sys\.exit\(sys\.version_info.*$",
                          self.code(self.jobs['audit']), re.M)
        self.assertIsNotNone(floor)
        self.assertIn(floor.group(0), self.code(smoke))

    def test_skillsaw_stays_off_path(self):
        # On PATH, skillsaw's venv supplies the python3 the audit runs
        # under. The audit step names the binary in AUDIT_SKILLSAW.
        # Named by the venv's own path: anything else is looked up on a
        # PATH that does not hold it, and llm-context-lint reports
        # error in every repository.
        audit = self.code(self.jobs['audit'])
        self.assertNotIn('GITHUB_PATH', audit)
        self.assertIn('AUDIT_SKILLSAW: ${{ runner.temp }}/skillsaw-venv/bin/skillsaw', audit)

    def test_no_fixed_tmp_paths(self):
        # The static runners share hosts; runner.temp is per runner.
        for workflow in (WORKFLOW, '.github/workflows/ci.yml'):
            with self.subTest(workflow=workflow):
                self.assertNotRegex(
                    self.code(repo_text(workflow)), r'(?<![\w}])/tmp\b')

    def test_runs_of_one_ref_never_overlap(self):
        # Keyed on the ref: one group for everything lets a branch
        # dispatch replace a scheduled run of main waiting in it.
        header = self.code(self.text.split('\njobs:', 1)[0])
        self.assertRegex(
            header,
            r'(?m)^concurrency:\n  group: consistency-audit-\$\{\{ github\.ref \}\}\n'
            r'  cancel-in-progress: false$')


if __name__ == '__main__':
    unittest.main()
