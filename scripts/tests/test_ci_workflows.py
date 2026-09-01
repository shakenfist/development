#!/usr/bin/env python3

"""Tests for audit/checks/ci_workflows.py.

Run with: python3 scripts/tests/test_ci_workflows.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import ci_workflows  # noqa: E402
from audit.text import workflows  # noqa: E402
from tests.base import REPO_ROOT, run_check  # noqa: E402

CI_REVIEW_DEVELOPER_WORKFLOWS = ci_workflows.CI_REVIEW_DEVELOPER_WORKFLOWS
CI_REVIEW_SHARED_ACTION = ci_workflows.CI_REVIEW_SHARED_ACTION
CI_REVIEW_TRIGGER_ACTION = ci_workflows.CI_REVIEW_TRIGGER_ACTION
RETIRED_ADDRESSER_SCRIPTS = ci_workflows.RETIRED_ADDRESSER_SCRIPTS
RETIRED_ADDRESSER_WORKFLOW = ci_workflows.RETIRED_ADDRESSER_WORKFLOW
is_dedicated_scanner_workflow = ci_workflows.is_dedicated_scanner_workflow
workflow_job_blocks = workflows.workflow_job_blocks


def check_ci_review_automation(path, props=None):
    return run_check(ci_workflows.CiReviewAutomation(), path, props)


def check_expensive_lane_path_filter(path, props=None):
    return run_check(ci_workflows.ExpensiveLanePathFilter(), path, props)


def check_merge_group_cancellation(path, props=None, name='testrepo',
                                   org='shakenfist'):
    return run_check(ci_workflows.MergeGroupCancellation(), path, props,
                     name=name, org=org)


class CiReviewAutomationSpecTest(unittest.TestCase):
    """The check and its spec page name the same requirements.

    A rewrite of the page condensed the "What we check" list and
    dropped review-pr-with-claude@main from it, while the check went
    on filing "No workflow uses shared action
    review-pr-with-claude@main" against repositories -- so a
    maintainer following the issue link landed on a page that did not
    state the thing they were being measured against. Deriving the
    agreement is what stops that recurring in a new guise.
    """

    def _measured(self):
        # The "Measured" subsection only. Asserting against the whole
        # page passes on the strength of the auto-generated compliance
        # table at the bottom, which quotes the issue message verbatim
        # -- so the assertion would hold precisely while a repository
        # was being failed for a requirement the page never states.
        # And asserting against all of "What we check" would let a
        # requirement satisfy it from the list the check does *not*
        # measure, which is the opposite claim.
        with open(os.path.join(
                REPO_ROOT, 'docs', 'audits',
                'ci-review-automation.md')) as f:
            spec = f.read()
        start = spec.index('### Measured')
        return spec[start:spec.index('\n### ', start + 1)]

    def test_the_spec_names_every_requirement(self):
        spec = self._measured()
        for requirement in (
            CI_REVIEW_DEVELOPER_WORKFLOWS
            + (CI_REVIEW_SHARED_ACTION,
               CI_REVIEW_TRIGGER_ACTION)
        ):
            with self.subTest(requirement=requirement):
                self.assertIn(requirement, spec)


class ExpensiveLanePathFilterTest(unittest.TestCase):
    """Which expensive lanes are allowed to skip a path filter."""

    LINT_JOB = """  lint:
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
    SCAN_JOB = """  gitleaks:
    runs-on: [self-hosted, vm, debian-13, s]
    steps:
      - run: gitleaks detect
"""
    SKILLSAW_JOB = """  agent-context:
    runs-on: [self-hosted, vm]
    steps:
      - run: pre-commit run skillsaw --all-files
"""

    def _repo(self, tmp, workflows, docs=True):
        wdir = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(wdir)
        if docs:
            os.makedirs(os.path.join(tmp, 'docs'))
            with open(os.path.join(tmp, 'docs', 'index.md'), 'w') as f:
                f.write('# Docs\n')
        for name, content in workflows.items():
            with open(os.path.join(wdir, name), 'w') as f:
                f.write(content)
        return check_expensive_lane_path_filter(
            tmp, {'has_workflows_dir': True}
        )

    def test_dedicated_scanner_workflow_needs_no_filter(self):
        # Reading the text a filter would skip is the whole job.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'gitleaks.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SCAN_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_agent_context_lint_is_a_content_scanner(self):
        # skillsaw reads the text a filter would skip for the same
        # reason gitleaks does: a prompt aimed at an agent lands in a
        # document as readily as a credential.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n' + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_scanner_and_a_context_lint_together_are_exempt(self):
        # The shape client-python arrived at: one ungated workflow
        # holding the credential scan and the context lint, and
        # nothing else.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'supply-chain.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.SKILLSAW_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_a_context_lint_does_not_exempt_the_lanes_beside_it(self):
        # Widening the scanner list must not widen the hole the
        # per-job rule exists to close.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SKILLSAW_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('beside it', result['details'])

    def test_a_context_lint_named_only_in_a_comment_does_not_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # skillsaw runs in the supply chain workflow, not here.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_scanner_does_not_exempt_the_lanes_beside_it(self):
        # shakenfist/actions ran lint, unit tests and the LLM
        # reviewer on ephemeral VMs for every documentation typo,
        # and passed this check, because a gitleaks job sat beside
        # them in the same unfiltered workflow.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('beside it', result['details'])

    def test_a_scanner_named_only_in_a_comment_does_not_count(self):
        # Otherwise one comment in an unrelated lane makes a whole
        # workflow look like a dedicated scanner.
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                + """  lint:
    # gitleaks-scan.sh is a separate workflow's business.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
            )})
        self.assertEqual(result['status'], 'fail')

    def test_a_filtered_mixed_workflow_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\n    paths-ignore:\n'
                "      - 'docs/**'\njobs:\n"
                + self.SCAN_JOB + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'pass')

    def test_an_unfiltered_lane_with_no_scanner_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'functional-tests.yml': (
                'on:\n  pull_request:\njobs:\n' + self.LINT_JOB
            )})
        self.assertEqual(result['status'], 'fail')

    def test_static_runner_lanes_are_not_expensive(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._repo(tmp, {'ci.yml': (
                'on:\n  pull_request:\njobs:\n'
                '  lint:\n'
                '    runs-on: [self-hosted, static]\n'
                '    steps:\n      - run: tox -e pep8\n'
            )})
        self.assertEqual(result['status'], 'pass')


class WorkflowJobBlocksTest(unittest.TestCase):
    def test_jobs_are_split_at_top_level_keys(self):
        blocks = workflow_job_blocks(
            'name: CI\n'
            'on:\n  pull_request:\n'
            'jobs:\n'
            '  lint:\n    runs-on: a\n'
            '  test:\n    runs-on: b\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint', 'test'])
        self.assertIn('runs-on: a', blocks[0][1])
        self.assertIn('runs-on: b', blocks[1][1])

    def test_keys_outside_jobs_are_not_jobs(self):
        # 'pull_request:' under 'on:' is indented exactly like a job
        # key, so a naive split would invent a job called
        # pull_request and decide the workflow is not all scanners.
        blocks = workflow_job_blocks(
            'on:\n  pull_request:\n'
            'jobs:\n  lint:\n    runs-on: a\n'
        )
        self.assertEqual([name for name, _ in blocks], ['lint'])

    def test_a_workflow_with_no_jobs_is_not_a_scanner(self):
        self.assertFalse(
            is_dedicated_scanner_workflow('on:\n  push:\n')
        )


class RetiredCommentAddresserTest(unittest.TestCase):
    """The comment addresser is retired and must not still be deployed.

    It was never used -- review items are worked through interactively
    instead -- and what it leaves behind is a workflow triggered by
    issue_comment holding contents: write on the pull request branch.
    The scripts go with it: address-comments-with-claude.sh was its only
    entry point, and render-review.py plus review-schema.json were only
    ever there for that script to call.
    """

    def _repo(self, tmp, leftovers=()):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        for wf in ('pr-re-review.yml', 'pr-retest.yml'):
            with open(os.path.join(workflows, wf), 'w') as f:
                f.write('uses: shakenfist/actions/pr-bot-trigger@main\n'
                        'uses: shakenfist/actions/'
                        'review-pr-with-claude@main\n')
        for path in leftovers:
            full = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w') as f:
                f.write('x\n')
        return tmp

    def _check(self, leftovers=(), docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, leftovers)
            return check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_repository_without_the_addresser_passes(self):
        result = self._check()
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_workflow_alone_fails(self):
        result = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_scripts_alone_fail(self):
        # Deleting the trigger but keeping the scripts is a half-done
        # job, and the scripts are what the next person copies.
        result = self._check(['tools/address-comments-with-claude.sh'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('address-comments-with-claude.sh', result['details'])

    def test_render_review_and_its_schema_are_reaped_too(self):
        # Nothing else in a project calls render-review.py: the reviewer
        # uses the copy inside shakenfist/actions.
        result = self._check(
            ['tools/render-review.py', 'tools/review-schema.json']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])
        self.assertIn('review-schema.json', result['details'])

    def test_the_whole_chain_is_one_finding(self):
        chain = [RETIRED_ADDRESSER_WORKFLOW] + [
            'tools/%s' % name
            for name in RETIRED_ADDRESSER_SCRIPTS
        ]
        result = self._check(chain)
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['details'].count('still deployed'), 1)
        for path in chain:
            self.assertIn(os.path.basename(path), result['details'])

    def test_scripts_outside_tools_are_found_too(self):
        # tools/ is the canonical home, but deployments put them
        # elsewhere; the check this replaced found a contrib/ copy.
        result = self._check(['contrib/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('contrib/render-review.py', result['details'])

    def test_the_git_directory_is_not_walked(self):
        # .git can hold checked-out state from another branch. Findings
        # from in there are not actionable.
        result = self._check(['.git/stash/render-review.py'])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_docs_only_project_is_checked_too(self):
        # cloudgood is exempt from most of this audit, but a workflow
        # holding contents: write is not a documentation concern.
        result = self._check(
            ['.github/workflows/pr-address-comments.yml'], docs_only=True
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])

    def test_the_reviewer_actions_own_copies_are_not_leftovers(self):
        # shakenfist/actions is in the matrix and is where
        # render-review.py and its schema actually live -- the copies
        # every project's reviewer runs, and the ones this retirement
        # sends projects to instead of their own. The finding says to
        # remove the whole chain in one commit, so reporting these would
        # be telling the maintainer to delete the renderer out from
        # under the reviewer in every repository at once.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            'review-pr-with-claude/review-schema.json',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_exemption_does_not_cover_the_rest_of_the_repository(self):
        # shakenfist/actions carries genuine leftovers of its own next
        # to the action. Exempting the action's directory must not
        # exempt the repository, or the one repository that hosts the
        # replacement is the one that never gets told to clean up.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            '.github/workflows/pr-address-comments.yml',
            'tools/address-comments-with-claude.sh',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-address-comments.yml', result['details'])
        self.assertIn('address-comments-with-claude.sh', result['details'])
        self.assertNotIn('review-pr-with-claude', result['details'])

    def test_any_composite_action_is_exempt_not_just_the_reviewer(self):
        # The exemption keys on action.yml rather than on the reviewer's
        # directory name, so a second action which vendors a renderer of
        # its own does not have to be added here to avoid a false
        # finding. Hardcoding the one name we know about today is how a
        # check acquires a maintenance burden nobody remembers.
        result = self._check([
            'some-other-action/action.yml',
            'some-other-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_yaml_spelling_of_the_manifest_counts(self):
        # Actions accepts action.yaml as readily as action.yml. Missing
        # the spelling produces the exact false finding the exemption
        # exists to prevent, and the finding says to delete everything
        # it names.
        result = self._check([
            'vendored-action/action.yaml',
            'vendored-action/render-review.py',
        ])
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_template_copy_of_the_workflow_is_named(self):
        # The workflow is matched by name anywhere, not only at
        # .github/workflows/. A template directory's copy does not run,
        # but it is the one the next project installs, and the
        # remediation is "remove everything the finding names in one
        # commit" -- so a finding which skipped it would have the
        # maintainer delete the scripts, leave the template, and pass
        # the audit from then on while still propagating the chain.
        result = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'templates/ci-review-automation/pr-address-comments.yml',
            result['details'])

    def test_only_the_installed_workflow_claims_contents_write(self):
        # The finding is the whole content of an auto-filed issue on
        # another repository. Only .github/workflows/ actually runs, so
        # asserting a privileged workflow for a template copy sends the
        # maintainer hunting for one that is not there.
        installed = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assertIn('contents: write', installed['details'])
        template = self._check(
            ['templates/ci-review-automation/pr-address-comments.yml']
        )
        self.assertNotIn('contents: write', template['details'])
        self.assertIn('dead weight', template['details'])

    def test_leftover_scripts_alone_do_not_claim_contents_write(self):
        # The normal state after a partial cleanup: the workflow is
        # gone, the scripts are not.
        result = self._check(['tools/render-review.py'])
        self.assertEqual(result['status'], 'fail')
        self.assertNotIn('contents: write', result['details'])

    def test_the_schema_alone_is_found(self):
        # review-schema.json is only ever exercised beside
        # render-review.py elsewhere in this suite, so a regression
        # which matched only the .py suffix would pass. It is dead on
        # its own too: nothing else in a project reads it.
        result = self._check(['tools/review-schema.json'])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('review-schema.json', result['details'])

    def test_a_docs_only_project_is_checked_for_scripts_too(self):
        # The docs-only branch returns early on the addresser finding.
        # The workflow leftover pins that branch elsewhere; a script
        # leftover takes the same return and had nothing holding it.
        result = self._check(['tools/render-review.py'], docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('render-review.py', result['details'])

    def test_the_exemption_is_the_directory_not_the_name(self):
        # An action.yml exempts the directory it sits in and nothing
        # below it, so a leftover parked one level down is still found.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/old/render-review.py',
        ])
        self.assertEqual(result['status'], 'fail')
        self.assertIn(
            'review-pr-with-claude/old/render-review.py', result['details'])


class PrReReviewTriggerTest(unittest.TestCase):
    """pr-re-review.yml must use pr-bot-trigger, not hand-rolled shell.

    The shared action refuses fork pull requests. Its pr-ref output is
    .head.ref -- a branch name in the head repository, with nothing to
    say which repository that is -- and callers check that name out and
    push to it in their own. A fork pull request opened from the fork's
    default branch names "main". A hand-rolled copy of the trigger
    handling does not get that guard, and did not get any of the other
    fixes made to the action either.
    """

    INLINE = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  check_and_review:\n'
        '    runs-on: [self-hosted, claude-code]\n'
        '    steps:\n'
        '      - name: Check commenter permissions\n'
        '        run: gh api repos/x/collaborators/y/permission\n'
    )
    USES_ACTION = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  trigger-re-review:\n'
        '    runs-on: [self-hosted, static]\n'
        '    steps:\n'
        '      - uses: shakenfist/actions/pr-bot-trigger@main\n'
    )

    def _repo(self, tmp, re_review_body=None):
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        if re_review_body is not None:
            with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
                f.write(re_review_body)
        return tmp

    def _check(self, re_review_body=None, docs_only=False):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, re_review_body)
            return check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_using_the_shared_action_passes(self):
        result = self._check(self.USES_ACTION)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_hand_rolled_trigger_handling_fails(self):
        result = self._check(self.INLINE)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])
        self.assertIn('fork', result['details'])

    def test_an_absent_workflow_is_reported_once_not_twice(self):
        # Its absence is already a finding. Saying "missing" and "does
        # not use the action" about the same missing file is two
        # findings for one problem.
        result = self._check(None)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('Missing pr-re-review.yml', result['details'])
        self.assertNotIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INLINE, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_passes_when_the_action_is_used(self):
        result = self._check(self.USES_ACTION, docs_only=True)
        self.assertEqual(result['status'], 'pass', result['details'])


class PrAutoReviewSecretsInheritTest(unittest.TestCase):
    """The reviewer job must not pass "secrets: inherit".

    pr-auto-review.yml reads no secrets -- it and review-pr-with-claude
    authenticate with github.token from the caller's permissions block
    -- so inheriting buys nothing and hands every secret the calling
    repository holds, publishing tokens included, to a workflow in
    another repository.
    """

    REVIEWER = (
        '  automated_reviewer:\n'
        '    permissions:\n'
        '      contents: read\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'pr-auto-review.yml@main\n'
    )
    INHERITS = REVIEWER + '    secrets: inherit\n'
    # smoke-cluster.yml genuinely needs the cluster secrets. Only the
    # reviewer job is the finding.
    SMOKE_INHERITS = (
        '  smoke:\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'smoke-cluster.yml@main\n'
        '    secrets: inherit\n'
    )

    def _repo(self, tmp, reviewer_job):
        # A compliant repository apart from whatever the reviewer job
        # under test does: both required workflows present, the shared
        # trigger action used, and none of the retired addresser's
        # files deployed. Anything else here shows up as an unrelated
        # finding and masks the one being tested.
        workflows = os.path.join(tmp, '.github', 'workflows')
        os.makedirs(workflows)
        with open(os.path.join(workflows, 'pr-retest.yml'), 'w') as f:
            f.write('uses: shakenfist/actions/'
                    'review-pr-with-claude@main\n')
        with open(os.path.join(workflows, 'pr-re-review.yml'), 'w') as f:
            f.write('  - uses: shakenfist/actions/pr-bot-trigger@main\n')
        with open(os.path.join(workflows, 'ci.yml'), 'w') as f:
            f.write('jobs:\n' + reviewer_job)
        return tmp

    def _check(self, reviewer_job, docs_only=False, extra=None):
        with tempfile.TemporaryDirectory() as tmp:
            self._repo(tmp, reviewer_job)
            for name, content in (extra or {}).items():
                path = os.path.join(tmp, '.github', 'workflows', name)
                with open(path, 'w') as f:
                    f.write(content)
            return check_ci_review_automation(
                tmp, {'is_docs_only': docs_only}
            )

    def test_a_reviewer_without_inherit_passes(self):
        result = self._check(self.REVIEWER)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_reviewer_which_inherits_fails(self):
        result = self._check(self.INHERITS)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])
        self.assertIn('ci.yml', result['details'])

    def test_other_callers_may_inherit(self):
        # smoke-cluster.yml reads real secrets. Sweeping it up in this
        # finding would be telling projects to break their own CI.
        result = self._check(self.REVIEWER + self.SMOKE_INHERITS)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_commented_out_inherit_is_not_a_finding(self):
        commented = self.REVIEWER + '    # secrets: inherit\n'
        result = self._check(commented)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_trailing_comment_does_not_hide_it(self):
        # The realistic evasion. Someone who reads the template text or
        # receives the audit issue is likelier to annotate the line than
        # to delete it, and Actions treats this as plain inherit.
        annotated = self.REVIEWER + (
            '    secrets: inherit  # TODO: drop once migrated\n')
        result = self._check(annotated)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])

    def test_a_quoted_inherit_does_not_hide_it(self):
        for quoted in ("    secrets: 'inherit'\n",
                       '    secrets: "inherit"\n'):
            result = self._check(self.REVIEWER + quoted)
            self.assertEqual(result['status'], 'fail', quoted)
            self.assertIn('ci.yml', result['details'])

    def test_a_named_secret_is_not_inherit(self):
        # The explicit mapping form passes only what it names, which is
        # the false positive worth declining.
        named = self.REVIEWER + (
            '    secrets:\n      MY_TOKEN: ${{ secrets.MY_TOKEN }}\n')
        result = self._check(named)
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INHERITS, docs_only=True)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('secrets: inherit', result['details'])

    def test_every_offending_workflow_is_named(self):
        # Most projects carry the reviewer job in functional-tests.yml
        # rather than ci.yml, so the finding has to name whichever file
        # it found rather than the one the fixtures happen to use. Two
        # at once also exercises the sorted join, which is what the
        # audit issue body shows the person doing the work.
        result = self._check(self.INHERITS, extra={
            'functional-tests.yml': 'jobs:\n' + self.INHERITS,
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertIn('functional-tests.yml', result['details'])
        self.assertLess(result['details'].index('ci.yml'),
                        result['details'].index('functional-tests.yml'))

    def test_a_workflow_with_no_jobs_key_is_skipped(self):
        # workflow_job_blocks finds nothing in a file with no top-level
        # jobs: key. That must skip the file rather than throw, or one
        # malformed workflow stops the check measuring the rest of the
        # repository -- and a check which does not run reports pass.
        result = self._check(self.INHERITS, extra={
            'dependabot-notes.yml': 'on:\n  push:\n',
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml', result['details'])
        self.assertNotIn('dependabot-notes.yml', result['details'])


class MergeGroupCancellationTest(unittest.TestCase):
    """Which merge group jobs must be able to cancel each other."""

    QUEUE_REF_KEY = """    concurrency:
      group: ${{ github.workflow }}-${{ github.ref }}-cluster
      cancel-in-progress: true
