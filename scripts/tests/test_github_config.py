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
            ['undecided: in the shakenfist organisation, but in neither '
             'the audit matrix nor the excluded list'])

    def test_a_name_that_no_longer_exists_is_reported(self):
        # The other direction of the same reconciliation. An exclusion
        # for a repository that does not exist excludes nothing, but it
        # is evidence the list has never been checked against reality.
        self.scope(['development'], ['gone', 'here'])
        result, _ = self.run_with(
            self.listing('development', 'here'),
            gone=CompletedCommand(returncode=1, stderr='gh: Not Found'))
        self.assert_fail(result, 'no longer exist')
        self.assertEqual(
            result['missing'],
            ['gone: named by the audit scope, but there is no '
             'shakenfist/gone'])

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
            ['imago: renamed to shakenfist/instar, which the audit scope '
             'does not name'])

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
            ['a-private-one: exists, but the shakenfist listing did not '
             'return it'])

    def test_both_directions_are_reported_together(self):
        self.scope(['development'], ['gone'])
        result, _ = self.run_with(
            self.listing('development', 'undecided'),
            gone=CompletedCommand(returncode=1, stderr='gh: Not Found'))
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
        result, _ = self.run_with(self.listing(*names))
        self.assert_fail(result, 'may have been truncated')

    def test_a_failed_listing_is_reported_as_such(self):
        self.scope(['development'], ['old-thing'])
        result, _ = self.run_with(
            CompletedCommand(returncode=1, stderr='gh: HTTP 401'))
        self.assert_fail(result, 'Could not list the shakenfist '
                                 'organisation')

    def test_an_unparseable_listing_is_reported_as_such(self):
        self.scope(['development'], ['old-thing'])
        result, _ = self.run_with(CompletedCommand(stdout='not json'))
        self.assert_fail(result, 'Could not parse')

    def test_a_timeout_resolving_a_name_is_reported(self):
        self.scope(['development'], ['gone'])
        result, _ = self.run_with(
            self.listing('development'),
            gone=subprocess.TimeoutExpired('gh', 30))
        self.assert_fail(result, 'Could not resolve 1 name(s)')

    def test_a_timeout_is_reported_rather_than_raised(self):
        github = FakeGitHub({
            self.LISTING: subprocess.TimeoutExpired('gh', 60)})
        self.scope(['development'], ['old-thing'])
        result = run_check(self.check_class(), self.fixture.path,
                           name='development', github=github)
        self.assert_fail(result, 'Could not list the shakenfist '
                                 'organisation')

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
