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

    CALLER = (
        'jobs:\n'
        '  export-config:\n'
        '    uses: shakenfist/actions/.github/workflows/'
        'export-repo-config.yml@main\n'
    )

    def test_the_template_shape_passes(self):
        self.fixture.workflow('export-repo-config.yml', self.CALLER)
        self.assert_pass(self.check())

    def test_inheriting_secrets_fails(self):
        """The shared workflow reads none, so inheriting only exposes them."""
        self.fixture.workflow(
            'export-repo-config.yml', self.CALLER + '    secrets: inherit\n')
        result = self.check()
        self.assert_fail(result, containing='secrets: inherit')
        self.assertIn('export-repo-config.yml', result['details'])

    def test_a_commented_out_inherit_passes(self):
        self.fixture.workflow(
            'export-repo-config.yml', self.CALLER + '    # secrets: inherit\n')
        self.assert_pass(self.check())

    def test_other_callers_may_inherit(self):
        """smoke-cluster.yml reads real secrets; only this caller is the finding."""
        self.fixture.workflows({
            'export-repo-config.yml': self.CALLER,
            'functional-tests.yml': (
                'jobs:\n'
                '  smoke:\n'
                '    uses: shakenfist/actions/.github/workflows/'
                'smoke-cluster.yml@main\n'
                '    secrets: inherit\n'),
        })
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

    def test_an_exempt_repository_is_not_asked_about(self):
        """The exemptions are decided in applies(), before any call.

        Nothing is scripted, so a call would answer non-zero and raise:
        an exempt repository's verdict must not depend on GitHub.
        """
        self.assert_skip(self.run_with({}, is_docs_only=True),
                         containing='Docs-only')
        self.assert_skip(
            self.run_with({}, default_branch_exception='a reason'),
            containing='Exempt: a reason')

    def test_an_api_error_is_a_tool_failure(self):
        """A repository we cannot ask about is neither compliant nor not."""
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): CompletedCommand(
                returncode=1, stderr='HTTP 404: Not Found'),
        }), containing='Not Found')

    def test_a_timeout_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        }), containing='timed out')

    def test_gh_not_installed_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): FileNotFoundError('gh'),
        }), containing='the default branch')


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

    def test_an_unparseable_response_is_a_tool_failure(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(stdout='not json'),
        }), containing='Could not parse')

    def test_a_response_that_is_not_an_object_is_a_tool_failure(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(stdout='[]'),
        }), containing='expected an object')

    def test_a_timeout_is_a_tool_failure(self):
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        }), containing='timed out')

    def test_an_api_error_is_a_tool_failure_not_a_pass(self):
        """With CodeQL present, a failed query has nothing else to fail on.

        Read as "no settings", it would pass: nothing wrong could be
        found with settings nobody saw.
        """
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(
                returncode=1, stderr='gh: Bad credentials (HTTP 401)'),
        }), containing='HTTP 401')

    def test_null_security_settings_are_a_tool_failure_not_a_pass(self):
        """GitHub returns null to a token without admin access."""
        self.fixture.workflow('codeql-analysis.yml', 'on: push\n')
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(
                stdout=json.dumps({'private': False, 'security': None})),
        }), containing='no security_and_analysis')

    def test_an_unknown_visibility_is_a_tool_failure(self):
        """Read as public or as private, it would decide the CodeQL rule."""
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.SETTINGS_JQ): CompletedCommand(
                stdout=json.dumps({'private': None, 'security': {}})),
        }), containing='private')


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

    def test_an_api_error_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.JQ): CompletedCommand(
                returncode=1, stderr='HTTP 403'),
        }), containing='HTTP 403')

    def test_a_timeout_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.JQ): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        }), containing='timed out')

    def test_a_null_answer_is_a_tool_failure(self):
        """The API omits the field for a token without push access."""
        self.assert_tool_failure(lambda: self.run_with({
            self.api(self.JQ): CompletedCommand(stdout='null\n'),
        }), containing='lack push access')


