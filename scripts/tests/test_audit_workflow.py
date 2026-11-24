#!/usr/bin/env python3

"""Tests for the guards in .github/workflows/consistency-audit.yml.

The audit workflow holds a token that files and closes issues across
the organisation and pushes to this repository, and runs checks over
files every audited repository controls. What keeps those two apart
is a handful of lines: which secret each step names, which jobs run
only from main, which identity pushes the page. Each is the kind
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
    job_environment, job_level_keys, parse_runner_labels, step_action, step_uses,
    step_with_inputs, strip_yaml_comments, workflow_job_blocks, workflow_step_blocks,
)
from tests.base import REPO_ROOT, repo_text, uses_clauses  # noqa: E402

WORKFLOW = '.github/workflows/consistency-audit.yml'

# The jobs that write, or hand what they generate to one that does:
# issues across the fleet, the compliance page on main, and the
# audit-failure issue here.
WRITING_JOBS = ('manage-issues', 'update-docs', 'push-docs', 'report-failure')

# The only jobs that may hold the writing token.
AUDIT_TOKEN_JOBS = ('manage-issues', 'update-docs')

# The only job that pushes, as the review-marks App: main's ruleset
# admits that App as its bypass actor, and GitHub will not admit
# GITHUB_TOKEN as one.
PUSHING_JOB = 'push-docs'
APP_KEY_ENVIRONMENT = 'review-marks'
APP_TOKEN_ACTION = 'actions/create-github-app-token'

# The other workflow that mints a token from the same App's key, and
# its deployed copy here.
PRUNE_REVIEWS_TEMPLATE = 'templates/review-tracking/prune-reviews.yml'
PRUNE_REVIEWS_DEPLOYED = '.github/workflows/prune-reviews.yml'

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
        # An empty GH_TOKEN makes gh fall back to the runner's own login,
        # so every leg checks the token before anything reads with it.
        # What the check itself does is executed, not read, in
        # test_check_audit_read_token.py.
        steps = workflow_step_blocks(self.jobs['audit'])
        probe = [i for i, step in enumerate(steps)
                 if 'tools/check-audit-read-token.sh' in strip_yaml_comments(step)]
        self.assertEqual(1, len(probe), 'no step runs tools/check-audit-read-token.sh')
        self.assertIn('secrets.AUDIT_READ_TOKEN', steps[probe[0]])
        readers = [i for i, step in enumerate(steps)
                   if 'secrets.AUDIT_READ_TOKEN' in step and i != probe[0]]
        self.assertTrue(readers)
        self.assertLess(probe[0], min(readers))

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

    def test_no_checkout_keeps_its_credential(self):
        # Every job that checks out runs code with a token in its
        # environment and pushes nothing, so it has no use for a
        # credential left in .git/config. The one job that pushes does
        # so with the App's token, from a clone of its own.
        keeping = set()
        for name, body in self.jobs.items():
            for step in workflow_step_blocks(body):
                if step_action(step) != 'actions/checkout':
                    continue
                if step_with_inputs(step).get('persist-credentials') != 'false':
                    keeping.add(name)
        self.assertEqual(sorted(keeping), [])

    def test_only_the_pushing_job_holds_the_app_key(self):
        holders = sorted(
            name for name, body in self.jobs.items()
            if re.search(r'secrets\.REVIEW_MARKS_APP_KEY\b', self.code(body))
        )
        self.assertEqual(holders, [PUSHING_JOB])
        self.assertEqual(sorted(
            name for name, body in self.jobs.items()
            if job_environment(body) == APP_KEY_ENVIRONMENT
        ), [PUSHING_JOB])

    def test_the_pushing_job_runs_on_an_ephemeral_runner_without_a_checkout(self):
        # The App's key bypasses main's ruleset. On a static runner,
        # or reading a workspace an earlier job could have left a .git
        # in, something else could run with it.
        body = self.jobs[PUSHING_JOB]
        labels = parse_runner_labels(job_level_keys(body).get('runs-on', ''))
        self.assertIsNotNone(labels)
        self.assertIn(EPHEMERAL_RUNNER, labels)
        self.assertNotIn(SHARED_RUNNER, labels)
        actions = [step_action(step) for step in workflow_step_blocks(body)]
        self.assertNotIn('actions/checkout', actions)

    def test_the_page_is_pushed_with_the_app_token_alone(self):
        # A fallback to github.token would be refused by the ruleset,
        # with a message about rules rather than the missing key.
        body = self.jobs[PUSHING_JOB]
        self.assertNotIn('github.token', self.code(body))
        self.assertNotIn('GITHUB_TOKEN', self.code(body))
        steps = workflow_step_blocks(body)
        minting = [step for step in steps if step_action(step) == APP_TOKEN_ACTION]
        self.assertEqual(1, len(minting))
        # Pinned to a commit: a moved tag would be handed the key.
        self.assertRegex(step_uses(minting[0]), r'@[0-9a-f]{40}$')
        inputs = step_with_inputs(minting[0])
        self.assertEqual(inputs.get('permission-contents'), 'write')
        # Either would widen the token to every repository the App is
        # installed on.
        self.assertNotIn('owner', inputs)
        self.assertNotIn('repositories', inputs)
        pushing = [step for step in steps if 'scripts/commit-audit-docs.sh' in self.code(step)]
        self.assertEqual(1, len(pushing))
        self.assertIn('PUSH_TOKEN: ${{ steps.app-token.outputs.token }}\n', self.code(pushing[0]))

    def test_every_app_token_mint_is_pinned_alike(self):
        # Every mint hands the action the key of the identity that
        # bypasses main's ruleset, and this workflow's mint step says it
        # pins the commit prune-reviews.yml does. Renovate bumps them
        # together (renovate.json extends the github-actions manager
        # over templates/), but this pin is also bumped by hand, and a
        # hand bump to one file and not another would leave two
        # versions holding that key and the comment false. Every
        # workflow and template is read whole, so a mint added in a new
        # job or file is held to the same pin, and the whole clause is
        # compared, so the commit and the release its comment names
        # cannot drift apart either.
        clauses = {}
        for top in ('.github/workflows', 'templates'):
            for directory, _, names in os.walk(os.path.join(REPO_ROOT, top)):
                for name in names:
                    if not name.endswith(('.yml', '.yaml')):
                        continue
                    path = os.path.relpath(os.path.join(directory, name), REPO_ROOT)
                    for clause in uses_clauses(repo_text(path), APP_TOKEN_ACTION):
                        clauses.setdefault(clause, []).append(path)
        # A walk that found nothing would agree with itself, so the
        # files are named. A new holder of the key is named here on
        # purpose, not picked up quietly.
        found = sorted(path for paths in clauses.values() for path in paths)
        self.assertEqual(found, sorted([WORKFLOW, PRUNE_REVIEWS_DEPLOYED, PRUNE_REVIEWS_TEMPLATE]))
        self.assertEqual(1, len(clauses), clauses)
        # Equal is not enough on its own: two references to a moving
        # tag would agree too.
        self.assertRegex(
            next(iter(clauses)),
            r'^uses: ' + re.escape(APP_TOKEN_ACTION) + r'@[0-9a-f]{40} # v\d+\.\d+\.\d+$')

    def test_the_pushing_job_needs_the_page_but_not_issue_success(self):
        # As update-docs does not wait for manage-issues' success, so
        # neither does this: the implicit success() would skip it when
        # manage-issues failed, throwing a correct page away over one
        # refused issue write. Pinned exactly, as test_registry.py pins
        # the other jobs' gates.
        keys = job_level_keys(self.jobs[PUSHING_JOB])
        self.assertEqual('[update-docs]', keys.get('needs'))
        self.assertEqual(
            "${{ !cancelled() && needs.update-docs.result == 'success' "
            "&& github.ref == 'refs/heads/main' }}",
            keys.get('if'))

    def test_the_pushing_job_gives_github_token_nothing(self):
        # With no scopes, GITHUB_TOKEN cannot be what pushes, whatever a
        # later edit to the push step names.
        self.assertEqual('{}', job_level_keys(self.jobs[PUSHING_JOB]).get('permissions'))

    def test_the_page_is_committed_where_it_was_generated(self):
        # The page carries the audited commit's copy of its own prose.
        # Committed on main's tip instead, it would quietly undo any
        # edit to that prose landed during the run; committed at the
        # audited commit, the script's rebase carries the edit forward.
        code = self.code(self.jobs[PUSHING_JOB])
        self.assertRegex(code, r'checkout --quiet --detach "\$\{GITHUB_SHA\}"')

    def test_the_pushing_job_ignores_the_runner_git_configuration(self):
        # A hook or url rewrite left in the runner's git configuration
        # would run, or redirect, with the token in hand.
        steps = workflow_step_blocks(self.jobs[PUSHING_JOB])
        for marker in ('git clone', 'scripts/commit-audit-docs.sh'):
            matching = [step for step in steps if marker in self.code(step)]
            self.assertEqual(1, len(matching), marker)
            self.assertRegex(self.code(matching[0]), r'GIT_CONFIG_GLOBAL[=:] ?/dev/null', marker)
            self.assertRegex(self.code(matching[0]), r"GIT_CONFIG_NOSYSTEM[=:] ?'?1'?", marker)

    def test_the_pushing_job_checks_its_credentials_before_minting(self):
        # Without a fallback, a missing key or client id should say so
        # by name, before the token step fails with its own message.
        steps = workflow_step_blocks(self.jobs[PUSHING_JOB])
        codes = [self.code(step) for step in steps]
        minting = [i for i, step in enumerate(steps) if step_action(step) == APP_TOKEN_ACTION][0]
        key = [i for i, code in enumerate(codes) if "env.HAS_REVIEW_MARKS_KEY != 'true'" in code]
        client = [i for i, code in enumerate(codes) if "vars.REVIEW_MARKS_APP_CLIENT_ID == ''" in code]
        self.assertEqual(1, len(key))
        self.assertEqual(1, len(client))
        self.assertLess(key[0], minting)
        self.assertLess(client[0], minting)
        for index in key + client:
            self.assertIn('exit 1', codes[index])

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
