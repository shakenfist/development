#!/usr/bin/env python3

"""Tests for audit/checks/github_config.py.

Run with: python3 scripts/tests/test_github_config.py
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audit.checks import github_config  # noqa: E402
from audit.github import CompletedCommand, FakeGitHub  # noqa: E402
from tests.base import CheckTestCase, run_check  # noqa: E402

evaluate_merge_queue_rules = github_config.evaluate_merge_queue_rules


class MergeQueueConfigTest(unittest.TestCase):
    def _rule(self, **params):
        return {'type': 'merge_queue', 'parameters': params}

    def test_no_merge_queue_rule_returns_none(self):
        self.assertIsNone(evaluate_merge_queue_rules([]))
        self.assertIsNone(evaluate_merge_queue_rules(
            [{'type': 'deletion'}, {'type': 'non_fast_forward'}]
        ))

    def test_serialized_queue_passes(self):
        problems = evaluate_merge_queue_rules([
            {'type': 'pull_request'},
            self._rule(
                max_entries_to_build=1, min_entries_to_merge=1,
                max_entries_to_merge=5,
                min_entries_to_merge_wait_minutes=5,
            ),
        ])
        self.assertEqual(problems, [])

    def test_speculative_stacking_fails(self):
        problems = evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=2, min_entries_to_merge=1),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('max_entries_to_build is 2', problems[0])

    def test_batched_merging_fails(self):
        problems = evaluate_merge_queue_rules([
            self._rule(max_entries_to_build=1, min_entries_to_merge=2),
        ])
        self.assertEqual(len(problems), 1)
        self.assertIn('min_entries_to_merge is 2', problems[0])

    def test_missing_parameters_flags_both(self):
        problems = evaluate_merge_queue_rules([
            {'type': 'merge_queue'},
        ])
        self.assertEqual(len(problems), 2)


class GithubConfigTestCase(CheckTestCase):
    """Shared plumbing for the criteria that ask the GitHub API."""

    REPO_PATH = 'repos/shakenfist/testrepo'

    def api(self, jq=None):
        """The FakeGitHub key for a `gh api` call on the fixture repo."""
        if jq is None:
            return f'api {self.REPO_PATH}'
        return f'api {self.REPO_PATH} --jq {jq}'

    def rules(self, branch):
        return f'api {self.REPO_PATH}/rules/branches/{branch}'

    def run_with(self, responses, **props):
        return run_check(self.check_class(), self.fixture.path, props,
                         github=FakeGitHub(responses))


class ExportRepoConfigTest(CheckTestCase):
    """The only criterion here that reads the checkout, not the API."""

    check_class = github_config.ExportRepoConfig

    def test_missing_workflow_fails(self):
        self.assert_fail(self.check(), containing='export-repo-config.yml')

    def test_present_workflow_passes(self):
        self.fixture.workflow('export-repo-config.yml', 'on: schedule\n')
        self.assert_pass(self.check())

    def test_a_docs_only_repository_is_still_expected_to_export(self):
        """No exemption here, deliberately.

        Every repository in the matrix has settings worth diffing,
        including the ones with no code: branch protection and the
        security features are the point, not the language.
        """
        self.assert_fail(self.check(is_docs_only=True))


class DefaultBranchNamingTest(GithubConfigTestCase):
    check_class = github_config.DefaultBranchNaming

    def test_develop_passes(self):
        result = self.run_with(
            {self.api('.default_branch'): CompletedCommand(stdout='develop\n')})
        self.assert_pass(result)

    def test_main_fails_and_names_what_it_found(self):
        result = self.run_with(
            {self.api('.default_branch'): CompletedCommand(stdout='main\n')})
        self.assert_fail(result, containing='"main"')

    def test_a_docs_only_repository_is_not_applicable(self):
        result = self.run_with(
            {self.api('.default_branch'): CompletedCommand(stdout='main\n')},
            is_docs_only=True)
        self.assert_skip(result, containing='Docs-only')

    def test_a_documented_exception_is_not_applicable(self):
        """The reason is published, so the check has to quote it."""
        result = self.run_with(
            {self.api('.default_branch'): CompletedCommand(stdout='main\n')},
            default_branch_exception='every consumer pins to @main')
        self.assert_skip(result, containing='every consumer pins to @main')

    def test_an_api_error_fails_rather_than_passing(self):
        """A repository we cannot ask about is not a compliant one."""
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(
                returncode=1, stderr='HTTP 404: Not Found'),
        })
        self.assert_fail(result, containing='Not Found')

    def test_a_timeout_fails(self):
        result = self.run_with({
            self.api('.default_branch'): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        })
        self.assert_fail(result, containing='Error checking default branch')

    def test_gh_not_installed_fails(self):
        result = self.run_with({
            self.api('.default_branch'): FileNotFoundError('gh'),
        })
        self.assert_fail(result, containing='Error checking default branch')


class GithubSecurityTest(GithubConfigTestCase):
    check_class = github_config.GithubSecurity

    SETTINGS_JQ = '{private: .private, security: .security_and_analysis}'

    def settings(self, private=False, secret_scanning='enabled',
                 push_protection='enabled'):
        return CompletedCommand(stdout=json.dumps({
            'private': private,
            'security': {
                'secret_scanning': {'status': secret_scanning},
                'secret_scanning_push_protection': {
                    'status': push_protection},
            },
        }))

    def test_a_public_repository_with_codeql_and_scanning_passes(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        result = self.run_with({self.api(self.SETTINGS_JQ): self.settings()})
        self.assert_pass(result)

    def test_missing_codeql_fails(self):
        result = self.run_with({self.api(self.SETTINGS_JQ): self.settings()})
        self.assert_fail(result, containing='codeql-analysis.yml')

    def test_a_private_repository_does_not_need_codeql(self):
        """CodeQL needs GHAS on a private repository."""
        result = self.run_with(
            {self.api(self.SETTINGS_JQ): self.settings(private=True)})
        self.assert_pass(result)

    def test_visibility_comes_from_the_api_not_the_overrides(self):
        """A stale override would silently skip the CodeQL check."""
        result = self.run_with(
            {self.api(self.SETTINGS_JQ): self.settings(private=False)},
            is_private=True)
        self.assert_fail(result, containing='codeql-analysis.yml')

    def test_a_docs_only_repository_does_not_need_codeql(self):
        result = self.run_with(
            {self.api(self.SETTINGS_JQ): self.settings()},
            is_docs_only=True)
        self.assert_pass(result)

    def test_secret_scanning_disabled_fails(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        result = self.run_with({
            self.api(self.SETTINGS_JQ): self.settings(
                secret_scanning='disabled'),
        })
        self.assert_fail(result, containing='Secret scanning not enabled')

    def test_push_protection_disabled_fails(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        result = self.run_with({
            self.api(self.SETTINGS_JQ): self.settings(
                push_protection='disabled'),
        })
        self.assert_fail(result, containing='push protection not enabled')

    def test_unparseable_response_is_reported(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        result = self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(stdout='not json'),
        })
        self.assert_fail(result, containing='Could not parse')

    def test_a_timeout_is_reported_rather_than_raised(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        result = self.run_with({
            self.api(self.SETTINGS_JQ): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        })
        self.assert_fail(result, containing='Could not query GitHub API')


class DeleteBranchOnMergeTest(GithubConfigTestCase):
    check_class = github_config.DeleteBranchOnMerge

    JQ = '.delete_branch_on_merge'

    def test_enabled_passes(self):
        result = self.run_with(
            {self.api(self.JQ): CompletedCommand(stdout='true\n')})
        self.assert_pass(result)

    def test_disabled_fails(self):
        result = self.run_with(
            {self.api(self.JQ): CompletedCommand(stdout='false\n')})
        self.assert_fail(result)

    def test_an_api_error_fails(self):
        result = self.run_with({
            self.api(self.JQ): CompletedCommand(
                returncode=1, stderr='HTTP 403'),
        })
        self.assert_fail(result)

    def test_a_timeout_fails(self):
        result = self.run_with({
            self.api(self.JQ): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        })
        self.assert_fail(result, containing='Error checking')


class MergeQueueConfigApiTest(GithubConfigTestCase):
    """The API half; evaluate_merge_queue_rules is covered separately."""

    check_class = github_config.MergeQueueConfig

    def ruleset(self, **params):
        return CompletedCommand(stdout=json.dumps(
            [{'type': 'merge_queue', 'parameters': params}]))

    SERIAL = dict(max_entries_to_build=1, min_entries_to_merge=1,
                  max_entries_to_merge=5,
                  min_entries_to_merge_wait_minutes=5)

    def test_a_serial_queue_passes(self):
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): self.ruleset(**self.SERIAL),
        })
        self.assert_pass(result)

    def test_no_merge_queue_is_not_applicable(self):
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): CompletedCommand(stdout='[]'),
        })
        self.assert_skip(result, containing='No merge queue')

    def test_speculative_stacking_fails(self):
        stacking = dict(self.SERIAL, max_entries_to_build=3)
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): self.ruleset(**stacking),
        })
        self.assert_fail(result, containing='max_entries_to_build is 3')

    def test_the_branch_lookup_failing_fails(self):
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(
                returncode=1, stderr='HTTP 404'),
        })
        self.assert_fail(result, containing='Could not query GitHub API')

    def test_the_rules_lookup_failing_fails(self):
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): CompletedCommand(
                returncode=1, stderr='HTTP 403'),
        })
        self.assert_fail(result, containing='branch rules')

    def test_an_unparseable_ruleset_fails(self):
        result = self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): CompletedCommand(stdout='not json'),
        })
        self.assert_fail(result, containing='Could not parse')

    def test_a_timeout_fails(self):
        result = self.run_with({
            self.api('.default_branch'): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        })
        self.assert_fail(result, containing='Error checking merge queue')


if __name__ == '__main__':
    unittest.main()