class ReleaseEnvironmentProtectionTest(GithubConfigTestCase):
    check_class = github_config.ReleaseEnvironmentProtection

    RELEASE = """name: Release
on:
  push:
    tags: ['v*']
jobs:
  build:
    runs-on: [self-hosted, static]
    steps:
      - run: python -m build
  sign-tag:
    needs: build
    environment: release
    steps:
      - run: true
  publish-pypi:
    needs: sign-tag
    environment: release
    steps:
      - run: true
"""

    PROTECTED = {
        'name': 'release',
        'protection_rules': [
            {'type': 'required_reviewers', 'prevent_self_review': False,
             'reviewers': [{'type': 'User', 'reviewer': {'login': 'mikalstill'}}]},
            {'type': 'branch_policy'},
        ],
        'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True},
    }

    TAG_ONLY = {'total_count': 1, 'branch_policies': [{'name': 'v*', 'type': 'tag'}]}

    def setUp(self):
        super().setUp()
        self.fixture.workflow('release.yml', self.RELEASE)

    def environment(self, name='release'):
        return f'api {self.REPO_PATH}/environments/{name}'

    def policies(self, name='release'):
        return f'api {self.REPO_PATH}/environments/{name}/deployment-branch-policies?per_page=100'

    def answers(self, name='release', environment=None, policies=None):
        """The two answers for one environment, defaulting to a protected one."""
        return {
            self.environment(name): CompletedCommand(
                stdout=json.dumps(self.PROTECTED if environment is None else environment)),
            self.policies(name): CompletedCommand(
                stdout=json.dumps(self.TAG_ONLY if policies is None else policies)),
        }

    def protected_except(self, **changes):
        environment = dict(self.PROTECTED)
        environment.update(changes)
        return environment

    def test_a_protected_environment_passes(self):
        result = self.assert_pass(self.run_with(self.answers()))
        self.assertIn('"release"', result['details'])

    def test_each_environment_is_asked_about_once(self):
        github = FakeGitHub(self.answers())
        run_check(self.check_class(), self.fixture.path, github=github)
        self.assertEqual([self.environment(), self.policies()], github.calls)

    def test_no_release_workflow_is_not_applicable(self):
        os.remove(os.path.join(self.fixture.path, '.github', 'workflows', 'release.yml'))
        github = FakeGitHub()
        result = run_check(self.check_class(), self.fixture.path, github=github)
        self.assert_skip(result, containing='release.yml')
        self.assertEqual([], github.calls)

    def test_a_missing_environment_fails(self):
        result = self.run_with({
            self.environment(): CompletedCommand(returncode=1, stderr='gh: Not Found (HTTP 404)'),
        })
        self.assert_fail(result, containing='would create it unprotected')
        self.assertIn('RELEASE-SETUP.md', result['details'])

    def test_no_reviewers_rule_fails(self):
        result = self.run_with(self.answers(environment=self.protected_except(
            protection_rules=[{'type': 'branch_policy'}])))
        self.assert_fail(result, containing='"release" environment has no required reviewer')

    def test_a_reviewers_rule_with_no_reviewers_fails(self):
        result = self.run_with(self.answers(environment=self.protected_except(
            protection_rules=[{'type': 'required_reviewers', 'reviewers': []}])))
        self.assert_fail(result, containing='no required reviewer')

    def test_a_null_deployment_policy_fails(self):
        result = self.run_with(self.answers(environment=self.protected_except(
            deployment_branch_policy=None)))
        self.assert_fail(result, containing='from any branch or tag')

    def test_protected_branches_instead_of_custom_policies_fails(self):
        result = self.run_with(self.answers(environment=self.protected_except(
            deployment_branch_policy={'protected_branches': True, 'custom_branch_policies': False})))
        self.assert_fail(result, containing='protected branches rather than to tags')

    def test_an_empty_policy_list_fails(self):
        result = self.run_with(self.answers(policies={'total_count': 0, 'branch_policies': []}))
        self.assert_fail(result, containing='has none')

    def test_a_branch_policy_beside_a_tag_policy_fails(self):
        result = self.run_with(self.answers(policies={'total_count': 2, 'branch_policies': [
            {'name': 'v*', 'type': 'tag'}, {'name': 'develop', 'type': 'branch'}]}))
        self.assert_fail(result, containing='from branches as well as tags (develop)')

    def test_a_policy_without_a_type_fails(self):
        """A policy that does not say it is a tag is not read as one."""
        result = self.run_with(self.answers(policies={'total_count': 1, 'branch_policies': [
            {'name': 'v*'}]}))
        self.assert_fail(result, containing='branches as well as tags')

    def test_one_of_two_environments_failing_fails_naming_it(self):
        self.fixture.workflow('release.yml', self.RELEASE + """  publish-proxy:
    needs: sign-tag
    environment: proxy
    steps:
      - run: true
""")
        responses = self.answers()
        responses.update(self.answers('proxy', environment=self.protected_except(protection_rules=[])))
        result = self.assert_fail(self.run_with(responses), containing='"proxy" environment')
        self.assertNotIn('"release" environment', result['details'])

    def test_a_mapping_environment_is_read_by_its_name(self):
        self.fixture.workflow('release.yml', """jobs:
  publish:
    environment:
      name: 'release'
      url: https://pypi.org/p/thing
    steps:
      - run: true
  sign:
    environment: {name: "release", url: https://example.com}
    steps:
      - run: true
""")
        self.assert_pass(self.run_with(self.answers()))

    def test_an_expression_environment_fails_without_asking(self):
        self.fixture.workflow('release.yml', """jobs:
  publish:
    environment: ${{ inputs.target }}
    steps:
      - run: true
""")
        github = FakeGitHub()
        result = run_check(self.check_class(), self.fixture.path, github=github)
        self.assert_fail(result, containing='name it literally')
        self.assertEqual([], github.calls)

    def test_a_release_workflow_naming_no_environment_fails(self):
        self.fixture.workflow('release.yml', """jobs:
  build:
    steps:
      - run: true
""")
        self.assert_fail(self.run_with({}), containing='names no environment')

    def test_a_403_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.environment(): CompletedCommand(returncode=1, stderr='gh: Forbidden (HTTP 403)'),
        }), containing='HTTP 403')

    def test_a_timeout_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.environment(): subprocess.TimeoutExpired(cmd='gh', timeout=30),
        }), containing='timed out')

    def test_a_policy_listing_error_is_a_tool_failure(self):
        responses = self.answers()
        responses[self.policies()] = CompletedCommand(returncode=1, stderr='gh: Forbidden (HTTP 403)')
        self.assert_tool_failure(lambda: self.run_with(responses), containing='deployment policies')

    def test_a_cut_policy_page_is_a_tool_failure(self):
        responses = self.answers(policies={'total_count': 101, 'branch_policies': [
            {'name': 'v*', 'type': 'tag'}]})
        self.assert_tool_failure(lambda: self.run_with(responses), containing='101 policies')

    def test_an_unparseable_environment_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.environment(): CompletedCommand(stdout='not json'),
        }), containing='Could not parse')


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

    def test_the_branch_lookup_failing_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): CompletedCommand(
                returncode=1, stderr='HTTP 404'),
        }), containing='the default branch')

    def test_the_rules_lookup_failing_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): CompletedCommand(
                returncode=1, stderr='HTTP 403'),
        }), containing='branch rules')

    def test_an_unparseable_ruleset_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
            self.rules('develop'): CompletedCommand(stdout='not json'),
        }), containing='Could not parse')

    def test_a_ruleset_that_is_not_a_list_of_rules_is_a_tool_failure(self):
        for body in ('{"message": "Not Found"}', '["merge_queue"]'):
            with self.subTest(body=body):
                self.assert_tool_failure(lambda: self.run_with({
                    self.api('.default_branch'): CompletedCommand(stdout='develop\n'),
                    self.rules('develop'): CompletedCommand(stdout=body),
                }), containing='expected a list of rules')

    def test_a_timeout_is_a_tool_failure(self):
        self.assert_tool_failure(lambda: self.run_with({
            self.api('.default_branch'): subprocess.TimeoutExpired(
                cmd='gh', timeout=30),
        }), containing='timed out')


