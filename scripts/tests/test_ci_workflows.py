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
from tests.base import CheckTestCase, REPO_ROOT, repo_text  # noqa: E402

CI_REVIEW_DEVELOPER_WORKFLOWS = ci_workflows.CI_REVIEW_DEVELOPER_WORKFLOWS
CI_REVIEW_SHARED_ACTION = ci_workflows.CI_REVIEW_SHARED_ACTION
CI_REVIEW_TRIGGER_ACTION = ci_workflows.CI_REVIEW_TRIGGER_ACTION
RETIRED_ADDRESSER_SCRIPTS = ci_workflows.RETIRED_ADDRESSER_SCRIPTS
RETIRED_ADDRESSER_WORKFLOW = ci_workflows.RETIRED_ADDRESSER_WORKFLOW
is_dedicated_scanner_workflow = ci_workflows.is_dedicated_scanner_workflow
workflow_job_blocks = workflows.workflow_job_blocks


def template_workflow(wf):
    """The canonical copy of a ci-review-automation workflow.

    The fixture for a compliant deployment. A stub would pass only the
    requirements it was written for, and the next one added to the
    criterion would turn every test built on it into a failure about
    something else; the template is what adopters copy, so it is also
    what the criterion has to accept.
    """
    return repo_text('templates', 'ci-review-automation', wf)


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


