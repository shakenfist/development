#!/usr/bin/env python3

"""Tests for the path filter in front of this repository's own CI.

.github/workflows/ci.yml skips its two gate jobs for a pull request
that changes only review state. Both back required status checks, and
a skipped job satisfies a required check, so every way this skip can
go wrong reads as green: a ci_paths that fails without !cancelled()
in the gates skips them, an output with no fallback skips them on any
event the filter does not run for, and a filter with the default
quantifier excludes nothing at all. actionlint is happy with every one
of those, so the conditions are pinned here as exact strings, the way
CodeqlTemplateTest pins the same shape in codeql-analysis.yml:
rewording one should be a decision, made by editing this test.

Read through the job and step blocks rather than searched for in the
file, because the comments explaining each condition quote it.

Run with: python3 scripts/tests/test_ci_workflow_guards.py
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.text.workflows import (  # noqa: E402
    indented_block, job_level_keys, job_needs, job_outputs, step_action, step_keys, step_with_inputs,
    strip_yaml_comments, unquote, workflow_job_blocks, workflow_step_blocks,
)
from tests.base import repo_text  # noqa: E402

WORKFLOW = '.github/workflows/ci.yml'

FILTER_JOB = 'ci_paths'
GATED_JOBS = ('lint-and-test', 'audit-smoke')

GATE_IF = "!cancelled() && needs.ci_paths.outputs.code_changed != 'false'"
CODE_CHANGED = "${{ steps.filter.outputs.code || 'true' }}"


class CiPathFilterTest(unittest.TestCase):

    def setUp(self):
        self.text = repo_text(WORKFLOW)
        self.jobs = dict(workflow_job_blocks(self.text))

    def filter_step(self):
        steps = [step for step in workflow_step_blocks(self.jobs[FILTER_JOB])
                 if step_action(step) == 'dorny/paths-filter']
        self.assertEqual(len(steps), 1, f'{FILTER_JOB} must run paths-filter exactly once')
        return steps[0]

    def test_the_filter_job_has_its_own_check_name(self):
        # It is a required check, and a required check matches by name:
        # codeql-analysis.yml already has a "Check paths" job, so this
        # one sharing a name with it would let either satisfy the other.
        keys = job_level_keys(self.jobs[FILTER_JOB])
        self.assertEqual('CI paths', unquote(keys.get('name', '')))

    def test_the_output_fails_open(self):
        # The filter step does not run on workflow_dispatch, which is
        # how pr-retest.yml re-runs this workflow; without the fallback
        # the output is empty there. The gates' != 'false' test would
        # still run them, but only by that accident of comparison.
        outputs = job_outputs(self.jobs[FILTER_JOB])
        self.assertEqual(CODE_CHANGED, outputs.get('code_changed'),
                         'code_changed must fall back to \'true\' when the filter step did not run '
                         'or produced nothing')

    def test_the_filter_job_never_checks_out_the_pull_request(self):
        # It runs on a long-lived static runner and reads the changed
        # files from the API, so a checkout would only put a fork's
        # content on that runner.
        job = self.jobs[FILTER_JOB]
        actions = [step_action(step) for step in workflow_step_blocks(job)]
        self.assertNotIn('actions/checkout', actions)
        self.assertNotIn('actions/checkout', strip_yaml_comments(job, trailing=True))

    def test_the_filter_runs_only_where_there_is_a_pull_request(self):
        # The one event with a pull request payload for the action to
        # read. A != 'workflow_dispatch' guard reads the same today and
        # breaks the day another trigger is added.
        keys = step_keys(self.filter_step())
        self.assertEqual("github.event_name == 'pull_request'", keys.get('if'))
        # The output reads steps.filter, so an id that drifted would
        # make it fall back to 'true' on every run and skip nothing.
        self.assertEqual('filter', keys.get('id'))

    def test_the_exclusions_are_review_state_alone(self):
        step = self.filter_step()
        # Under the default 'some', the bare '**' matches every file and
        # every '!' exclusion below is a no-op.
        self.assertEqual("'every'", step_with_inputs(step).get('predicate-quantifier'),
                         'without every, the \'!\' exclusions are no-ops')
        code = indented_block(step, 'code')
        self.assertIsNotNone(code, 'the filter must define a code filter')
        patterns = re.findall(r"^\s+- '(.+)'$", code, re.M)
        # The three review state paths, written inline rather than as a
        # constant: this test reads the patterns, not the files, and
        # test_hooks.py takes a constant naming a tracked file as a file
        # the suite reads. The scope config is deliberately absent: it
        # decides what is reviewed at all, so a change to it is linted
        # and reviewed like code.
        self.assertEqual(['**', '!REVIEWS.md', '!.vscode/*.weaudit', '!.vscode/*.weaudit-shas.json'], patterns)
        self.assertNotIn('review-scope.toml', code)

    def test_the_trigger_is_not_path_filtered(self):
        # A workflow filtered at the trigger never starts, so its
        # required checks never report and the pull request can never
        # merge. The filter has to be a job for exactly that reason.
        on = indented_block(self.text, 'on')
        self.assertIsNotNone(on)
        pull_request = indented_block(on, 'pull_request')
        self.assertIsNotNone(pull_request, 'ci.yml must run on pull_request')
        self.assertNotIn('paths', pull_request)

    def test_the_gates_fail_open(self):
        # Without !cancelled() a failed ci_paths skips its dependants,
        # and a skipped required check reads as passed. Only a filter
        # that positively said 'false' may skip a gate.
        for name in GATED_JOBS:
            with self.subTest(job=name):
                body = self.jobs[name]
                self.assertIn(FILTER_JOB, job_needs(body))
                self.assertEqual(
                    GATE_IF, re.sub(r'\s+', ' ', job_level_keys(body).get('if', '')),
                    f'{name} must run unless {FILTER_JOB} positively reported a review-only pull request '
                    '(!cancelled() and the != \'false\' test)')


if __name__ == '__main__':
    unittest.main()