"""

    STABLE_KEY = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""

    def _job(self, concurrency='', runs_on='[self-hosted, vm, debian-12, l]',
             condition=''):
        return (
            '  cluster:\n'
            f'    runs-on: {runs_on}\n'
            + (f'    if: {condition}\n' if condition else '')
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    # Whether the repository's merge queue builds one entry at a
    # time. The check asks GitHub; these tests answer for it, both to
    # stay offline and because the interesting case -- a queue that
    # stacks speculatively, where the base_ref key would cancel a live
    # entry -- does not exist in the fleet to point at.
    serial_queue = True

    def setUp(self):
        self._real_serial = ci_workflows.merge_queue_is_serial
        ci_workflows.merge_queue_is_serial = (
            lambda repo_name, org, github=None: self.serial_queue
        )

    def tearDown(self):
        ci_workflows.merge_queue_is_serial = self._real_serial

    def _check(self, workflows):
        with tempfile.TemporaryDirectory() as tmp:
            wdir = os.path.join(tmp, '.github', 'workflows')
            os.makedirs(wdir)
            for name, content in workflows.items():
                with open(os.path.join(wdir, name), 'w') as f:
                    f.write(content)
            return check_merge_group_cancellation(
                tmp, {'has_workflows_dir': True}, 'testrepo', 'shakenfist'
            )

    def _merge_group_workflow(self, job):
        return 'on:\n  pull_request:\n  merge_group:\njobs:\n' + job

    def test_a_queue_ref_key_fails(self):
        # The bug this audit exists for: on merge_group github.ref is
        # gh-readonly-queue/<base>/pr-N-<SHA>, unique per rebuild, so
        # cancel-in-progress never matches.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def test_a_merge_group_aware_key_passes(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_no_concurrency_block_at_all_fails(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job())})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('no concurrency block', result['details'])

    def test_cancel_in_progress_must_be_on(self):
        # A stable key that queues instead of cancelling still leaves
        # the superseded run holding the runner.
        block = """    concurrency:
      group: ${{ github.workflow }}-merge
      cancel-in-progress: false
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('cancel-in-progress is not true', result['details'])

    def test_a_workflow_level_block_covers_a_bare_job(self):
        content = (
            'on:\n  merge_group:\n'
            'concurrency:\n'
            "  group: ${{ github.workflow }}-${{ github.event_name =="
            " 'merge_group' && 'queue' || github.ref }}\n"
            '  cancel-in-progress: true\n'
            'jobs:\n' + self._job()
        )
        result = self._check({'ci.yml': content})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_job_that_cannot_run_on_merge_group_is_out_of_scope(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      condition="github.event_name != 'merge_group'"))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_static_pool_is_out_of_scope(self):
        # Gate jobs and path filters are seconds long on an
        # always-on shared pool; there is nothing to starve.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, static]'))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_self_hosted_pool_without_the_vm_label_is_in_scope(self):
        # instar's ephemeral runners are [self-hosted, debian-12, xl].
        # The sibling path-filter audit's 'vm' test would miss them
        # while an abandoned merge group holds one for two hours.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, debian-12, xl]'))})
        self.assertEqual(result['status'], 'fail')

    def test_a_github_hosted_runner_is_out_of_scope(self):
        # No fleet runner to starve, so the workflow is examined and
        # reports nothing rather than being skipped entirely.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY, runs_on='ubuntu-latest'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_an_unresolvable_runs_on_expression_is_out_of_scope(self):
        # ryll's cross-platform build matrix is runs-on:
        # ${{ matrix.os }}; there is nothing to resolve it against.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='${{ matrix.os }}'))})
        self.assertEqual(result['status'], 'pass', result['details'])
        self.assertIn('0 job(s)', result['details'])

    def test_a_reusable_workflow_is_audited(self):
        # It inherits the caller's event, and a callee published for
        # the fleet cannot know what that event is. This is
        # shakenfist/actions' smoke-cluster.yml.
        result = self._check({'smoke-cluster.yml': (
            'on:\n  workflow_call:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'fail')

    def test_a_reusable_workflow_is_audited_despite_an_in_repo_caller(self):
        # Inferring reachability from in-repo callers exempted
        # smoke-cluster.yml on the strength of a scheduled canary
        # calling it, while every shakenfist merge group ran four
        # nested clusters through it from another repository.
        result = self._check({
            'smoke-cluster.yml': (
                'on:\n  workflow_call:\njobs:\n'
                + self._job(self.QUEUE_REF_KEY)
            ),
            'canary.yml': (
                'on:\n  schedule:\n    - cron: "0 3 * * *"\njobs:\n'
                '  canary:\n'
                '    uses: ./.github/workflows/smoke-cluster.yml\n'
            ),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('smoke-cluster.yml:cluster', result['details'])

    def test_calling_a_reusable_workflow_is_out_of_scope(self):
        # The caller job has no runner of its own; the group that
        # matters is in the callee, audited where it is defined.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_marked_exception_is_allowed(self):
        result = self._check({'test-drift-fix.yml': (
            'on:\n  workflow_call:\n'
            '# audit-ok: merge-group-cancellation -- issue_comment only\n'
            'jobs:\n' + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_comment_quoting_the_bad_key_does_not_count(self):
        # Every fixed workflow explains itself with a comment naming
        # github.ref directly above the corrected key.
        block = """    # github.ref is wrong here on merge_group.
    concurrency:
      group: ${{ github.workflow }}-merge-queue
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_github_sha_key_fails(self):
        # The same defect wearing a different name: on merge_group
        # github.sha is the per-attempt merge commit, not the pull
        # request head, so it is minted afresh on every rebuild.
        block = """    concurrency:
      group: ${{ github.workflow }}-${{ github.sha }}-cluster
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    def _matrix_job(self, concurrency, key='${{ matrix.topology }}'):
        return (
            '  cluster:\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
            + concurrency
            + '    steps:\n      - run: deploy.sh\n'
        )

    def test_matrix_lanes_sharing_one_group_fails(self):
        # The expensive half of getting this wrong: the lanes cancel
        # each other inside a single run, the queue sees a cancelled
        # required check, and the pull request is ejected.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('lanes cancel each other', result['details'])

    def test_a_matrix_key_in_the_group_passes(self):
        block = """    concurrency:
      group: >-
        ${{ github.workflow }}-cluster-${{ matrix.topology }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._matrix_job(block))})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_matrix_of_self_hosted_runners_is_in_scope(self):
        # runs-on: ${{ matrix.runner }} is only unresolvable in the
        # sense that a regex cannot read it. The matrix says what it
        # resolves to, and a whole matrix of cloud builds should not
        # drop out of the audit because of the indirection.
        job = (
            '  cluster:\n'
            '    strategy:\n'
            '      matrix:\n'
            '        runner: [[self-hosted, vm, debian-12, l],\n'
            '                 [self-hosted, vm, debian-12, xl]]\n'
            '    runs-on: ${{ matrix.runner }}\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: deploy.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(job)})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('per-attempt queue ref', result['details'])

    REUSABLE_HEAD = (
        'on:\n'
        '  workflow_call:\n'
        '    inputs:\n'
        '      concurrency_key:\n'
        '        type: string\n'
        '        default: \'\'\n'
        'jobs:\n'
    )

    def test_a_callee_group_made_only_of_caller_contexts_fails(self):
        # Every invocation on one ref renders the same group, so a
        # matrix of four callers cancels itself down to one.
        block = """    concurrency:
      group: >-
        smoke-cluster-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'fail')
        self.assertIn('every invocation on a ref', result['details'])

    def test_a_callee_keyed_on_an_input_passes(self):
        block = """    concurrency:
      group: >-
        smoke-cluster-${{ inputs.concurrency_key }}-${{
        github.event_name == 'merge_group'
        && format('merge_group-{0}', github.event.merge_group.base_ref)
        || github.ref }}
      cancel-in-progress: true
"""
        result = self._check({
            'smoke-cluster.yml': self.REUSABLE_HEAD + self._job(block),
        })
        self.assertEqual(result['status'], 'pass', result['details'])

    def _caller(self, name, extra_with='', matrix=''):
        return (
            f'  {name}:\n'
            + matrix
            + '    uses: shakenfist/actions/.github/workflows/'
            'smoke-cluster.yml@main\n'
            '    with:\n'
            '      component: shakenfist\n'
            + extra_with
        )

    def test_two_invocations_of_one_callee_need_distinct_keys(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier')
            + self._caller('ansible_modules')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('more than once per ref', result['details'])

    def test_distinct_concurrency_keys_pass(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier', '      concurrency_key: merge-tier\n')
            + self._caller(
                'ansible_modules',
                '      concurrency_key: ansible-modules\n')
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_the_same_concurrency_key_twice_fails(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier', '      concurrency_key: full\n')
            + self._caller(
                'ansible_modules', '      concurrency_key: full\n')
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('passes the same concurrency_key', result['details'])

    def test_a_matrix_caller_must_vary_its_key(self):
        # shakenfist runs four nested clusters through one callee from
        # a single matrix job. Varying topology and base image is not
        # enough: the callee keys its group on concurrency_key, and
        # what does not vary there does not separate the lanes.
        matrix = (
            '    strategy:\n'
            '      matrix:\n'
            '        topology: [slim-primary, slim-tier]\n'
        )
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      topology: ${{ matrix.topology }}\n'
                '      concurrency_key: full\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('same for every matrix lane', result['details'])

        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      concurrency_key: ${{ matrix.topology }}\n',
                matrix=matrix)
        )})
        self.assertEqual(result['status'], 'pass', result['details'])

    def test_a_callee_outside_the_fleet_is_reported(self):
        # Nothing here can see its concurrency group, and the caller
        # cannot fix it either.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    uses: someone-else/ci/.github/workflows/build.yml@v1\n'
        )})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('outside the audited fleet', result['details'])

    def test_a_marked_exception_only_exempts_its_own_job(self):
        # The marker used to be read against the whole file, so one
        # job's stated exception silently stopped the other fourteen
        # in an eight hundred line workflow being measured.
        exempt = (
            '  drift:\n'
            '    # audit-ok: merge-group-cancellation -- comment only\n'
            '    runs-on: [self-hosted, vm, debian-12, l]\n'
            + self.QUEUE_REF_KEY
            + '    steps:\n      - run: drift.sh\n'
        )
        result = self._check({'ci.yml': self._merge_group_workflow(
            exempt + self._job(self.QUEUE_REF_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('ci.yml:cluster', result['details'])
        self.assertNotIn('ci.yml:drift', result['details'])

    def test_a_stacking_merge_queue_makes_the_base_ref_key_unsafe(self):
        # The pattern this audit requires aliases every live entry in
        # the queue. That is only safe while the queue builds one at a
        # time, which merge-queue-config is what enforces -- so the
        # precondition is checked rather than left as a note.
        self.serial_queue = False
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assertEqual(result['status'], 'fail')
        self.assertIn('aliases live entries', result['details'])

    def test_a_repo_with_no_merge_group_is_not_applicable(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assertEqual(result['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()


if __name__ == '__main__':
    unittest.main()