class ExpensiveLanePathFilterTest(CheckTestCase):
    """Which expensive lanes are allowed to skip a path filter."""

    check_class = ci_workflows.ExpensiveLanePathFilter

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

    def _check(self, workflows):
        """The check over a repository made of these workflows.

        Every fixture carries a docs/ directory, because the filter
        this check demands has to exclude the repository's non-code
        content and a repository with no docs/ is a different case
        from the ones these tests are about.
        """
        self.fixture.write('docs/index.md', '# Docs\n')
        self.fixture.workflows(workflows)
        return self.check(has_workflows_dir=True)

    def test_dedicated_scanner_workflow_needs_no_filter(self):
        # Reading the text a filter would skip is the whole job.
        result = self._check({'gitleaks.yml': (
            'on:\n  pull_request:\njobs:\n' + self.SCAN_JOB
        )})
        self.assert_pass(result)

    def test_agent_context_lint_is_a_content_scanner(self):
        # skillsaw reads the text a filter would skip for the same
        # reason gitleaks does: a prompt aimed at an agent lands in a
        # document as readily as a credential.
        result = self._check({'supply-chain.yml': (
            'on:\n  pull_request:\njobs:\n' + self.SKILLSAW_JOB
        )})
        self.assert_pass(result)

    def test_a_scanner_and_a_context_lint_together_are_exempt(self):
        # The shape client-python arrived at: one ungated workflow
        # holding the credential scan and the context lint, and
        # nothing else.
        result = self._check({'supply-chain.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self.SCAN_JOB + self.SKILLSAW_JOB
        )})
        self.assert_pass(result)

    def test_a_context_lint_does_not_exempt_the_lanes_beside_it(self):
        # Widening the scanner list must not widen the hole the
        # per-job rule exists to close.
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self.SKILLSAW_JOB + self.LINT_JOB
        )})
        self.assert_fail(result, containing='beside it')

    def test_a_context_lint_named_only_in_a_comment_does_not_count(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + """  lint:
    # skillsaw runs in the supply chain workflow, not here.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
        )})
        self.assert_fail(result)

    def test_a_scanner_does_not_exempt_the_lanes_beside_it(self):
        # shakenfist/actions ran lint, unit tests and the LLM
        # reviewer on ephemeral VMs for every documentation typo,
        # and passed this check, because a gitleaks job sat beside
        # them in the same unfiltered workflow.
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self.SCAN_JOB + self.LINT_JOB
        )})
        self.assert_fail(result, containing='ci.yml')
        self.assertIn('beside it', result['details'])

    def test_a_scanner_named_only_in_a_comment_does_not_count(self):
        # Otherwise one comment in an unrelated lane makes a whole
        # workflow look like a dedicated scanner.
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + """  lint:
    # gitleaks-scan.sh is a separate workflow's business.
    runs-on: [self-hosted, vm, debian-12, s]
    steps:
      - run: tox -e pep8
"""
        )})
        self.assert_fail(result)

    def test_a_filtered_mixed_workflow_passes(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\n    paths-ignore:\n'
            "      - 'docs/**'\njobs:\n"
            + self.SCAN_JOB + self.LINT_JOB
        )})
        self.assert_pass(result)

    def test_an_unfiltered_lane_with_no_scanner_fails(self):
        result = self._check({'functional-tests.yml': (
            'on:\n  pull_request:\njobs:\n' + self.LINT_JOB
        )})
        self.assert_fail(result)

    def test_static_runner_lanes_are_not_expensive(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            '  lint:\n'
            '    runs-on: [self-hosted, static]\n'
            '    steps:\n      - run: tox -e pep8\n'
        )})
        self.assert_pass(result)


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


class RetiredCommentAddresserTest(CheckTestCase):
    """The comment addresser is retired and must not still be deployed.

    It was never used -- review items are worked through interactively
    instead -- and what it leaves behind is a workflow triggered by
    issue_comment holding contents: write on the pull request branch.
    The scripts go with it: address-comments-with-claude.sh was its only
    entry point, and render-review.py plus review-schema.json were only
    ever there for that script to call.
    """

    check_class = ci_workflows.CiReviewAutomation

    def _check(self, leftovers=(), docs_only=False):
        """The check over a clean repository plus the named leftovers.

        A fresh fixture per call, via CheckTestCase.fresh_fixture,
        because the case which separates the installed workflow from a
        template copy runs the check twice, and the first call's
        leftovers would be findings in the second.
        """
        self.fresh_fixture()
        self.fixture.workflows({
            wf: template_workflow(wf)
            for wf in ('pr-re-review.yml', 'pr-retest.yml')
        })
        self.fixture.write_all({path: 'x\n' for path in leftovers})
        return self.check(is_docs_only=docs_only)

    def test_a_repository_without_the_addresser_passes(self):
        self.assert_pass(self._check())

    def test_the_workflow_alone_fails(self):
        result = self._check(
            ['.github/workflows/pr-address-comments.yml']
        )
        self.assert_fail(result, containing='pr-address-comments.yml')

    def test_the_scripts_alone_fail(self):
        # Deleting the trigger but keeping the scripts is a half-done
        # job, and the scripts are what the next person copies.
        result = self._check(['tools/address-comments-with-claude.sh'])
        self.assert_fail(result,
                         containing='address-comments-with-claude.sh')

    def test_render_review_and_its_schema_are_reaped_too(self):
        # Nothing else in a project calls render-review.py: the reviewer
        # uses the copy inside shakenfist/actions.
        result = self._check(
            ['tools/render-review.py', 'tools/review-schema.json']
        )
        self.assert_fail(result, containing='render-review.py')
        self.assertIn('review-schema.json', result['details'])

    def test_the_whole_chain_is_one_finding(self):
        chain = [RETIRED_ADDRESSER_WORKFLOW] + [
            'tools/%s' % name
            for name in RETIRED_ADDRESSER_SCRIPTS
        ]
        result = self._check(chain)
        self.assert_fail(result)
        self.assertEqual(result['details'].count('still deployed'), 1)
        for path in chain:
            self.assertIn(os.path.basename(path), result['details'])

    def test_scripts_outside_tools_are_found_too(self):
        # tools/ is the canonical home, but deployments put them
        # elsewhere; the check this replaced found a contrib/ copy.
        result = self._check(['contrib/render-review.py'])
        self.assert_fail(result, containing='contrib/render-review.py')

    def test_the_git_directory_is_not_walked(self):
        # .git can hold checked-out state from another branch. Findings
        # from in there are not actionable.
        self.assert_pass(self._check(['.git/stash/render-review.py']))

    def test_a_docs_only_project_is_checked_too(self):
        # cloudgood is exempt from most of this audit, but a workflow
        # holding contents: write is not a documentation concern.
        result = self._check(
            ['.github/workflows/pr-address-comments.yml'], docs_only=True
        )
        self.assert_fail(result, containing='pr-address-comments.yml')

    def test_the_reviewer_actions_own_copies_are_not_leftovers(self):
        # shakenfist/actions is in the matrix and is where
        # render-review.py and its schema actually live -- the copies
        # every project's reviewer runs, and the ones this retirement
        # sends projects to instead of their own. The finding says to
        # remove the whole chain in one commit, so reporting these would
        # be telling the maintainer to delete the renderer out from
        # under the reviewer in every repository at once.
        self.assert_pass(self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/render-review.py',
            'review-pr-with-claude/review-schema.json',
        ]))

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
        self.assert_fail(result, containing='pr-address-comments.yml')
        self.assertIn('address-comments-with-claude.sh', result['details'])
        self.assertNotIn('review-pr-with-claude', result['details'])

    def test_any_composite_action_is_exempt_not_just_the_reviewer(self):
        # The exemption keys on action.yml rather than on the reviewer's
        # directory name, so a second action which vendors a renderer of
        # its own does not have to be added here to avoid a false
        # finding. Hardcoding the one name we know about today is how a
        # check acquires a maintenance burden nobody remembers.
        self.assert_pass(self._check([
            'some-other-action/action.yml',
            'some-other-action/render-review.py',
        ]))

    def test_the_yaml_spelling_of_the_manifest_counts(self):
        # Actions accepts action.yaml as readily as action.yml. Missing
        # the spelling produces the exact false finding the exemption
        # exists to prevent, and the finding says to delete everything
        # it names.
        self.assert_pass(self._check([
            'vendored-action/action.yaml',
            'vendored-action/render-review.py',
        ]))

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
        self.assert_fail(
            result,
            containing='templates/ci-review-automation/'
                       'pr-address-comments.yml')

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
        self.assert_fail(result)
        self.assertNotIn('contents: write', result['details'])

    def test_the_schema_alone_is_found(self):
        # review-schema.json is only ever exercised beside
        # render-review.py elsewhere in this suite, so a regression
        # which matched only the .py suffix would pass. It is dead on
        # its own too: nothing else in a project reads it.
        result = self._check(['tools/review-schema.json'])
        self.assert_fail(result, containing='review-schema.json')

    def test_a_docs_only_project_is_checked_for_scripts_too(self):
        # The docs-only branch returns early on the addresser finding.
        # The workflow leftover pins that branch elsewhere; a script
        # leftover takes the same return and had nothing holding it.
        result = self._check(['tools/render-review.py'], docs_only=True)
        self.assert_fail(result, containing='render-review.py')

    def test_the_exemption_is_the_directory_not_the_name(self):
        # An action.yml exempts the directory it sits in and nothing
        # below it, so a leftover parked one level down is still found.
        result = self._check([
            'review-pr-with-claude/action.yml',
            'review-pr-with-claude/old/render-review.py',
        ])
        self.assert_fail(
            result, containing='review-pr-with-claude/old/render-review.py')


class PrReReviewTriggerTest(CheckTestCase):
    """pr-re-review.yml must use pr-bot-trigger, not hand-rolled shell.

    The shared action refuses fork pull requests. Its pr-ref output is
    .head.ref -- a branch name in the head repository, with nothing to
    say which repository that is -- and callers check that name out and
    push to it in their own. A fork pull request opened from the fork's
    default branch names "main". A hand-rolled copy of the trigger
    handling does not get that guard, and did not get any of the other
    fixes made to the action either.
    """

    check_class = ci_workflows.CiReviewAutomation

    INLINE = (
        'name: PR Re-review\n'
        'on:\n  issue_comment:\n    types: [created]\n'
        'jobs:\n  check_and_review:\n'
        '    runs-on: [self-hosted, claude-code]\n'
        '    steps:\n'
        '      - name: Check commenter permissions\n'
        '        run: gh api repos/x/collaborators/y/permission\n'
    )
    USES_ACTION = template_workflow('pr-re-review.yml')

    def _check(self, re_review_body=None, docs_only=False):
        """The check over a repository whose pr-re-review.yml is this.

        A body of None means the workflow is absent, which is a case of
        its own rather than an empty one.
        """
        self.fixture.workflow('pr-retest.yml', template_workflow('pr-retest.yml'))
        if re_review_body is not None:
            self.fixture.workflow('pr-re-review.yml', re_review_body)
        return self.check(is_docs_only=docs_only)

    def test_using_the_shared_action_passes(self):
        self.assert_pass(self._check(self.USES_ACTION))

    def test_hand_rolled_trigger_handling_fails(self):
        result = self._check(self.INLINE)
        self.assert_fail(result, containing='pr-bot-trigger@main')
        self.assertIn('fork', result['details'])

    def test_an_absent_workflow_is_reported_once_not_twice(self):
        # Its absence is already a finding. Saying "missing" and "does
        # not use the action" about the same missing file is two
        # findings for one problem.
        result = self._check(None)
        self.assert_fail(result, containing='Missing pr-re-review.yml')
        self.assertNotIn('pr-bot-trigger@main', result['details'])

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INLINE, docs_only=True)
        self.assert_fail(result, containing='pr-bot-trigger@main')

    def test_the_docs_only_path_passes_when_the_action_is_used(self):
        self.assert_pass(self._check(self.USES_ACTION, docs_only=True))


class ForkGateAndConfirmStepTest(CheckTestCase):
    """Deployed copies must carry the fork gate and the confirm step.

    Both were fixed in the templates (shakenfist/development#172, #174),
    and every adopter had copied the earlier versions verbatim, so the
    fix reaches the fleet only if the criterion measures it. Each case
    below is a mutation of the real template, so a template edit that
    the criterion no longer accepts fails here rather than in twenty
    repositories the next morning.
    """

    check_class = ci_workflows.CiReviewAutomation

    SAME_REPO_OUTPUT = '      same_repo: ${{ steps.trigger.outputs.same-repo }}\n'
    RETEST_FORK_GATE = (
        "needs.trigger-retest.outputs.authorized == 'true' &&\n"
        "      needs.trigger-retest.outputs.same_repo == 'true'")
    RE_REVIEW_FORK_GATE = (
        "needs.trigger-re-review.outputs.authorized == 'true' &&\n"
        "      needs.trigger-re-review.outputs.same_repo == 'true'")
    CONFIRM_STEP = '      - name: Confirm the checkout is the validated commit\n'
    TRIGGER_STEP = '      - name: Handle trigger\n        id: trigger\n'

    def _mutate(self, wf, old, new):
        body = template_workflow(wf)
        self.assertIn(old, body, f'the {wf} template no longer carries '
                      f'the text this mutation replaces')
        return body.replace(old, new)

    def _check(self, re_review=None, retest=None, docs_only=False):
        self.fixture.workflows({
            'pr-re-review.yml': re_review or template_workflow('pr-re-review.yml'),
            'pr-retest.yml': retest or template_workflow('pr-retest.yml'),
        })
        return self.check(is_docs_only=docs_only)

    def test_the_templates_pass(self):
        self.assert_pass(self._check())

    def test_retest_without_the_same_repo_export_fails(self):
        # The half the round-two review of #180 found unguarded: the
        # `if:` still names same_repo, but it now reads empty.
        retest = self._mutate('pr-retest.yml', self.SAME_REPO_OUTPUT, '')
        self.assert_fail(self._check(retest=retest), containing=(
            "pr-retest.yml: job trigger-retest does not export "
            "pr-bot-trigger's same-repo output"))

    def test_re_review_without_the_same_repo_export_fails(self):
        re_review = self._mutate('pr-re-review.yml', self.SAME_REPO_OUTPUT, '')
        self.assert_fail(self._check(re_review=re_review), containing=(
            'pr-re-review.yml: job trigger-re-review does not export'))

    def test_retest_gated_on_authorized_alone_fails(self):
        retest = self._mutate(
            'pr-retest.yml', self.RETEST_FORK_GATE,
            "needs.trigger-retest.outputs.authorized == 'true'")
        self.assert_fail(self._check(retest=retest), containing=(
            'pr-retest.yml: job retest does not require '
            'needs.trigger-retest.outputs.same_repo'))

    def test_re_review_gated_on_authorized_alone_fails(self):
        re_review = self._mutate(
            'pr-re-review.yml', self.RE_REVIEW_FORK_GATE,
            "needs.trigger-re-review.outputs.authorized == 'true'")
        self.assert_fail(self._check(re_review=re_review), containing=(
            'pr-re-review.yml: job re-review does not require'))

    def test_a_gate_only_quoted_in_a_comment_fails(self):
        # The comments above each `if:` quote the expression they
        # explain, so the gate has to be read from the key itself.
        retest = self._mutate(
            'pr-retest.yml', self.RETEST_FORK_GATE,
            "needs.trigger-retest.outputs.authorized == 'true'\n"
            "    # needs.trigger-retest.outputs.same_repo == 'true'")
        self.assert_fail(self._check(retest=retest),
                         containing='job retest does not require')

    def test_renamed_jobs_are_followed_by_role(self):
        # Jobs are found by what they do, so a repository which renamed
        # them is measured on the same property, not failed on a name.
        retest = template_workflow('pr-retest.yml').replace(
            'trigger-retest', 'handle-comment')
        self.assert_pass(self._check(retest=retest))

    def test_a_work_job_which_no_longer_needs_the_trigger_fails(self):
        # Nothing to follow the gate into is not the same as a gate.
        retest = self._mutate(
            'pr-retest.yml', '    needs: trigger-retest\n', '')
        self.assert_fail(self._check(retest=retest), containing=(
            'pr-retest.yml: no job needs trigger-retest'))

    def test_every_spelling_of_needs_is_followed(self):
        # The round-three review of #180 found the block sequence read
        # as no dependency at all, which reported "no job needs" on a
        # workflow whose gate was completely wired.
        for spelling in ('    needs: [trigger-retest]\n',
                         "    needs: ['trigger-retest']\n",
                         '    needs:\n      - trigger-retest\n',
                         '    needs:\n      # the trigger\n'
                         '      - trigger-retest  # the one\n'):
            with self.subTest(spelling=spelling):
                self.fresh_fixture()
                retest = self._mutate(
                    'pr-retest.yml', '    needs: trigger-retest\n', spelling)
                self.assert_pass(self._check(retest=retest))

    def test_an_ungated_job_with_a_block_sequence_needs_fails(self):
        # Reading the block form must count the job in, not merely stop
        # the "no job needs" finding: an ungated dependant still fails.
        retest = self._mutate(
            'pr-retest.yml', '    needs: trigger-retest\n',
            '    needs:\n      - trigger-retest\n')
        retest = retest.replace(
            self.RETEST_FORK_GATE,
            "needs.trigger-retest.outputs.authorized == 'true'")
        self.assert_fail(self._check(retest=retest), containing=(
            'pr-retest.yml: job retest does not require '
            'needs.trigger-retest.outputs.same_repo'))

    def test_retest_without_the_trigger_action_fails(self):
        retest = self._mutate(
            'pr-retest.yml',
            'uses: shakenfist/actions/pr-bot-trigger@main',
            'run: echo hand-rolled')
        self.assert_fail(self._check(retest=retest), containing=(
            'pr-retest.yml has no shakenfist/actions/pr-bot-trigger@main '
            'step'))

    def test_a_hand_rolled_re_review_is_reported_once(self):
        re_review = self._mutate(
            'pr-re-review.yml',
            'uses: shakenfist/actions/pr-bot-trigger@main',
            'run: echo hand-rolled')
        result = self._check(re_review=re_review)
        self.assert_fail(result, containing='does not use')
        self.assertNotIn('has no shakenfist/actions/pr-bot-trigger@main',
                         result['details'])

    def test_a_conditional_confirm_step_fails(self):
        # The shape every adopter copied before #172.
        re_review = self._mutate(
            'pr-re-review.yml', self.CONFIRM_STEP,
            self.CONFIRM_STEP
            + "        if: steps.ref.outputs.merged == 'true'\n")
        self.assert_fail(self._check(re_review=re_review), containing=(
            "confirm step is conditional "
            "(if: steps.ref.outputs.merged == 'true')"))

    def test_a_conditional_confirm_step_with_the_dash_alone_fails(self):
        # With the dash on its own line the step used to merge into the
        # one before it, and its `if:` went unread.
        re_review = self._mutate(
            'pr-re-review.yml', self.CONFIRM_STEP,
            '      -\n' + self.CONFIRM_STEP.replace('      - ', '        ')
            + "        if: steps.ref.outputs.merged == 'true'\n")
        self.assert_fail(self._check(re_review=re_review),
                         containing='confirm step is conditional')

    def test_a_trigger_step_with_the_dash_alone_passes(self):
        retest = self._mutate(
            'pr-retest.yml', self.TRIGGER_STEP,
            '      -\n' + self.TRIGGER_STEP.replace('      - ', '        '))
        self.assert_pass(self._check(retest=retest))

    def test_a_trigger_step_without_an_id_fails(self):
        retest = self._mutate(
            'pr-retest.yml', self.TRIGGER_STEP,
            self.TRIGGER_STEP.replace('        id: trigger\n', ''))
        self.assert_fail(self._check(retest=retest), containing=(
            'pr-retest.yml: the shakenfist/actions/pr-bot-trigger@main '
            'step in job trigger-retest has no id:'))

    def test_a_confirm_step_without_the_head_comparison_fails(self):
        re_review = self._mutate(
            'pr-re-review.yml',
            'if ! reviewed=$(git rev-parse HEAD 2>/dev/null); then',
            'if ! reviewed=$(echo "${HEAD_SHA}"); then')
        self.assert_fail(self._check(re_review=re_review),
                         containing='merge path only')

    def test_no_confirm_step_fails(self):
        body = template_workflow('pr-re-review.yml')
        start = body.index(self.CONFIRM_STEP)
        end = body.index('\n      # Which ref was reviewed', start)
        result = self._check(re_review=body[:start] + body[end + 1:])
        self.assert_fail(result, containing=(
            'pr-re-review.yml does not confirm that the tree it checked '
            'out is the commit it resolved'))

    def test_the_docs_only_path_checks_it_too(self):
        re_review = self._mutate('pr-re-review.yml', self.SAME_REPO_OUTPUT, '')
        self.fixture.workflow('pr-re-review.yml', re_review)
        self.assert_fail(self.check(is_docs_only=True),
                         containing='does not export')


class JobNeedsTest(unittest.TestCase):
    """Every spelling of `needs:`, and only the job's own."""

    def test_each_spelling(self):
        for needs, expected in (
                ('    needs: build\n', ['build']),
                ("    needs: 'build'\n", ['build']),
                ('    needs: [build, test-unit]\n', ['build', 'test-unit']),
                ('    needs: [\n      build,\n      test-unit\n    ]\n',
                 ['build', 'test-unit']),
                ('    needs:\n      - build\n      - "test-unit"\n',
                 ['build', 'test-unit']),
                ('    needs:\n      - build  # first\n'
                 '      # - commented-out\n      - test-unit\n',
                 ['build', 'test-unit']),
                ('    runs-on: ubuntu-latest\n', [])):
            with self.subTest(needs=needs):
                self.assertEqual(expected, workflows.job_needs(
                    needs + '    steps:\n      - run: echo hi\n'))

    def test_the_block_ends_at_the_next_job_key(self):
        body = ('    needs:\n      - build\n'
                '    if: always()\n'
                '    steps:\n      - run: echo test\n')
        self.assertEqual(['build'], workflows.job_needs(body))

    def test_a_needs_key_inside_a_step_is_not_the_jobs(self):
        # The job's own block comes after the step's here, so reading
        # the first `needs:` at any depth would answer with the step's.
        body = ('    runs-on: ubuntu-latest\n'
                '    steps:\n'
                '      - uses: some/action@v1\n'
                '        with:\n'
                '          needs:\n'
                '            - lint\n'
                '    needs:\n'
                '      - build\n')
        self.assertEqual(['build'], workflows.job_needs(body))
        self.assertEqual([], workflows.job_needs(body.split('    needs:')[0]))


class StepKeysTest(unittest.TestCase):
    """A step's own keys, not its inputs' or its script's."""

    def test_the_key_after_the_sequence_marker_is_a_step_key(self):
        step = ('      - name: Confirm\n'
                "        if: steps.ref.outputs.merged == 'true'\n"
                '        run: |\n'
                '          if [ x ]; then exit 1; fi\n')
        keys = workflows.step_keys(step)
        self.assertEqual("steps.ref.outputs.merged == 'true'", keys['if'])
        self.assertEqual('Confirm', keys['name'])

    def test_a_dash_alone_on_its_line_starts_a_step(self):
        body = ('    steps:\n'
                '      - name: First\n'
                '        run: true\n'
                '      -\n'
                '        name: Second\n'
                '        id: second\n'
                "        if: github.event_name == 'push'\n")
        steps = workflows.workflow_step_blocks(body)
        self.assertEqual(2, len(steps))
        keys = workflows.step_keys(steps[1])
        self.assertEqual('second', keys['id'])
        self.assertEqual("github.event_name == 'push'", keys['if'])
        self.assertNotIn('if', workflows.step_keys(steps[0]))

    def test_an_if_inside_the_script_is_not_a_step_key(self):
        step = ('      - name: Confirm\n'
                '        run: |\n'
                '          if: not a key\n')
        self.assertNotIn('if', workflows.step_keys(step))


class PrAutoReviewSecretsInheritTest(CheckTestCase):
    """The reviewer job must not pass "secrets: inherit".

    pr-auto-review.yml reads no secrets -- it and review-pr-with-claude
    authenticate with github.token from the caller's permissions block
    -- so inheriting buys nothing and hands every secret the calling
    repository holds, publishing tokens included, to a workflow in
    another repository.
    """

    check_class = ci_workflows.CiReviewAutomation

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

    def _check(self, reviewer_job, docs_only=False, extra=None):
        # A compliant repository apart from whatever the reviewer job
        # under test does: both required workflows present, the shared
        # trigger action used, and none of the retired addresser's
        # files deployed. Anything else here shows up as an unrelated
        # finding and masks the one being tested.
        #
        # A fresh fixture per call, via CheckTestCase.fresh_fixture,
        # because the quoted-inherit case runs this once per spelling
        # in a loop, and an `extra` workflow from one iteration would
        # answer for the next.
        self.fresh_fixture()
        self.fixture.workflows({
            'pr-retest.yml': template_workflow('pr-retest.yml'),
            'pr-re-review.yml': template_workflow('pr-re-review.yml'),
            'ci.yml': 'jobs:\n' + reviewer_job,
        })
        self.fixture.workflows(extra or {})
        return self.check(is_docs_only=docs_only)

    def test_a_reviewer_without_inherit_passes(self):
        self.assert_pass(self._check(self.REVIEWER))

    def test_a_reviewer_which_inherits_fails(self):
        result = self._check(self.INHERITS)
        self.assert_fail(result, containing='secrets: inherit')
        self.assertIn('ci.yml', result['details'])

    def test_other_callers_may_inherit(self):
        # smoke-cluster.yml reads real secrets. Sweeping it up in this
        # finding would be telling projects to break their own CI.
        self.assert_pass(self._check(self.REVIEWER + self.SMOKE_INHERITS))

    def test_a_commented_out_inherit_is_not_a_finding(self):
        commented = self.REVIEWER + '    # secrets: inherit\n'
        self.assert_pass(self._check(commented))

    def test_a_trailing_comment_does_not_hide_it(self):
        # The realistic evasion. Someone who reads the template text or
        # receives the audit issue is likelier to annotate the line than
        # to delete it, and Actions treats this as plain inherit.
        annotated = self.REVIEWER + (
            '    secrets: inherit  # TODO: drop once migrated\n')
        self.assert_fail(self._check(annotated), containing='ci.yml')

    def test_a_quoted_inherit_does_not_hide_it(self):
        for quoted in ("    secrets: 'inherit'\n",
                       '    secrets: "inherit"\n'):
            # subTest carries the spelling under test, which the failure
            # message on the status assertion used to carry.
            with self.subTest(quoted=quoted):
                result = self._check(self.REVIEWER + quoted)
                self.assert_fail(result, containing='ci.yml')

    def test_a_named_secret_is_not_inherit(self):
        # The explicit mapping form passes only what it names, which is
        # the false positive worth declining.
        named = self.REVIEWER + (
            '    secrets:\n      MY_TOKEN: ${{ secrets.MY_TOKEN }}\n')
        self.assert_pass(self._check(named))

    def test_the_docs_only_path_checks_it_too(self):
        # cloudgood takes a different branch through this check, and a
        # guard that only covers one branch is a guard with a hole.
        result = self._check(self.INHERITS, docs_only=True)
        self.assert_fail(result, containing='secrets: inherit')

    def test_every_offending_workflow_is_named(self):
        # Most projects carry the reviewer job in functional-tests.yml
        # rather than ci.yml, so the finding has to name whichever file
        # it found rather than the one the fixtures happen to use. Two
        # at once also exercises the sorted join, which is what the
        # audit issue body shows the person doing the work.
        result = self._check(self.INHERITS, extra={
            'functional-tests.yml': 'jobs:\n' + self.INHERITS,
        })
        self.assert_fail(result, containing='ci.yml')
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
        self.assert_fail(result, containing='ci.yml')
        self.assertNotIn('dependabot-notes.yml', result['details'])


class MergeGroupCancellationTest(CheckTestCase):
    """Which merge group jobs must be able to cancel each other."""

    check_class = ci_workflows.MergeGroupCancellation

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
        super().setUp()
        self.addCleanup(setattr, ci_workflows, 'merge_queue_is_serial',
                        ci_workflows.merge_queue_is_serial)
        ci_workflows.merge_queue_is_serial = (
            lambda repo_name, org, github=None: self.serial_queue
        )

    def _check(self, workflows):
        """The check over a repository made of these workflows.

        The name and organisation are named rather than left to the
        default because the check hands them to merge_queue_is_serial,
        so they are part of what each case sets up.

        A fresh fixture per call, via CheckTestCase.fresh_fixture,
        because the matrix case runs the check twice in one method, and
        workflows written by the first call would otherwise still be on
        disk for the second.
        """
        self.fresh_fixture()
        self.fixture.workflows(workflows)
        return self.check(name='testrepo', org='shakenfist',
                          has_workflows_dir=True)

    def _merge_group_workflow(self, job):
        return 'on:\n  pull_request:\n  merge_group:\njobs:\n' + job

    def test_a_queue_ref_key_fails(self):
        # The bug this audit exists for: on merge_group github.ref is
        # gh-readonly-queue/<base>/pr-N-<SHA>, unique per rebuild, so
        # cancel-in-progress never matches.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY))})
        self.assert_fail(result, containing='per-attempt queue ref')

    def test_a_merge_group_aware_key_passes(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assert_pass(result)

    def test_no_concurrency_block_at_all_fails(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job())})
        self.assert_fail(result, containing='no concurrency block')

    def test_cancel_in_progress_must_be_on(self):
        # A stable key that queues instead of cancelling still leaves
        # the superseded run holding the runner.
        block = """    concurrency:
      group: ${{ github.workflow }}-merge
      cancel-in-progress: false
"""
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(block))})
        self.assert_fail(result, containing='cancel-in-progress is not true')

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
        self.assert_pass(result)

    def test_a_job_that_cannot_run_on_merge_group_is_out_of_scope(self):
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      condition="github.event_name != 'merge_group'"))})
        self.assert_pass(result)

    def test_the_static_pool_is_out_of_scope(self):
        # Gate jobs and path filters are seconds long on an
        # always-on shared pool; there is nothing to starve.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, static]'))})
        self.assert_pass(result)

    def test_a_self_hosted_pool_without_the_vm_label_is_in_scope(self):
        # instar's ephemeral runners are [self-hosted, debian-12, xl].
        # The sibling path-filter audit's 'vm' test would miss them
        # while an abandoned merge group holds one for two hours.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='[self-hosted, debian-12, xl]'))})
        self.assert_fail(result)

    def test_a_github_hosted_runner_is_out_of_scope(self):
        # No fleet runner to starve, so the workflow is examined and
        # reports nothing rather than being skipped entirely.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY, runs_on='ubuntu-latest'))})
        self.assert_pass(result)
        self.assertIn('0 job(s)', result['details'])

    def test_an_unresolvable_runs_on_expression_is_out_of_scope(self):
        # ryll's cross-platform build matrix is runs-on:
        # ${{ matrix.os }}; there is nothing to resolve it against.
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.QUEUE_REF_KEY,
                      runs_on='${{ matrix.os }}'))})
        self.assert_pass(result)
        self.assertIn('0 job(s)', result['details'])

    def test_a_reusable_workflow_is_audited(self):
        # It inherits the caller's event, and a callee published for
        # the fleet cannot know what that event is. This is
        # shakenfist/actions' smoke-cluster.yml.
        result = self._check({'smoke-cluster.yml': (
            'on:\n  workflow_call:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assert_fail(result)

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
        self.assert_fail(result, containing='smoke-cluster.yml:cluster')

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
        self.assert_pass(result)

    def test_a_trigger_line_carrying_a_comment_is_audited(self):
        # `merge_group:  # note` is a merge queue trigger, and used to
        # read as an absence of one -- the workflow was skipped whole.
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\n  merge_group:  # the merge tier\n'
            'jobs:\n' + self._job())})
        self.assert_fail(result, containing='no concurrency block')

    def test_a_marked_exception_is_allowed(self):
        result = self._check({'test-drift-fix.yml': (
            'on:\n  workflow_call:\n'
            '# audit-ok: merge-group-cancellation -- issue_comment only\n'
            'jobs:\n' + self._job(self.QUEUE_REF_KEY)
        )})
        self.assert_pass(result)

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
        self.assert_pass(result)

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
        self.assert_fail(result, containing='per-attempt queue ref')

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
        self.assert_fail(result, containing='lanes cancel each other')

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
        self.assert_pass(result)

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
        self.assert_fail(result, containing='per-attempt queue ref')

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
        self.assert_fail(result, containing='every invocation on a ref')

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
        self.assert_pass(result)

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
        self.assert_fail(result, containing='more than once per ref')

    def test_distinct_concurrency_keys_pass(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier', '      concurrency_key: merge-tier\n')
            + self._caller(
                'ansible_modules',
                '      concurrency_key: ansible-modules\n')
        )})
        self.assert_pass(result)

    def test_the_same_concurrency_key_twice_fails(self):
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller('merge_tier', '      concurrency_key: full\n')
            + self._caller(
                'ansible_modules', '      concurrency_key: full\n')
        )})
        self.assert_fail(result, containing='passes the same concurrency_key')

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
        self.assert_fail(result, containing='same for every matrix lane')

        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            + self._caller(
                'merge_tier',
                '      concurrency_key: ${{ matrix.topology }}\n',
                matrix=matrix)
        )})
        self.assert_pass(result)

    def test_a_callee_outside_the_fleet_is_reported(self):
        # Nothing here can see its concurrency group, and the caller
        # cannot fix it either.
        result = self._check({'ci.yml': (
            'on:\n  merge_group:\njobs:\n'
            '  cluster:\n'
            '    uses: someone-else/ci/.github/workflows/build.yml@v1\n'
        )})
        self.assert_fail(result, containing='outside the audited fleet')

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
        self.assert_fail(result, containing='ci.yml:cluster')
        self.assertNotIn('ci.yml:drift', result['details'])

    def test_a_stacking_merge_queue_makes_the_base_ref_key_unsafe(self):
        # The pattern this audit requires aliases every live entry in
        # the queue. That is only safe while the queue builds one at a
        # time, which merge-queue-config is what enforces -- so the
        # precondition is checked rather than left as a note.
        self.serial_queue = False
        result = self._check({'ci.yml': self._merge_group_workflow(
            self._job(self.STABLE_KEY))})
        self.assert_fail(result, containing='aliases live entries')

    def test_a_repo_with_no_merge_group_is_not_applicable(self):
        result = self._check({'ci.yml': (
            'on:\n  pull_request:\njobs:\n'
            + self._job(self.QUEUE_REF_KEY)
        )})
        self.assert_skip(result)