class ScopeCoverageTest(CheckTestCase):
    """The criterion that measures the fleet rather than one repository."""

    check_class = github_config.ScopeCoverage

    LISTING = ('repo list shakenfist --limit '
               f'{github_config.ORG_LISTING_LIMIT} --json name,isPrivate')

    def scope(self, matrix, excluded):
        """Write the two places the check reads scope from."""
        self.fixture.workflow('consistency-audit.yml', (
            'jobs:\n'
            '  audit:\n'
            '    strategy:\n'
            '      matrix:\n'
            '        repo:\n'
            + ''.join(f'          - {name}\n' for name in matrix)
            + '    steps:\n'
            '      - run: true\n'
        ))
        self.fixture.write('docs/audits/README.md', (
            '## In-scope projects\n'
            '\n'
            + ''.join(f'- {name}\n' for name in matrix)
            + '\n'
            'One project is in scope for part of the audit only:\n'
            '\n'
            '### Excluded projects\n'
            '\n'
            'The following projects are **excluded** from these criteria:\n'
            '\n'
            + ''.join(f'* {name}\n' for name in excluded)
            + '\n'
            'The `actions` repository is audited despite being tooling.\n'
        ))

    def listing(self, *names, private=()):
        return CompletedCommand(stdout=json.dumps(
            [{'name': name, 'isPrivate': name in private} for name in names]))

    def resolve(self, name):
        """The FakeGitHub key for resolving one unlisted name."""
        return f'api repos/shakenfist/{name} --jq .full_name'

    def run_with(self, response, name='development', **resolutions):
        """Run the check with a scripted listing and name resolutions."""
        responses = {self.LISTING: response}
        responses.update({self.resolve(unlisted): answer
                          for unlisted, answer in resolutions.items()})
        github = FakeGitHub(responses)
        result = run_check(self.check_class(), self.fixture.path,
                           name=name, github=github)
        return result, github

    def test_only_the_repository_holding_the_lists_is_measured(self):
        # Everywhere else there is nothing to read, and the listing
        # would be paid for an answer nothing could be compared to --
        # which is why applies() decides this rather than run().
        self.scope(['development'], ['old'])
        github = FakeGitHub({})
        result = run_check(self.check_class(), self.fixture.path,
                           name='occystrap', github=github)
        self.assert_skip(result, 'stated in the development repository')
        self.assertEqual(github.calls, [])

    def test_a_reconciled_scope_passes(self):
        self.scope(['development', 'ryll'], ['old-thing'])
        result, _ = self.run_with(
            self.listing('development', 'ryll', 'old-thing'))
        self.assert_pass(result)

    def test_a_repository_in_neither_list_is_reported(self):
        self.scope(['development'], ['old-thing'])
        result, _ = self.run_with(
            self.listing('development', 'old-thing', 'undecided'))
        self.assert_fail(result, 'in neither the audit matrix nor the '
                                 'excluded list')
        self.assertEqual(
            result['missing'],
            ['undecided (in the organisation, decided nowhere)'])

    def test_a_name_that_no_longer_exists_is_reported(self):
        # The other direction of the same reconciliation. An exclusion
        # for a repository that does not exist excludes nothing, but it
        # is evidence the list has never been checked against reality.
        self.scope(['development'], ['gone', 'here'])
        result, _ = self.run_with(
            self.listing('development', 'here', private=('here',)),
            gone=CompletedCommand(
                returncode=1, stderr='gh: Not Found (HTTP 404)'))
        self.assert_fail(result, 'no longer exist')
        self.assertEqual(result['missing'], ['gone (no such repository)'])

    def test_a_renamed_repository_is_named_by_its_new_name(self):
        # The API follows a rename redirect while issue listing and
        # search do not, so a scope still naming the old name is the
        # same trap gh_canonical_repo() exists for -- and the useful
        # finding is what to write in the list, not that something is
        # missing from it.
        self.scope(['development', 'instar'], ['imago'])
        result, _ = self.run_with(
            self.listing('development', 'instar'),
            imago=CompletedCommand(stdout='shakenfist/instar\n'))
        self.assert_fail(result, 'have been renamed')
        self.assertEqual(
            result['missing'],
            ['imago -> shakenfist/instar (renamed)'])

    def test_a_name_the_listing_missed_is_not_called_stale(self):
        # A token that cannot see private repositories produces a
        # listing missing every one of them, which would read as
        # exclusions naming repositories that no longer exist. The fix
        # that finding implies -- deleting the exclusions -- would be
        # actively harmful, so the name is resolved directly and the
        # listing is what gets blamed.
        self.scope(['development'], ['a-private-one'])
        result, _ = self.run_with(
            self.listing('development'),
            **{'a-private-one': CompletedCommand(
                stdout='shakenfist/a-private-one\n')})
        self.assert_fail(result, 'listing, which is therefore incomplete')
        self.assertEqual(
            result['missing'],
            ['a-private-one (exists, but not in the listing)'])

    def test_both_directions_are_reported_together(self):
        self.scope(['development'], ['gone'])
        result, _ = self.run_with(
            self.listing('development', 'undecided'),
            gone=CompletedCommand(
                returncode=1, stderr='gh: Not Found (HTTP 404)'))
        self.assert_fail(result)
        self.assertEqual(len(result['missing']), 2)

    def test_the_listing_is_not_filtered_by_archived(self):
        # isArchived is the obvious filter and is deliberately not
        # asked for: a repository dormant for years that nobody
        # archived is exactly the case this criterion exists to name,
        # and it is indistinguishable from an active one here.
        self.scope(['development'], ['old-thing'])
        _, github = self.run_with(
            self.listing('development', 'old-thing'))
        self.assertEqual(github.calls, [self.LISTING])

    def test_a_listing_at_the_limit_is_refused(self):
        # gh repo list returns 30 by default, which loses eight of this
        # organisation's repositories and reports them as undecided. A
        # listing that reaches whatever limit was asked for may have
        # been cut, and a cut listing accuses repositories that are
        # merely unlisted.
        self.scope(['development'], ['old-thing'])
        names = [f'repo-{n}' for n in range(github_config.ORG_LISTING_LIMIT)]
        self.assert_tool_failure(lambda: self.run_with(self.listing(*names)),
                                 'may have been truncated')

    def test_a_failed_listing_is_a_tool_failure(self):
        self.scope(['development'], ['old-thing'])
        self.assert_tool_failure(lambda: self.run_with(
            CompletedCommand(returncode=1, stderr='gh: HTTP 401')),
            'the shakenfist organisation listing: gh: HTTP 401')

    def test_an_unparseable_listing_is_a_tool_failure(self):
        self.scope(['development'], ['old-thing'])
        self.assert_tool_failure(
            lambda: self.run_with(CompletedCommand(stdout='not json')),
            'Could not parse')

    def test_a_failure_that_is_not_a_404_is_not_called_a_deletion(self):
        # GitHub answers 404 for a private repository the token cannot
        # see, so the status is the only thing separating "gone" from
        # "cannot look". Everything else -- an expired token, rate
        # limiting, a network blip -- is GitHub not answering, and must
        # not arrive at a finding at all, least of all one whose
        # suggested fix is to delete the entry.
        self.scope(['development'], ['maybe-here'])
        self.assert_tool_failure(lambda: self.run_with(
            self.listing('development'),
            **{'maybe-here': CompletedCommand(
                returncode=1, stderr='gh: Bad credentials (HTTP 401)')}),
            'shakenfist/maybe-here: HTTP 401')

    def test_a_404_without_private_repositories_in_sight_is_not_a_deletion(
            self):
        # A token blind to private repositories 404s on every one of
        # them, one name at a time, exactly as a deletion does, so in
        # that state a 404 carries no information at all. The finding
        # has to name the token: it is the only edit that can clear it,
        # and a red row nobody can act on is one people learn to skip.
        self.scope(['development'], ['a-private-one'])
        result, _ = self.run_with(
            self.listing('development'),
            **{'a-private-one': CompletedCommand(
                returncode=1, stderr='gh: Not Found (HTTP 404)')})
        self.assert_fail(result, 'could not be resolved either way')
        self.assert_fail(result, 'grant it private-repository read')
        self.assertNotIn('no such repository', str(result['missing']))

    def test_a_404_beside_a_visible_private_repository_is_not_qualified(self):
        self.scope(['development'], ['gone'])
        result, _ = self.run_with(
            self.listing('development', 'hidden', private=('hidden',)),
            gone=CompletedCommand(returncode=1,
                                  stderr='gh: Not Found (HTTP 404)'))
        self.assert_fail(result, 'no longer exist')
        self.assertNotIn('check the token', result['details'])

    def test_a_resolution_that_answers_nothing_is_not_a_rename(self):
        # Exit zero and empty stdout would compare unequal to the name
        # asked about, and report a rename to nowhere.
        self.scope(['development'], ['quiet'])
        self.assert_tool_failure(lambda: self.run_with(
            self.listing('development'),
            quiet=CompletedCommand(stdout='\n')),
            'the API returned no name')

    def test_a_rename_is_reported_once_not_twice(self):
        # The new name is also in the organisation and in neither list.
        # Both findings ask for the same single edit, so only the one
        # that names the edit is reported.
        self.scope(['development'], ['imago'])
        result, _ = self.run_with(
            self.listing('development', 'instar', private=('instar',)),
            imago=CompletedCommand(stdout='shakenfist/instar\n'))
        self.assert_fail(result, 'have been renamed')
        self.assertEqual(result['missing'],
                         ['imago -> shakenfist/instar (renamed)'])

    def test_a_move_out_of_the_organisation_suppresses_nothing(self):
        # The suppression above is owner-blind if it takes the basename
        # alone. A repository transferred out of the organisation
        # redirects to its new owner indefinitely, so an unrelated
        # repository still here under that same name -- and in neither
        # list -- had its genuine finding silenced, which is the third
        # state this criterion exists to remove.
        self.scope(['development'], ['departed'])
        result, _ = self.run_with(
            self.listing('development', 'target'),
            departed=CompletedCommand(stdout='otherowner/target\n'))
        self.assert_fail(result, 'in neither the audit matrix nor the '
                                 'excluded list')
        self.assertIn('target (in the organisation, decided nowhere)',
                      result['missing'])

    def test_a_move_out_of_the_organisation_says_to_remove_the_entry(self):
        # A rename inside the organisation and a transfer out of it are
        # the same API answer and want opposite edits. Telling a reader
        # to write otherowner/target into the matrix is advice they
        # cannot take: consistency-audit.yml clones
        # shakenfist/${{ matrix.repo }}, so a foreign owner does not fit
        # there at all. The entry goes instead.
        self.scope(['development'], ['departed'])
        result, _ = self.run_with(
            self.listing('development'),
            departed=CompletedCommand(stdout='otherowner/departed\n'))
        self.assert_fail(result, 'have moved out of the shakenfist '
                                 'organisation')
        self.assertEqual(
            result['missing'],
            ['departed -> otherowner/departed (moved out of shakenfist: '
             'remove the entry)'])

    def test_a_blank_stderr_is_described_rather_than_raised_on(self):
        # describe_failure() indexed the first line of a stderr that
        # had one only if it was not whitespace. An IndexError would be
        # an `error` too, but one blaming a bug rather than naming the
        # failure.
        self.scope(['development'], ['odd', 'hidden'])
        self.assert_tool_failure(lambda: self.run_with(
            self.listing('development', 'hidden', private=('hidden',)),
            odd=CompletedCommand(returncode=1, stderr='   \n')),
            'shakenfist/odd: no error reported')

    def test_an_empty_listing_is_a_tool_failure(self):
        self.scope(['development'], ['old-thing'])
        self.assert_tool_failure(lambda: self.run_with(self.listing()),
                                 'returned no repositories at all')

    def test_the_truncation_guard_counts_entries_not_unique_names(self):
        # A duplicated name would leave the deduplicated set one short
        # of the limit, and a genuinely truncated listing would sail
        # through the guard that exists to catch it.
        self.scope(['development'], ['old-thing'])
        names = [f'repo-{n}'
                 for n in range(github_config.ORG_LISTING_LIMIT - 1)]
        self.assert_tool_failure(
            lambda: self.run_with(self.listing(*(names + [names[0]]))),
            'may have been truncated')

    def test_a_listing_of_the_wrong_shape_is_a_tool_failure(self):
        # Valid JSON that is not a listing would otherwise raise a
        # TypeError or KeyError deeper in, which is `error` too but
        # reads as a bug in the audit rather than GitHub answering
        # something unexpected.
        self.scope(['development'], ['old-thing'])
        for body in ['null', '{"repositories": []}', '[{"nom": "x"}]']:
            with self.subTest(body=body):
                self.assert_tool_failure(
                    lambda: self.run_with(CompletedCommand(stdout=body)),
                    'expected a list of repositories')

    def test_a_matrix_that_cannot_be_parsed_fails_the_check(self):
        # The README rewording route is covered above; this is the same
        # guard reached through the workflow, which is parsed by a
        # different function.
        self.scope(['development'], ['old-thing'])
        self.fixture.workflow('consistency-audit.yml', 'jobs: {}\n')
        result, _ = self.run_with(self.listing('development', 'old-thing'))
        self.assert_fail(result, 'Could not read the audit scope')

    def test_a_timeout_on_one_name_is_a_tool_failure(self):
        # Even beside a real undecided finding. The error leaves an
        # issue already filed for it open, so an outage costs a new
        # finding a day rather than filing one made of unresolved names.
        self.scope(['development'], ['gone', 'hidden'])
        self.assert_tool_failure(lambda: self.run_with(
            self.listing('development', 'hidden', 'undecided',
                         private=('hidden',)),
            gone=subprocess.TimeoutExpired('gh', 30)),
            'shakenfist/gone: timed out')

    def test_gh_missing_on_one_name_is_a_tool_failure(self):
        self.scope(['development'], ['gone', 'hidden'])
        self.assert_tool_failure(lambda: self.run_with(
            self.listing('development', 'hidden', private=('hidden',)),
            gone=FileNotFoundError('gh')),
            'shakenfist/gone')

    def test_a_timeout_listing_is_a_tool_failure(self):
        github = FakeGitHub({
            self.LISTING: subprocess.TimeoutExpired('gh', 60)})
        self.scope(['development'], ['old-thing'])
        self.assert_tool_failure(
            lambda: run_check(self.check_class(), self.fixture.path,
                              name='development', github=github),
            'the shakenfist organisation listing: timed out')

    def test_a_reworded_anchor_fails_naming_the_phrase(self):
        # The parse is anchored to phrases in documents that get
        # rewritten for reasons that have nothing to do with this
        # check. A reworded anchor has to fail as a reworded anchor
        # rather than as a scope that suddenly excludes nothing.
        self.scope(['development'], ['old-thing'])
        self.fixture.write('docs/audits/README.md', 'No lists here.\n')
        result, _ = self.run_with(self.listing('development', 'old-thing'))
        self.assert_fail(result, 'Could not read the audit scope')
        self.assert_fail(result, 'are **excluded**')

    def test_a_missing_workflow_fails_rather_than_raising(self):
        result, _ = self.run_with(self.listing('development'))
        self.assert_fail(result, 'Could not read the audit scope')

    def test_an_undecodable_byte_does_not_abort_the_audit(self):
        # UnicodeDecodeError is a ValueError rather than an OSError, so
        # it escapes the handler around the scope parse, and
        # registry.run_check() would report the check as an error: one
        # bad byte in a scope document would fail the development leg
        # on a traceback. The byte is replaced instead, so the parse
        # reaches its own guard and the check returns a finding a reader
        # can act on.
        self.scope(['development'], ['old-thing'])
        path = os.path.join(self.fixture.path, 'docs', 'audits', 'README.md')
        with open(path, 'rb') as f:
            document = f.read()
        with open(path, 'wb') as f:
            f.write(document.replace(b'* old-thing', b'* old-\xffthing'))
        result, _ = self.run_with(self.listing('development', 'old-thing'))
        self.assert_fail(result, 'Could not read the audit scope')

    def test_an_undecodable_byte_in_prose_still_reports_normally(self):
        # The other half of what replacing the byte buys, and the more
        # valuable one: the guard above only proves the run is not lost,
        # while this proves it is not degraded either. U+FFFD outside a
        # bullet reaches no repository name, so the scope parses and the
        # check answers as it would have. A future guard that scanned
        # the whole block rather than the bullets would turn every such
        # run into a spurious fail, and only this test would object.
        self.scope(['development'], ['old-thing'])
        path = os.path.join(self.fixture.path, 'docs', 'audits', 'README.md')
        with open(path, 'rb') as f:
            document = f.read()
        prose = b'The `actions` repository is audited despite being tooling.'
        self.assertIn(prose, document)
        with open(path, 'wb') as f:
            f.write(document.replace(prose, prose.replace(b'tooling', b'too\xffling')))
        result, _ = self.run_with(self.listing('development', 'old-thing'))
        self.assert_pass(result)

    def test_an_owner_that_shares_the_prefix_is_still_a_move_out(self):
        # The organisation test is a prefix match, so the trailing '/'
        # is what separates 'shakenfist/x' from 'shakenfist-archive/x'.
        # Without it an archive organisation reads as a rename inside
        # shakenfist, which both offers an edit the matrix cannot take
        # and lets the basename back into the undecided suppression.
        self.scope(['development'], ['departed'])
        result, _ = self.run_with(
            self.listing('development'),
            departed=CompletedCommand(
                stdout='shakenfist-archive/departed\n'))
        self.assert_fail(result, 'have moved out of the shakenfist '
                                 'organisation')
        self.assertEqual(
            result['missing'],
            ['departed -> shakenfist-archive/departed (moved out of '
             'shakenfist: remove the entry)'])


if __name__ == '__main__':
    unittest.main()