class WorkflowPermissionsTest(CheckTestCase):
    check_class = ci_workflows.WorkflowPermissions

    def test_without_workflows_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No .github/workflows/')

    def test_an_empty_workflow_directory_does_not_apply(self):
        os.makedirs(os.path.join(self.fixture.path, '.github', 'workflows'))
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No workflow files')

    def test_a_top_level_permissions_block_passes(self):
        self.fixture.workflow('ci.yml', 'permissions:\n  contents: read\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_workflow_without_permissions_fails_and_names_it(self):
        self.fixture.workflow('ci.yml', 'on: push\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='ci.yml')

    def test_a_nested_permissions_block_does_not_count(self):
        """Job-level permissions do not narrow the default token."""
        self.fixture.workflow(
            'ci.yml',
            'on: push\njobs:\n  a:\n    permissions:\n      contents: read\n')
        self.assert_fail(self.check(has_workflows_dir=True))


class PreCommitConfigTest(CheckTestCase):
    check_class = ci_workflows.PreCommitConfig

    def test_a_missing_config_fails(self):
        self.assert_fail(self.check(), containing='.pre-commit-config.yaml')

    def test_a_present_config_passes(self):
        self.fixture.write('.pre-commit-config.yaml', 'repos: []\n')
        self.assert_pass(self.check())


class DevpiFallbackTest(CheckTestCase):
    check_class = ci_workflows.DevpiFallback

    WITH_FALLBACK = (
        'jobs:\n'
        '  build:\n'
        '    env:\n'
        '      PIP_INDEX_URL: http://192.168.1.15:3141/root/pypi/+simple/\n'
        '      PIP_EXTRA_INDEX_URL: https://pypi.org/simple\n'
    )
    WITHOUT_FALLBACK = (
        'jobs:\n'
        '  build:\n'
        '    env:\n'
        '      PIP_INDEX_URL: http://192.168.1.15:3141/root/pypi/+simple/\n'
    )

    def test_without_workflows_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No .github/workflows/')

    def test_a_workflow_not_using_devpi_does_not_apply(self):
        self.fixture.workflow('ci.yml', 'on: push\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No jobs use the local devpi cache')

    def test_a_fallback_beside_the_index_passes(self):
        self.fixture.workflow('ci.yml', self.WITH_FALLBACK)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_no_fallback_fails(self):
        """Without it a devpi outage stops every build."""
        self.fixture.workflow('ci.yml', self.WITHOUT_FALLBACK)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='ci.yml')

    def test_the_hostname_form_is_recognised_too(self):
        self.fixture.workflow('ci.yml', self.WITHOUT_FALLBACK.replace(
            '192.168.1.15:3141', 'devpi.home.stillhq.com'))
        self.assert_fail(self.check(has_workflows_dir=True))


class DevpiStaleIpTest(CheckTestCase):
    check_class = ci_workflows.DevpiStaleIp

    def test_without_workflows_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No .github/workflows/')

    def test_the_current_address_passes(self):
        self.fixture.workflow(
            'ci.yml',
            'env:\n  PIP_INDEX_URL: http://192.168.1.15:3141/\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_the_retired_address_fails(self):
        self.fixture.workflow(
            'ci.yml',
            'env:\n  PIP_INDEX_URL: http://192.168.1.4:3141/\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='ci.yml')

    def test_a_longer_address_starting_with_the_same_digits_passes(self):
        """192.168.1.45 is not 192.168.1.4."""
        self.fixture.workflow(
            'ci.yml',
            'env:\n  PIP_INDEX_URL: http://192.168.1.45:3141/\n')
        self.assert_pass(self.check(has_workflows_dir=True))


class SecretScanningCiTest(CheckTestCase):
    check_class = ci_workflows.SecretScanningCi

    def test_a_docs_only_repository_does_not_apply(self):
        self.assert_skip(self.check(is_docs_only=True),
                         containing='Documentation-only')

    def test_without_workflows_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No .github/workflows/')

    def test_a_gitleaks_step_passes(self):
        self.fixture.workflow(
            'scan.yml',
            'jobs:\n  scan:\n    steps:\n      - run: gitleaks detect\n')
        self.assert_pass(self.check(has_workflows_dir=True),)

    def test_an_alternative_scanner_passes(self):
        self.fixture.workflow(
            'scan.yml',
            'jobs:\n  scan:\n    steps:\n      - run: trufflehog git file://.\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_no_scanner_anywhere_fails(self):
        self.fixture.workflow('ci.yml', 'on: push\njobs:\n  a:\n    steps: []\n')
        self.assert_fail(self.check(has_workflows_dir=True))


GOOD_NIGHTLY = """\
name: Fuzz
on:
  schedule:
    - cron: '0 4 * * *'
permissions:
  contents: read
  issues: write
jobs:
  fuzz:
    steps:
      - run: cargo fuzz run $TARGET
      - run: tools/ci/report-fuzz-crash.sh "$TARGET" "$CRASH" "$LOG"
"""


SCHEDULING_CALLER = """\
on:
  schedule:
    - cron: '0 4 * * *'
jobs:
  call:
    uses: ./.github/workflows/fuzz-run.yml
"""


class FuzzNightlyReportingTest(CheckTestCase):
    check_class = ci_workflows.FuzzNightlyReporting

    def _targets(self):
        """Give the fixture a cargo-fuzz layout."""
        self.fixture.write('src/fuzz/fuzz_targets/fuzz_parse.rs', '// target\n')

    def _reporter(self):
        self.fixture.write('tools/ci/report-fuzz-crash.sh',
                           '#!/bin/bash\ngh issue create --title x\n')

    def _callee(self, trigger):
        """GOOD_NIGHTLY as a reusable workflow, triggered as given."""
        return GOOD_NIGHTLY.replace(
            "  schedule:\n    - cron: '0 4 * * *'", trigger)

    def _queue_gated(self, marked):
        """The nightly, also triggered by the merge queue.

        The marker goes on its own comment line: a trailing one on the
        trigger would be honoured too, but it would leave the test
        unable to tell the marker from a trigger it never saw.
        """
        marker = ('  # audit-ok: fuzz-in-merge-queue -- required check\n'
                  if marked else '')
        return GOOD_NIGHTLY.replace(
            '  schedule:', marker + '  merge_group:\n  schedule:')

    def test_without_workflows_it_does_not_apply(self):
        self.assert_skip(self.check(), containing='No .github/workflows/')

    def test_a_repository_with_no_fuzz_targets_does_not_apply(self):
        self.fixture.workflow('ci.yml', 'on: push\njobs:\n  a:\n    steps: []\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    def test_the_reference_shape_passes(self):
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_an_inline_gh_issue_create_passes(self):
        """The script indirection is recommended, not required."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                'tools/ci/report-fuzz-crash.sh "$TARGET" "$CRASH" "$LOG"',
                'gh issue create --title "crash"'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_targets_with_no_workflow_running_them_fails(self):
        self._targets()
        self.fixture.workflow('ci.yml', 'on: push\njobs:\n  a:\n    steps: []\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='no workflow runs them')

    def test_fuzzing_only_on_merge_group_fails(self):
        """The shape that evicted ryll PRs from its own merge queue."""
        self._targets()
        self.fixture.workflow(
            'ci.yml',
            'on:\n  merge_group:\njobs:\n  fuzz:\n'
            '    steps:\n      - run: make fuzz-build-parse\n')
        result = self.assert_fail(self.check(has_workflows_dir=True),
                                  containing='merge_group')
        self.assertIn('no schedule trigger', result['details'])

    def test_a_scheduled_lane_that_also_gates_the_queue_fails(self):
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace('  schedule:', '  merge_group:\n  schedule:'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='merge_group')

    def test_the_merge_queue_exception_marker_is_honoured(self):
        self._targets()
        self._reporter()
        self.fixture.workflow('fuzz.yml', self._queue_gated(marked=True))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_the_same_workflow_without_the_marker_fails(self):
        """The pair pins the marker as what makes the difference."""
        self._targets()
        self._reporter()
        self.fixture.workflow('fuzz.yml', self._queue_gated(marked=False))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='merge_group')

    def test_a_trigger_line_carrying_a_comment_is_still_a_trigger(self):
        """A trailing comment must not hide the queue trigger."""
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                '  schedule:',
                '  merge_group:  # the merge tier\n  schedule:'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='merge_group')

    def test_a_flow_style_queue_trigger_is_found(self):
        """`on: [merge_group]` is the same lane written differently."""
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)
        self.fixture.workflow(
            'fuzz-queue.yml',
            'on: [merge_group]\njobs:\n  fuzz:\n'
            '    steps:\n      - run: cargo fuzz run $TARGET\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='fuzz-queue.yml')

    def test_a_build_only_lane_on_the_queue_is_still_a_finding(self):
        """Building the targets in the queue costs what running them costs.

        The lane that evicted ryll's pull requests three times in eight
        days was a build-and-smoke matrix, not a campaign: what the
        queue pays for is four runners held while its clock runs. A
        repository that needs a fuzz status check to report on
        merge_group reports it from an aggregate gate job, the way
        ryll's `Can merge` does, rather than putting the fuzz job in
        the queue.
        """
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)
        self.fixture.workflow(
            'fuzz-smoke.yml',
            'on:\n  pull_request:\n  merge_group:\njobs:\n  smoke:\n'
            '    steps:\n      - run: cargo fuzz build\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='fuzz-smoke.yml')

    def test_a_dispatch_only_lane_fails(self):
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace("  schedule:\n    - cron: '0 4 * * *'",
                                 '  workflow_dispatch:'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='no schedule trigger')

    def test_a_nightly_that_cannot_file_an_issue_fails(self):
        """Nobody reads a scheduled workflow's result."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                '      - run: tools/ci/report-fuzz-crash.sh '
                '"$TARGET" "$CRASH" "$LOG"\n', ''))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_issues_write_without_a_reporter_fails(self):
        """The permission alone tells nobody anything."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                'tools/ci/report-fuzz-crash.sh "$TARGET" "$CRASH" "$LOG"',
                'echo "crashed"'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_without_issues_write_fails(self):
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml', GOOD_NIGHTLY.replace('  issues: write\n', ''))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_pr_smoke_lane_beside_the_nightly_is_fine(self):
        """A short build-and-smoke on PRs is good practice, not a finding."""
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)
        self.fixture.workflow(
            'fuzz-smoke.yml',
            'on:\n  pull_request:\njobs:\n  smoke:\n'
            '    steps:\n      - run: cargo fuzz build\n')
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reporter_only_mentioned_in_a_yaml_comment_fails(self):
        """A TODO describes reporting rather than doing it."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                '      - run: tools/ci/report-fuzz-crash.sh '
                '"$TARGET" "$CRASH" "$LOG"',
                '      # TODO: gh issue create for crashes'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_script_that_only_comments_about_filing_fails(self):
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\n# TODO: gh issue create --title x\necho crashed\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_script_that_is_not_in_the_repository_fails(self):
        """The workflow names a script the checkout does not have."""
        self._targets()
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_script_path_reaching_outside_the_checkout_is_never_read(self):
        """An audited repository's YAML does not get to pick the file.

        Asserted on the reads rather than on the result: the file is
        not there either way, so a check that walked out of the clone
        and found nothing would look exactly like one that refused to.
        """
        requested = []

        class RecordingRepo:
            def read(self, path):
                requested.append(path)
                return None

        for escape in ('tools/../../../../etc/report-fuzz-crash.sh',
                       '../../etc/report-fuzz-crash.sh',
                       '/etc/report-fuzz-crash.sh'):
            content = GOOD_NIGHTLY.replace(
                'tools/ci/report-fuzz-crash.sh', escape)
            self.assertFalse(
                ci_workflows.reaches_issue_filing(RecordingRepo(), content),
                escape)
            self.assertEqual([], requested, escape)

    def test_a_reporter_that_splits_into_two_scripts_passes(self):
        """The walker and the filer are allowed to be separate files.

        This is ryll's shape: the workflow names a script that walks
        the crash markers, and that script hands each one to a second
        script that decides what the issue says. Stopping at the first
        script failed a repository for splitting a reporter that the
        criterion asked to be testable in the first place.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\nfor c in "$@"; do\n'
            '  tools/ci/file-fuzz-issue.sh "$c"\ndone\n')
        self.fixture.write('tools/ci/file-fuzz-issue.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_sibling_named_through_a_variable_is_followed(self):
        """`${SCRIPT_DIR}/helper.sh` is how a script finds its helper.

        The literal text a pattern can see there begins at the `/`,
        which reads as an absolute path and would be refused. ryll's
        report-fuzz-run.sh defaults its reporter exactly that way.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\nSCRIPT_DIR="$(dirname "$0")"\n'
            'REPORTER="${REPORTER:-${SCRIPT_DIR}/file-fuzz-issue.sh}"\n'
            '"${REPORTER}" "$1"\n')
        self.fixture.write('tools/ci/file-fuzz-issue.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_chain_of_scripts_that_never_files_fails(self):
        """Following further must not turn silence into a pass."""
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\ntools/ci/file-fuzz-issue.sh "$1"\n')
        self.fixture.write('tools/ci/file-fuzz-issue.sh',
                           '#!/bin/bash\necho "would have filed"\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_scripts_that_name_each_other_terminate(self):
        """A cycle is a repository's own file naming its own caller."""
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\ntools/ci/file-fuzz-issue.sh "$1"\n')
        self.fixture.write(
            'tools/ci/file-fuzz-issue.sh',
            '#!/bin/bash\ntools/ci/report-fuzz-crash.sh "$1"\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_deeper_than_the_depth_limit_fails(self):
        """The bound is real, so a repository cannot be walked forever."""
        self._targets()
        self.fixture.write('tools/ci/report-fuzz-crash.sh',
                           '#!/bin/bash\ntools/ci/a.sh\n')
        self.fixture.write('tools/ci/a.sh', '#!/bin/bash\ntools/ci/b.sh\n')
        self.fixture.write('tools/ci/b.sh', '#!/bin/bash\ntools/ci/c.sh\n')
        self.fixture.write('tools/ci/c.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_taking_gh_from_a_variable_passes(self):
        """`GH="${GH:-gh}"` is a test seam, not an evasion.

        ryll spells the call that way so that
        tools/test-report-fuzz-failure.sh can stub `gh` and assert on
        what the reporter would have filed. Requiring the literal
        command name fails a repository for making its reporter
        testable, which is what this criterion asked of it.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\nGH="${GH:-gh}"\n'
            '"${GH}" issue create --title x --body-file b\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_prose_about_creating_an_issue_is_not_a_call(self):
        """Only a variable expansion stands in for the command."""
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\necho "somebody should issue create for this"\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_variable_at_the_end_of_a_line_is_not_a_call(self):
        """A command and its sub-commands sit on a single line.

        Whitespace that spans the newline between them would take a
        usage heredoc ending a line in an expansion, and beginning the
        next with the words, for a call -- and so would a `${x:-y}`
        default allowed to run past the line it is written on.
        Asserted against the pattern because each of these shapes
        would otherwise cost a whole check to drive from a fixture,
        and it is the set of them that pins the property.
        """
        for prose in ('echo "$MSG"\nissue create is the plan\n',
                      'echo $FOO\n  issue create would be nice\n',
                      'TMP=$DIR:-fallback\necho "issue create something"\n',
                      'cat <<EOF\nusage: $PROG\nissue create <title>\nEOF\n'):
            self.assertIsNone(
                ci_workflows.FILES_AN_ISSUE_RE.search(prose), prose)
        # The seam the alternative exists for still reads as a call.
        self.assertIsNotNone(ci_workflows.FILES_AN_ISSUE_RE.search(
            'GH="${GH:-gh}"\n"${GH}" issue create --title x\n'))

    def test_the_variable_standing_in_for_the_command_is_an_expansion(self):
        """What that alternative holds, and what it does not.

        It holds that the position is a balanced expansion: `${GH` and
        `$GH}` are not shell and are not calls. It does not hold that
        the expansion is the command -- any expansion sharing the line
        with the words matches, so a usage message naming its own
        program through a variable reads as a call. That is the
        permissive direction the rest of this module takes, and the
        spec page says so; it is asserted here so that a later tighten
        of the pattern is a deliberate change rather than a surprise.
        """
        for unbalanced in ('${GH issue create --title x\n',
                           '$GH} issue create --title x\n'):
            with self.subTest(unbalanced=unbalanced):
                self.assertIsNone(
                    ci_workflows.FILES_AN_ISSUE_RE.search(unbalanced))
        for accepted in ('echo "usage: $PROG issue create <title>"\n',
                         'echo "we should $TOOL issue create later"\n'):
            with self.subTest(accepted=accepted):
                self.assertIsNotNone(
                    ci_workflows.FILES_AN_ISSUE_RE.search(accepted))

    def test_a_caller_reaches_a_reporter_two_scripts_deep(self):
        """The caller side is walked as far as the callee side.

        lane_can_report runs the walk twice, once per side, with a
        visited set each. Every other multi-level test drives the side
        the schedule is on, so a walk that went deep on one side only
        would pass all of them.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\ntools/ci/file-fuzz-issue.sh "$1"\n')
        self.fixture.write('tools/ci/file-fuzz-issue.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow(
            'fuzz-nightly.yml',
            "on:\n  schedule:\n    - cron: '0 4 * * *'\n"
            'permissions:\n  issues: write\njobs:\n'
            '  call:\n    uses: ./.github/workflows/fuzz-run.yml\n'
            '  report:\n    needs: [call]\n    steps:\n'
            '      - run: tools/ci/report-fuzz-crash.sh "$TARGET"\n')
        self.fixture.workflow(
            'fuzz-run.yml',
            GOOD_NIGHTLY.replace(
                "  schedule:\n    - cron: '0 4 * * *'", '  workflow_call:')
            .replace('  issues: write\n', '')
            .replace('      - run: tools/ci/report-fuzz-crash.sh '
                     '"$TARGET" "$CRASH" "$LOG"\n',
                     '      - uses: actions/upload-artifact@v4\n'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reporter_named_under_a_workspace_variable_is_followed(self):
        """`${{ github.workspace }}/tools/ci/x.sh` names a path.

        The directories between the variable and the file are part of
        the reference. Keeping only the basename looks for the
        reporter at the repository root, where it is not, and fails a
        repository for a spelling `run:` blocks use routinely.
        """
        self._targets()
        for rooted in ('${{ github.workspace }}/tools/ci/report-fuzz-crash.sh',
                       '$GITHUB_WORKSPACE/tools/ci/report-fuzz-crash.sh'):
            with self.subTest(rooted=rooted):
                self._reporter()
                self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY.replace(
                    'tools/ci/report-fuzz-crash.sh', rooted))
                self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reporter_at_the_depth_limit_passes(self):
        """Three scripts from the workflow is inside the bound.

        The limit is only pinned from one side by the test above it: a
        walk that stopped one short would still fail that one, and
        nothing here would notice.
        """
        self._targets()
        self.fixture.write('tools/ci/report-fuzz-crash.sh',
                           '#!/bin/bash\ntools/ci/a.sh\n')
        self.fixture.write('tools/ci/a.sh', '#!/bin/bash\ntools/ci/b.sh\n')
        self.fixture.write('tools/ci/b.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_python_reporter_is_followed_past_the_first_script(self):
        """.py is one of the two extensions, at every depth.

        The shell leg carries the multi-level tests, so a walk that
        only followed .sh past the first script would pass all of
        them.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\npython3 tools/ci/file_fuzz_issue.py "$1"\n')
        self.fixture.write(
            'tools/ci/file_fuzz_issue.py',
            'import subprocess\n'
            "subprocess.run(['gh', 'issue', 'create', '--title', 'x'])\n")
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reference_from_a_nested_script_cannot_leave_the_checkout(self):
        """The guard holds for the directory-relative resolution too.

        The escape test above drives the workflow, whose references
        are resolved against the repository root. Resolving against a
        referring script's own directory is a second way into the same
        guard, and joining a base onto the reference is exactly the
        step that would defeat it.

        Each case names what it yields rather than asserting inside a
        loop over it. Three of the four yield nothing, so a loop body
        would not run at all, and the whole test would be satisfied by
        a referenced_scripts that yielded nothing for anything -- which
        is a real failure mode for a guard written this way and not one
        a test pinning the guard should be blind to. The positive
        control is here for the same reason.
        """
        cases = (
            # Refused outright: absolute, or climbing from the base.
            ('/etc/x.sh', []),
            ('../../../../etc/x.sh', []),
            # Variable-rooted, but the tail climbs: the sibling reading
            # is not offered for a tail containing `..`, and the two
            # path readings both leave the clone.
            ('"${SCRIPT_DIR}/../../../../etc/x.sh"', []),
            # The base absorbs the climb, so this one stays inside --
            # at a path the repository did name.
            ('tools/../../../etc/x.sh', ['etc/x.sh']),
            # Positive control: the shape the walk exists to follow.
            ('${SCRIPT_DIR}/helper.sh', ['tools/ci/helper.sh', 'helper.sh']),
        )
        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(
                    expected,
                    list(ci_workflows.referenced_scripts(
                        reference, 'tools/ci')))

    def test_a_quoted_expansion_before_the_separator_is_followed(self):
        """`"${SCRIPT_DIR}"/helper.sh` is the same reference.

        Quoting the expansion and leaving the rest of the path outside
        it is as common as quoting the whole path, and `"$(dirname
        "$0")"/helper.sh` computes the same thing inline. Reading the
        single character before the reference finds the quote rather
        than the `}` or `)`, and the reference then resolves to
        nothing at all -- so a repository that split its reporter the
        way this criterion recommends would fail the check.
        """
        for spelling in ('"${SCRIPT_DIR}"/helper.sh "$1"',
                         "'${SCRIPT_DIR}'/helper.sh",
                         '"$(dirname "$0")"/helper.sh'):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    ['tools/ci/helper.sh', 'helper.sh'],
                    list(ci_workflows.referenced_scripts(
                        spelling, 'tools/ci')))
        # A quote does not make an absolute path relative.
        self.assertEqual(
            [], list(ci_workflows.referenced_scripts(
                'echo "/etc/x.sh"', 'tools/ci')))

    def test_a_quoted_expansion_reaches_the_reporter(self):
        """The same spelling, driven through the check."""
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\nSCRIPT_DIR="$(dirname "$0")"\n'
            '"${SCRIPT_DIR}"/file-fuzz-issue.sh "$1"\n')
        self.fixture.write('tools/ci/file-fuzz-issue.sh',
                           '#!/bin/bash\ngh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_variable_rooted_tail_with_directories_names_no_sibling(self):
        """A tail that names directories means them.

        The sibling reading exists because a script computing a
        directory is nearly always computing its own, which is only
        the case where nothing follows the variable but a file name.
        Offering the basename for a tail that names directories --
        whether it climbs out of the referring file's directory or
        descends past it -- fabricates a path the repository never
        named, and can hand the walk an unrelated file of the right
        name and turn a repository that files nothing into a pass.
        """
        for reference, expected in (
                ('"${TOOLS}/../shared/report.sh"',
                 ['tools/shared/report.sh']),
                ('"${SCRIPT_DIR}/sub/dir/report.sh"',
                 ['tools/ci/sub/dir/report.sh', 'sub/dir/report.sh']),
                # Nothing but a file name after the variable, so the
                # sibling is what was meant.
                ('${SCRIPT_DIR}/report.sh',
                 ['tools/ci/report.sh', 'report.sh'])):
            with self.subTest(reference=reference):
                self.assertEqual(
                    expected,
                    list(ci_workflows.referenced_scripts(
                        reference, 'tools/ci')))

    def test_a_bare_sibling_is_resolved_against_its_own_directory(self):
        """`helper.sh "$1"` inside tools/ci means tools/ci/helper.sh.

        The reading relative to the repository root is offered too,
        because a script run from the root may well name a path from
        there, but the sibling is the one a script writing a bare name
        means.
        """
        self.assertEqual(
            ['tools/ci/helper.sh', 'helper.sh'],
            list(ci_workflows.referenced_scripts('helper.sh "$1"',
                                                 'tools/ci')))

    def test_filing_the_issue_with_an_action_passes(self):
        """`gh issue create` is the fleet's spelling, not the criterion."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                '      - run: tools/ci/report-fuzz-crash.sh '
                '"$TARGET" "$CRASH" "$LOG"',
                '      - uses: peter-evans/create-issue-from-file@v5'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_filing_the_issue_through_the_rest_api_passes(self):
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                'tools/ci/report-fuzz-crash.sh "$TARGET" "$CRASH" "$LOG"',
                'gh api repos/${{ github.repository }}/issues -f title=crash'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_schedule_on_the_caller_of_a_reusable_workflow_passes(self):
        """The nightly may be split across a caller and its callee."""
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz-nightly.yml',
            "on:\n  schedule:\n    - cron: '0 4 * * *'\njobs:\n"
            '  call:\n    uses: ./.github/workflows/fuzz-run.yml\n')
        self.fixture.workflow(
            'fuzz-run.yml',
            GOOD_NIGHTLY.replace(
                "  schedule:\n    - cron: '0 4 * * *'", '  workflow_call:'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reusable_fuzz_workflow_nobody_schedules_fails(self):
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz-run.yml',
            GOOD_NIGHTLY.replace(
                "  schedule:\n    - cron: '0 4 * * *'", '  workflow_call:'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='no schedule trigger')

    def test_installing_cargo_fuzz_is_not_running_it(self):
        """A toolchain setup step must not pull a repository into scope."""
        self.fixture.workflow(
            'ci.yml',
            'on:\n  pull_request:\n  merge_group:\njobs:\n  build:\n'
            '    steps:\n      - run: cargo install cargo-fuzz --locked\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    def test_a_make_target_that_merely_starts_with_fuzz_is_not_fuzzing(self):
        self.fixture.workflow(
            'ci.yml',
            'on:\n  pull_request:\n  merge_group:\njobs:\n  test:\n'
            '    steps:\n      - run: make fuzzy-logic-tests\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    def test_a_make_build_target_is_still_fuzzing(self):
        """ryll's lane, which is the shape the criterion came from."""
        self._targets()
        self.fixture.workflow(
            'ci.yml',
            'on:\n  merge_group:\njobs:\n  fuzz:\n'
            '    steps:\n      - run: make fuzz-build-${{ matrix.target }}\n')
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='merge_group')

    def test_a_pinned_toolchain_invocation_counts(self):
        """cargo-fuzz needs nightly, so `cargo +nightly fuzz run` is common."""
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace('cargo fuzz run $TARGET',
                                 'cargo +nightly fuzz run $TARGET'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reporter_on_the_scheduling_caller_passes(self):
        """The callee may fuzz and upload while the caller files."""
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz-nightly.yml',
            "on:\n  schedule:\n    - cron: '0 4 * * *'\n"
            'permissions:\n  issues: write\njobs:\n'
            '  call:\n    uses: ./.github/workflows/fuzz-run.yml\n'
            '  report:\n    needs: [call]\n    steps:\n'
            '      - run: tools/ci/report-fuzz-crash.sh "$TARGET"\n')
        self.fixture.workflow(
            'fuzz-run.yml',
            GOOD_NIGHTLY.replace(
                "  schedule:\n    - cron: '0 4 * * *'", '  workflow_call:')
            .replace('  issues: write\n', '')
            .replace('      - run: tools/ci/report-fuzz-crash.sh '
                     '"$TARGET" "$CRASH" "$LOG"\n',
                     '      - uses: actions/upload-artifact@v4\n'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_header_comment_mentioning_fuzzing_does_not_count(self):
        """A workflow saying fuzzing moved away is not running it."""
        self.fixture.workflow(
            'ci.yml',
            '# The cargo fuzz matrix moved to fuzz.yml.\n'
            'on: push\njobs:\n  a:\n    steps: []\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    def test_a_vendored_fuzz_target_directory_is_ignored(self):
        self.fixture.write(
            'target/debug/dep/fuzz_targets/x.rs', '// not ours\n')
        self.fixture.workflow('ci.yml', 'on: push\njobs:\n  a:\n    steps: []\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    #: Written out rather than read from FUZZ_WALK_SKIP: a test that
    #: iterates the constant it is testing shrinks when the constant
    #: does, so deleting an entry would pass. This list has to be
    #: edited alongside it, which is the point.
    SKIPPED_DIRECTORIES = (
        '.git', '.tox', '.venv', 'build', 'dist', 'node_modules',
        'target', 'third_party', 'vendor', 'venv',
    )

    def test_a_repository_with_a_fuzz_lane_is_never_walked(self):
        """The checkout walk is the expensive half, and is avoidable.

        A repository recognised by its fuzz workflow does not need the
        target search at all: `applies()` can say yes from the cheap
        cached read, and the only branch of `run()` that names where
        the targets are is the one where no workflow runs them. Pinned
        because it is invisible -- reintroducing the walk changes no
        verdict, only 100ms per fuzzing repository per nightly.
        """
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)

        walked = []
        real = ci_workflows.repo_fuzz_target_dirs

        def counting(path):
            walked.append(path)
            return real(path)

        ci_workflows.repo_fuzz_target_dirs = counting
        try:
            self.assert_pass(self.check(has_workflows_dir=True))
        finally:
            ci_workflows.repo_fuzz_target_dirs = real
        self.assertEqual([], walked)

    def test_every_skipped_directory_hides_a_fuzz_target(self):
        """`target/` is the realistic case; the rest reach it another way."""
        self.assertEqual(set(self.SKIPPED_DIRECTORIES),
                         set(ci_workflows.FUZZ_WALK_SKIP))
        for skipped in self.SKIPPED_DIRECTORIES:
            with self.subTest(directory=skipped):
                with tempfile.TemporaryDirectory() as path:
                    os.makedirs(os.path.join(path, skipped, 'dep',
                                             'fuzz_targets'))
                    self.assertEqual(
                        [], ci_workflows.repo_fuzz_target_dirs(path))
        with tempfile.TemporaryDirectory() as path:
            os.makedirs(os.path.join(path, 'src', 'fuzz', 'fuzz_targets'))
            self.assertEqual(['src/fuzz/fuzz_targets'],
                             ci_workflows.repo_fuzz_target_dirs(path))

    def test_a_schedule_line_carrying_a_comment_is_still_a_schedule(self):
        """The mirror of the merge_group case, failing the other way.

        A blind spot here fails a repository whose nightly is exactly
        right, and the audit files that finding as an issue against
        somebody else's project.
        """
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace('  schedule:',
                                 '  schedule:  # nightly at 04:00'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_workflow_call_line_carrying_a_comment_is_still_a_trigger(self):
        self._targets()
        self._reporter()
        self.fixture.workflow('fuzz-nightly.yml', SCHEDULING_CALLER)
        self.fixture.workflow('fuzz-run.yml',
                              self._callee('  workflow_call:  # reusable'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_flow_style_workflow_call_is_found(self):
        """`on: [workflow_call]` needs no sub-keys, so it is written."""
        self._targets()
        self._reporter()
        self.fixture.workflow('fuzz-nightly.yml', SCHEDULING_CALLER)
        self.fixture.workflow(
            'fuzz-run.yml',
            GOOD_NIGHTLY.replace(
                "on:\n  schedule:\n    - cron: '0 4 * * *'",
                'on: [workflow_call]'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_caller_of_another_project_s_workflow_does_not_count(self):
        """`uses:` naming another repository schedules that one, not this.

        The callee here is the local fuzz-run.yml; the caller
        schedules a same-named workflow belonging to somebody else,
        which leaves this repository's targets running only on
        dispatch.
        """
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz-nightly.yml',
            SCHEDULING_CALLER.replace(
                'uses: ./.github/workflows/fuzz-run.yml',
                'uses: other-org/other-repo/.github/workflows/'
                'fuzz-run.yml@main'))
        self.fixture.workflow('fuzz-run.yml', self._callee('  workflow_call:'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='no schedule trigger')

    def test_a_caller_naming_this_repository_in_full_counts(self):
        """The other spelling of a local callee, `owner/repo/...@ref`."""
        self._targets()
        self._reporter()
        self.fixture.workflow(
            'fuzz-nightly.yml',
            SCHEDULING_CALLER.replace(
                'uses: ./.github/workflows/fuzz-run.yml',
                'uses: shakenfist/testrepo/.github/workflows/'
                'fuzz-run.yml@main'))
        self.fixture.workflow('fuzz-run.yml', self._callee('  workflow_call:'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_reporter_in_a_trailing_yaml_comment_fails(self):
        """A TODO on the end of a line describes filing, not filing."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                '      - run: tools/ci/report-fuzz-crash.sh '
                '"$TARGET" "$CRASH" "$LOG"',
                '      - run: echo crashed  # TODO: gh issue create for this'))
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_reporter_script_commenting_about_filing_inline_fails(self):
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\necho crashed  # TODO: gh issue create --title x\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_fail(self.check(has_workflows_dir=True),
                         containing='cannot file an issue')

    def test_a_hash_glued_to_a_word_is_not_a_comment(self):
        """`${#crashes[@]}` is how a bash reporter counts what it found.

        A `#` opens a comment only where it follows whitespace. Cut on
        every `#` instead and the filing call that follows a length
        expansion on the same line disappears, which fails a reporter
        that works.
        """
        self._targets()
        self.fixture.write(
            'tools/ci/report-fuzz-crash.sh',
            '#!/bin/bash\n'
            '[ ${#CRASHES[@]} -gt 0 ] && gh issue create --title crash\n')
        self.fixture.workflow('fuzz.yml', GOOD_NIGHTLY)
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_hash_inside_a_quoted_argument_is_not_a_comment(self):
        """Stripping trailing comments must not eat the command itself."""
        self._targets()
        self.fixture.workflow(
            'fuzz.yml',
            GOOD_NIGHTLY.replace(
                'tools/ci/report-fuzz-crash.sh "$TARGET" "$CRASH" "$LOG"',
                'echo "see # below" && gh issue create --title crash'))
        self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_trailing_comment_mentioning_fuzz_does_not_pull_a_repo_in(self):
        """The mirror of the header-comment guard, on one line.

        A repository that has never fuzzed anything must not be
        measured, let alone failed, because a build step carries a
        comment about a make target that used to exist.
        """
        self.fixture.workflow(
            'ci.yml',
            'on:\n  pull_request:\n  merge_group:\njobs:\n  build:\n'
            '    steps:\n      - run: make build  '
            '# replaces the old make fuzz-all target\n')
        self.assert_skip(self.check(has_workflows_dir=True),
                         containing='No fuzz targets')

    def test_a_python_reporter_script_counts(self):
        """The reporter belongs in a script; the language is not the point.

        Both spellings a Python reporter reaches for: `gh` through
        subprocess as an argv list, where the sub-commands are
        separated by quotes and commas rather than spaces, and a
        client library's own call.
        """
        for body in (
                'import subprocess\n'
                "subprocess.run(['gh', 'issue', 'create', '--title', 'x'])\n",
                'from github import Github\n'
                "Github().get_repo(name).create_issue(title='crash')\n"):
            with self.subTest(body=body.splitlines()[0]):
                self.setUp()
                self._targets()
                self.fixture.write('tools/ci/report-fuzz-crash.py', body)
                self.fixture.workflow(
                    'fuzz.yml',
                    GOOD_NIGHTLY.replace('report-fuzz-crash.sh',
                                         'report-fuzz-crash.py'))
                self.assert_pass(self.check(has_workflows_dir=True))

    def test_a_second_scheduled_lane_that_cannot_report_fails(self):
        """Every scheduled lane is held to this, not one of them.

        Nothing in a workflow file separates a silent crash campaign
        from a corpus-minimisation lane with nothing to say, and the
        permissive reading loses exactly the crash this criterion
        exists to surface. An `any()` over the lanes would pass the
        rest of this suite.
        """
        self._targets()
        self._reporter()
        self.fixture.workflow('coverage-fuzz.yml', GOOD_NIGHTLY)
        self.fixture.workflow(
            'fuzz-min.yml',
            "on:\n  schedule:\n    - cron: '0 5 * * *'\njobs:\n  min:\n"
            '    steps:\n      - run: cargo fuzz cmin $TARGET\n')
        result = self.assert_fail(self.check(has_workflows_dir=True),
                                  containing='cannot file an issue')
        self.assertIn('fuzz-min.yml', result['details'])
        self.assertNotIn('coverage-fuzz.yml', result['details'])


if __name__ == '__main__':
    unittest.main()
